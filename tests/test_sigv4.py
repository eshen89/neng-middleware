"""SigV4 signing helper tests. Offline: static dummy credentials via botocore, no AWS calls."""

from __future__ import annotations

import pytest

from neng_middleware import sigv4


@pytest.fixture(autouse=True)
def _clear_bypass(monkeypatch):
    # sigv4 shares the auth bypass switch; force it OFF here so signing actually runs, and give
    # botocore static creds through the environment (never a real account).
    monkeypatch.setenv("SUPABASE_JWKS_URL", "https://proj.supabase.co/auth/v1/.well-known/jwks.json")
    monkeypatch.delenv("NENG_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("NENG_INTERNAL_BYPASS", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secretexamplekey")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)


def test_signed_headers_carry_sigv4_authorization():
    headers = sigv4.signed_headers(
        "POST", "https://abc123.lambda-url.us-east-1.on.aws/internal/users/upsert",
        body=b'{"user_id":"u1"}',
    )
    assert headers["Authorization"].startswith("AWS4-HMAC-SHA256")
    assert "Credential=AKIDEXAMPLE/" in headers["Authorization"]
    assert "lambda" in headers["Authorization"]  # the signed service
    assert "X-Amz-Date" in headers


def test_bypass_returns_plain_headers(monkeypatch):
    monkeypatch.setenv("NENG_INTERNAL_BYPASS", "1")
    headers = sigv4.signed_headers("GET", "http://localhost:9002/internal/users/u1",
                                   headers={"X-Test": "1"})
    assert "Authorization" not in headers
    assert headers == {"X-Test": "1"}


def test_missing_credentials_raise(monkeypatch):
    # Force the credential chain empty (the host may have a profile configured, so clearing env
    # vars alone isn't enough) — assert we fail loudly rather than send an unsigned request.
    import botocore.session
    monkeypatch.setattr(botocore.session.Session, "get_credentials", lambda self: None)
    with pytest.raises(RuntimeError, match="credentials"):
        sigv4.signed_headers("GET", "https://x.lambda-url.us-east-1.on.aws/internal/x")
