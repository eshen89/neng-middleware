# neng-middleware agent guide

## Ownership and package release

This repository is a shared package, not a deployed service. Own Supabase JWT verification, hosted startup guards, bounded IAM Lambda HTTP transport, LWA response parsing, resource cleanup and controlled Postgres schema selection.

Never enable SDK automatic retries for business invocations. Bound the full serialized request, close SDK streams on early cancellation, and surface incomplete/error completion. SSE must not claim terminal success before invocation completion. Keep issuer/audience/expiry/signature validation identical across consumers.

After `make test lint`, run `python -m build` and verify the installed wheel with the `lambda` extra in a clean environment. Publish an immutable checked version/tag before updating consumer pins; do not rewrite existing tags. The currently consumed runtime release is v0.2.0. Documentation-only changes do not require a new package release.

## Local verification

Use Python 3.11 and this repository's own `.venv`; recreate an environment with a stale interpreter path. Install dependencies using `make install` in that environment, then `make test` and `make lint`. Use `pytest tests`, avoiding tests copied into CDK assets.

Postgres tests use a throwaway instance via `NENG_TEST_POSTGRES_URL`. A skip is not a passing Postgres or write-gate check; CI must execute required database checks. Never point fixture tests at production data.
## Shared invariants

- Neng prepares drafts only. Never send a DocuSign envelope or email; envelopes use `status="created"`.
- Recipient addresses come from the verified user's deal party records, never model guesses.
- All data access is tenant-scoped by a verified end-user JWT. Hosted dev/prod cannot use auth bypass or a default user.
- Write tools propose on turn N and execute only after confirmation on turn N+1. Keep capability, lease, replay, and concurrent-turn checks.
- Use English when interacting with the Neng application during verification.
- Preserve unrelated local changes. Do not read or print `.env`, credential files, JWTs, or secret values.
- Read README.md, Makefile (when present), and applicable workspace instructions before editing. Old scaffold sections in README are not evidence of current implementation.

## Deployment and evidence

`develop` targets dev; `main` targets prod. Release deployment must depend on successful checks for the same source revision. An explicit user request for prod authorizes a prod release; do not treat an old disabled flag as a permanent prohibition. Respect any actual approval rejection and explain its concrete scope.

Use `--profile demiurge` for authorized local AWS operations; verify account and Region before mutations. GitHub uses environment-specific OIDC roles. Keep IAM targets on same-environment `live` aliases. Production schema is `public`; dev schema is `neng_dev` with restricted runtime roles and separate migration credentials. Migrate with direct/session connections; use transaction-local schema selection under pooling.

CDK synth is not deployment evidence. Inspect diff before deploy; record the tested revision, CI run, stack status, alias versions and health. Validate signed-in reads and the fallback before cutover. Keep rollback URLs and prior versions until the new path is proven; the final gateway topology exposes only Gateway. Never automatically rerun a failed chat or uncertain external write.

Trello is workflow priority, repository code is implementation truth. Do not move cards or comment without an explicit request. Gateway rollout/evidence lives in the sibling `neng-gateway/docs/`; report verified facts separately from pending checks.
