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
pip install "neng-middleware @ git+https://github.com/eshen89/neng-middleware.git@v0.1.0"
# local dev:  pip install -e ../neng-middleware
```

## Develop

```
make install   # pip install -e ".[dev]"
make test      # pytest — offline, fake JWKS, no network
make lint      # ruff
```

Configuration: see [`.env.example`](.env.example).

## Version 0.2.0

The `lambda` extra adds IAM HTTP transport with buffered Invoke and LWA response streaming. Serialized requests are bounded, SDK retries are disabled, incomplete/error completion is surfaced, and resources close on completion or early cancellation. JWT verification still happens at each application boundary.

`postgres.configure_schema` sets a controlled transaction-local schema, including under transaction pooling; gateway dev requires `neng_dev`. Migration locks include service/schema. Release the checked version/tag before updating consumers; CI builds and imports the installed wheel with the Lambda extra.
