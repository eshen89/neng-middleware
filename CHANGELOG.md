# Changelog

## 0.3.0

### Added
- Optional, payload-free request and phase timing with validated correlation IDs across JWT
  verification, ASGI responses and IAM Lambda calls. Timings are disabled unless explicitly enabled.

### Changed
- Restore the released 0.2.0 Lambda transport and transaction-local Postgres helpers in the
  main package line. Keep bounded requests, disabled invocation retries and stream cleanup.
- Refuse identity bypass on hosted or gateway startup, including hosted development environments.

No deployment, JWT scope, business-write permission or prepare-only policy changes are introduced.
