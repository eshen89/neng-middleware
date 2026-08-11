"""SigV4 signing for internal service-to-service calls (D8).

neng-user's internal endpoints live behind a Function URL with `AuthType AWS_IAM`: AWS itself
rejects any request that is not SigV4-signed by an allowed principal, so there is no shared
secret to leak or rotate. neng-agent calls those endpoints signing with its own Lambda
execution-role credentials.

This module produces the signed headers for such a call. `botocore` is imported lazily so the
dependency is only paid when a service actually makes an internal call — the deal hot path never
touches this. Offline (auth bypass), `signed_headers` returns plain headers so a local/eval run
can call a stub without AWS credentials.
"""

from __future__ import annotations

import os

from .settings import load as load_settings


def _bypass_enabled() -> bool:
    # Same switch as auth: no AWS in tests/eval. Internal calls are stubbed/in-process there.
    return load_settings().disabled or _truthy(os.getenv("NENG_INTERNAL_BYPASS", ""))


def _truthy(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


def signed_headers(
    method: str,
    url: str,
    *,
    body: bytes | str = b"",
    region: str | None = None,
    service: str = "lambda",
    headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return the headers (Authorization + X-Amz-Date + X-Amz-Security-Token…) that authorize a
    SigV4 request to an IAM-protected Function URL.

    Credentials come from the ambient AWS chain (the Lambda execution role in prod; a dev
    profile locally). In bypass mode returns `headers` unchanged — the caller is expected to hit
    a local stub, not a signed URL.
    """
    base = dict(headers or {})
    if _bypass_enabled():
        return base

    import botocore.session
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    region = region or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    session = botocore.session.get_session()
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError(
            "SigV4 signing requested but no AWS credentials are available. In prod the Lambda "
            "execution role supplies these; locally set a profile, or enable NENG_INTERNAL_BYPASS."
        )
    data = body.encode() if isinstance(body, str) else body
    request = AWSRequest(method=method.upper(), url=url, data=data, headers=base)
    SigV4Auth(credentials, service, region).add_auth(request)
    return dict(request.headers)
