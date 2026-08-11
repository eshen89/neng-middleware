"""JWT verification tests against a FAKE JWKS — the security core of the whole auth story.

No network: we generate an RSA keypair, publish its public half as a one-key JWKS, and
monkeypatch PyJWKClient's fetch so `current_user` runs its real decode path against keys we
control. That lets us assert the exact acceptances and rejections the plan requires:
valid→sub; expired / wrong-aud / wrong-iss / tampered / missing-claim → 401; JWKS unreachable →
401 (fail closed); and the offline bypass → dev-user with a prod-boot guard.
"""

from __future__ import annotations

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from neng_middleware import auth, settings


def _pem(private_key) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

ISSUER = "https://proj.supabase.co/auth/v1"
AUDIENCE = "authenticated"
JWKS_URL = "https://proj.supabase.co/auth/v1/.well-known/jwks.json"
KID = "test-key-1"


# --- fake key material -------------------------------------------------------------------

_PRIV = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIV_PEM = _pem(_PRIV)


def _jwks() -> dict:
    """The public key as a single-entry JWKS, tagged with KID so lookup-by-kid finds it."""
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_PRIV.public_key()))
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    return {"keys": [jwk]}


def _token(**overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": "user-123",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return jwt.encode(claims, _PRIV_PEM, algorithm="RS256", headers={"kid": KID})


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    """Verification ON, pointed at the fake JWKS. Each test starts with a clean client cache so
    a swapped key set in one test can't be served from another's cache."""
    monkeypatch.setenv("SUPABASE_JWKS_URL", JWKS_URL)
    monkeypatch.setenv("SUPABASE_JWT_ISSUER", ISSUER)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("NENG_AUTH_ALGORITHMS", "RS256")
    monkeypatch.delenv("NENG_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("NENG_ENV", raising=False)
    auth.reset_jwks_cache()
    yield
    auth.reset_jwks_cache()


def _serve_jwks(monkeypatch, data=None):
    """Make every PyJWKClient fetch return our fake JWKS instead of hitting the network."""
    payload = data if data is not None else _jwks()
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: payload)


def _unreachable_jwks(monkeypatch):
    def _boom(self):
        raise jwt.exceptions.PyJWKClientConnectionError("JWKS endpoint unreachable")
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", _boom)


# --- acceptance --------------------------------------------------------------------------


def test_valid_token_returns_sub(monkeypatch):
    _serve_jwks(monkeypatch)
    assert auth.current_user(f"Bearer {_token()}") == "user-123"


def test_bearer_prefix_is_case_insensitive(monkeypatch):
    _serve_jwks(monkeypatch)
    assert auth.current_user(f"bearer {_token(sub='u9')}") == "u9"


# --- rejections (each a distinct 401) ----------------------------------------------------


def _assert_401(callable_):
    with pytest.raises(HTTPException) as ei:
        callable_()
    assert ei.value.status_code == 401


def test_expired_token_rejected(monkeypatch):
    _serve_jwks(monkeypatch)
    _assert_401(lambda: auth.current_user(f"Bearer {_token(exp=int(time.time()) - 10)}"))


def test_wrong_audience_rejected(monkeypatch):
    _serve_jwks(monkeypatch)
    _assert_401(lambda: auth.current_user(f"Bearer {_token(aud='some-other-api')}"))


def test_wrong_issuer_rejected(monkeypatch):
    _serve_jwks(monkeypatch)
    _assert_401(lambda: auth.current_user(f"Bearer {_token(iss='https://evil.example/auth/v1')}"))


def test_tampered_signature_rejected(monkeypatch):
    _serve_jwks(monkeypatch)
    good = _token()
    tampered = good[:-3] + ("abc" if good[-3:] != "abc" else "xyz")
    _assert_401(lambda: auth.current_user(f"Bearer {tampered}"))


def test_token_signed_by_a_foreign_key_rejected(monkeypatch):
    # A token from a different keypair whose kid isn't in the published JWKS → no signing key → 401.
    _serve_jwks(monkeypatch)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = _pem(other)
    now = int(time.time())
    forged = jwt.encode(
        {"sub": "x", "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + 3600},
        other_pem, algorithm="RS256", headers={"kid": "unknown-kid"},
    )
    _assert_401(lambda: auth.current_user(f"Bearer {forged}"))


def test_missing_required_claim_rejected(monkeypatch):
    # A token with no exp must be rejected, not treated as never-expiring.
    _serve_jwks(monkeypatch)
    now = int(time.time())
    no_exp = jwt.encode({"sub": "x", "iss": ISSUER, "aud": AUDIENCE, "iat": now},
                        _PRIV_PEM, algorithm="RS256", headers={"kid": KID})
    _assert_401(lambda: auth.current_user(f"Bearer {no_exp}"))


def test_missing_header_rejected(monkeypatch):
    _serve_jwks(monkeypatch)
    _assert_401(lambda: auth.current_user(""))
    _assert_401(lambda: auth.current_user("Basic abc"))


# --- fail closed -------------------------------------------------------------------------


def test_jwks_uncached_and_unreachable_rejects(monkeypatch):
    # The core fail-closed case: no cached keys AND the endpoint is down ⇒ reject. We must never
    # accept an unverified token as a fallback.
    _unreachable_jwks(monkeypatch)
    _assert_401(lambda: auth.current_user(f"Bearer {_token()}"))


# --- offline bypass + prod guard ---------------------------------------------------------


def test_bypass_injects_dev_user(monkeypatch):
    monkeypatch.setenv("NENG_AUTH_DISABLED", "1")
    # No JWKS served, no token — still resolves, so tests/eval run offline.
    assert auth.current_user("") == settings.DEV_USER


def test_bypass_also_on_when_jwks_url_unset(monkeypatch):
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    assert settings.load().disabled is True
    assert auth.current_user("") == settings.DEV_USER


def test_prod_boot_refuses_bypass(monkeypatch):
    monkeypatch.setenv("NENG_ENV", "prod")
    monkeypatch.setenv("NENG_AUTH_DISABLED", "1")
    with pytest.raises(RuntimeError, match="bypass"):
        settings.assert_safe_boot()


def test_prod_boot_ok_when_verification_configured(monkeypatch):
    monkeypatch.setenv("NENG_ENV", "prod")
    monkeypatch.delenv("NENG_AUTH_DISABLED", raising=False)
    # JWKS url is set by the autouse fixture → not disabled → safe to boot.
    settings.assert_safe_boot()


def test_nonprod_boot_allows_bypass(monkeypatch):
    monkeypatch.setenv("NENG_ENV", "dev")
    monkeypatch.setenv("NENG_AUTH_DISABLED", "1")
    settings.assert_safe_boot()  # dev may run bypassed
