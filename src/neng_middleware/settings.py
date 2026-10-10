"""Auth settings, read from the environment — shared by every service that verifies a
Supabase JWT (neng-agent, neng-user).

Read once per call (not a module singleton) so a test can flip an env var with monkeypatch and
see it take effect. That matters because the whole offline story hinges on one switch:
`NENG_AUTH_DISABLED` (or an unconfigured JWKS URL) turns verification off and injects a fixed
dev user, which is how the pytest suite and the chat eval run with no network and no key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# The fixed identity injected when verification is disabled (offline dev / tests / eval). Every
# fixture deal is owned by this id, so the tenancy layer behaves identically to production —
# there is exactly one tenant instead of many. Chosen to be obviously-not-a-real-Supabase-sub.
DEV_USER = "dev-user"


@dataclass(frozen=True)
class AuthSettings:
    # Supabase project JWKS endpoint, e.g. https://<ref>.supabase.co/auth/v1/.well-known/jwks.json
    jwks_url: str
    # Expected `iss` and `aud` on the token. Supabase issues iss=<project>/auth/v1, aud="authenticated".
    issuer: str
    audience: str
    # Signing algorithms we accept. Supabase asymmetric keys are ES256 (ECC) or RS256 (RSA).
    algorithms: tuple[str, ...]
    # Deployment environment. "prod" makes the bypass a fatal boot error (see assert_safe_boot).
    env: str
    # The master offline switch: True → skip verification, inject DEV_USER.
    disabled: bool


def _truthy(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


def load() -> AuthSettings:
    """Build AuthSettings from the environment on each call.

    Bypass is ON when `NENG_AUTH_DISABLED` is truthy OR the JWKS URL is unset — the second
    clause mirrors neng-agent's old `require_api_secret` ergonomic ("unset ⇒ gate disabled"),
    so a fresh checkout with no auth env configured runs offline out of the box.
    """
    jwks_url = os.getenv("SUPABASE_JWKS_URL", "").strip()
    disabled = _truthy(os.getenv("NENG_AUTH_DISABLED", "")) or not jwks_url
    algos = tuple(
        a.strip() for a in os.getenv("NENG_AUTH_ALGORITHMS", "ES256,RS256").split(",") if a.strip()
    )
    return AuthSettings(
        jwks_url=jwks_url,
        issuer=os.getenv("SUPABASE_JWT_ISSUER", "").strip(),
        audience=os.getenv("SUPABASE_JWT_AUDIENCE", "authenticated").strip(),
        algorithms=algos,
        env=os.getenv("NENG_ENV", "dev").strip().lower(),
        disabled=disabled,
    )


def assert_safe_boot(settings: AuthSettings | None = None) -> None:
    """Refuse to start a prod process with the auth bypass enabled.

    The offline bypass is a ship-blocker if it ever reaches production: it would accept every
    request as `dev-user` and collapse all tenants into one. This is the belt-and-suspenders
    guard the plan calls for — a service calls it at startup so a misconfigured deploy crashes
    loudly instead of serving unauthenticated. `NENG_ENV=prod` + bypass ⇒ RuntimeError.
    """
    s = settings or load()
    if s.env == "prod" and s.disabled:
        raise RuntimeError(
            "NENG_AUTH_DISABLED (or an unset SUPABASE_JWKS_URL) in a prod environment: the auth "
            "bypass injects a fixed dev-user and disables tenant isolation. Refusing to start. "
            "Set SUPABASE_JWKS_URL/ISSUER/AUDIENCE and clear NENG_AUTH_DISABLED, or set NENG_ENV!=prod."
        )
