"""`current_user` — the FastAPI dependency every Neng service uses to turn a request into a
verified user id (the Supabase `sub`).

Verification is asymmetric (D4): the service holds no secret, only Supabase's public keys,
fetched from the project JWKS endpoint and cached in-process by PyJWKClient. A token is accepted
only if its signature verifies against a current JWKS key AND its iss/aud/exp all check out.
Anything else is a 401.

Fail closed, always. If the JWKS is not cached and the endpoint is unreachable, we reject —
we never fall back to accepting an unverified token. The one deliberate escape hatch is the
offline bypass (`NENG_AUTH_DISABLED` / unset JWKS URL), which injects a fixed `dev-user` so the
test suite and the chat eval run with no network; `assert_safe_boot` (settings.py) makes that
bypass fatal in prod.
"""

from __future__ import annotations

import threading

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

from .settings import DEV_USER, AuthSettings
from .settings import load as load_settings
from .timing import phase

# One PyJWKClient per JWKS URL, reused across requests so its key cache actually persists (a
# fresh client per request would re-fetch the JWKS every time and defeat the caching that keeps
# verification off the network hot path). Guarded by a lock — FastAPI serves requests on a
# threadpool, and PyJWKClient construction is not guaranteed re-entrant.
_clients: dict[str, PyJWKClient] = {}
_clients_lock = threading.Lock()


class _TimedJWKClient(PyJWKClient):
    def fetch_data(self):
        with phase("auth_fetch"):
            return super().fetch_data()


def _jwks_client(url: str) -> PyJWKClient:
    client = _clients.get(url)
    if client is None:
        with _clients_lock:
            client = _clients.get(url)
            if client is None:
                client = _TimedJWKClient(url)
                _clients[url] = client
    return client


def reset_jwks_cache() -> None:
    """Drop the cached JWKS clients. For tests that swap key material between cases; not used
    on the request path."""
    with _clients_lock:
        _clients.clear()


def _bearer(authorization: str) -> str:
    """Pull the raw token out of an `Authorization: Bearer <token>` header, or "" if absent/malformed."""
    prefix = "bearer "
    if authorization[: len(prefix)].lower() == prefix:
        return authorization[len(prefix):].strip()
    return ""


def verify_token(token: str, settings: AuthSettings) -> str:
    """Verify a Supabase JWT and return its `sub`. Raises HTTPException(401) on any failure.

    Kept separate from the FastAPI dependency so it can be unit-tested directly and reused by a
    non-FastAPI caller. The list of required claims is explicit: a token missing exp/iss/aud/sub
    is rejected rather than silently treated as unbounded.
    """
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    try:
        with phase("auth_lookup"):
            signing_key = _jwks_client(settings.jwks_url).get_signing_key_from_jwt(token)
        with phase("auth_verify"):
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(settings.algorithms),
                audience=settings.audience,
                issuer=settings.issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
    except HTTPException:
        raise
    except Exception as e:
        # Any verification failure — bad signature, expired, wrong aud/iss, tampered, unreachable
        # JWKS (PyJWKClientError), missing required claim — collapses to one 401. We deliberately
        # do NOT leak which check failed to the caller; the class name goes to logs only.
        raise HTTPException(status_code=401, detail="Invalid or expired token") from e

    sub = claims.get("sub")
    if not sub or not isinstance(sub, str):
        raise HTTPException(status_code=401, detail="Token has no subject")
    return sub


def current_user(authorization: str = Header(default="")) -> str:
    """FastAPI dependency: the verified Supabase `sub` for this request, or 401.

    Wire it as `user_id: str = Depends(current_user)` on every protected route. When the offline
    bypass is active it returns `DEV_USER` without touching the header, so local dev / tests /
    eval need no token — exactly the ergonomic the interim bearer gate had.
    """
    with phase("auth_total"):
        settings = load_settings()
        if settings.disabled:
            return DEV_USER
        return verify_token(_bearer(authorization), settings)
