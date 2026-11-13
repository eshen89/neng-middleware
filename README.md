# neng-middleware

Shared TypeScript library for Neng services on Node.js 24.

- `assertSafeBoot` validates JWT configuration on startup. `currentUser` verifies a Supabase
  bearer token against cached public keys and returns the verified subject. Verification
  requires a signature, issuer, audience, expiry and subject. Offline standalone development
  may use `dev-user`. Hosted, gateway and production processes require JWT verification.
- `signedHeaders` signs IAM Function URL requests with the ambient AWS credentials.
- `neng-middleware/lambda-http` invokes a Lambda with an HTTP v2 event and preserves the
  end-user authorization header. The transport bounds the serialized request, disables SDK
  retries, and requires successful invocation completion when streaming. Use `for await`
  to consume the chunks and call `close()` if discarding a result before reading it.
- `neng-middleware/postgres` sets the search path inside the caller's active transaction and
  returns stable advisory lock IDs as `bigint`. Hosted gateway development requires `neng_dev`.
- `neng-middleware/timing` provides request correlation and payload-free diagnostic timing.
  Enable records with `NENG_TRACE_ENABLED=true`. Pass registered route templates to
  `setRoute`, call `responseHeaders` before sending headers, and call `bodySent` after the
  transport accepts a body chunk. Mark the final chunk only after successful completion.

```typescript
import { assertSafeBoot, currentUser } from "neng-middleware";

assertSafeBoot();

// In the authenticated request handler
const userId = await currentUser(request.headers.authorization);
```

The HTTP adapter maps `AuthenticationError.status` and `message` to the response. Configuration
is listed in [`.env.example`](.env.example).

## Develop

```sh
make install
make lint
make build
pnpm pack
```

CI verifies the packed module from a separate project using both ESM and CommonJS. Consumers should install an immutable release tarball.
Python consumers can use the existing [v0.3.0 tag](https://github.com/eshen89/neng-middleware/tree/v0.3.0).

## Dev Lambda Layer

A successful package check on a push to `develop` publishes `neng-middleware-dev` in
`us-west-2` through CDK. The Actions summary records the version ARN. Pin that ARN in
the consumer stack and externalize `neng-middleware` and `neng-middleware/*` in its
Lambda bundle. The Layer includes the locked production dependencies and both ESM and
CommonJS entry points. Previous Layer versions are retained for consumer rollback.
