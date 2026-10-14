# neng-middleware

Shared verification package for the Neng services — **not a deployed service**. Imported by
[`neng-agent`](https://github.com/eshen89/neng-agent) and `neng-user` to give every request a
verified user identity, and to sign internal service-to-service calls.

Two responsibilities, deliberately tiny:

- **`current_user`** — a FastAPI dependency that verifies a Supabase JWT against the project's
  JWKS (asymmetric RS256/ES256; the service holds only public keys), checks `iss`/`aud`/`exp`,
  and returns the `sub`. **Fail-closed**: any failure — bad signature, expired, wrong audience,
  unreachable JWKS with no cache — is a `401`. An offline bypass (`NENG_AUTH_DISABLED`, or an
  unset `SUPABASE_JWKS_URL`) injects a fixed `dev-user` so the test suite and the chat eval run
  with no network; `assert_safe_boot()` makes that bypass a **fatal boot error in prod**.
- **`signed_headers`** — SigV4-signs a request to an IAM-protected (`AuthType AWS_IAM`) Function
  URL, using the caller's ambient AWS credentials (the Lambda execution role in prod). Offline,
  it returns plain headers so a local stub can be called without AWS.

## Use

```python
from fastapi import Depends
from neng_middleware import current_user, assert_safe_boot

assert_safe_boot()  # at startup: refuse to run a prod process with the auth bypass on

@app.get("/api/deals")
def list_deals(user_id: str = Depends(current_user)):
    ...  # user_id is the verified Supabase sub
```

Consumed via a pinned git tag; use an editable path dep for local dev:

```
pip install "neng-middleware[lambda] @ git+https://github.com/eshen89/neng-middleware.git@v0.3.0"
# local dev:  pip install -e ../neng-middleware
```

## Develop

```
make install   # pip install -e ".[dev]"
make test      # pytest — offline, fake JWKS, no network
make lint      # ruff
```

Configuration: see [`.env.example`](.env.example).

## Runtime release 0.3.0

The `lambda` extra provides bounded IAM HTTP transport, confirmed streaming completion and
cleanup on cancellation. Business invocation retries remain disabled. `postgres.configure_schema`
applies a controlled transaction-local schema for transaction pooling. Hosted/gateway startup
rejects authentication bypass in both dev and prod.

`TimingMiddleware` and `phase` add optional request diagnostics under `NENG_TRACE_ENABLED=true`.
Records contain route templates, status, completion and durations, never JWTs, payloads, SQL,
identities or raw URLs. Correlation IDs do not authorize a request. These runtime helpers do not
generate load or claim a browser latency SLO. See [CHANGELOG.md](CHANGELOG.md).
