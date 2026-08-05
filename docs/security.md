# Security

## Authentication flow

```
POST /auth/register  →  user + organization + org_admin membership  →  token pair
POST /auth/login     →  token pair
POST /auth/refresh   →  new token pair; the presented refresh token is revoked as it is redeemed
POST /auth/logout    →  revokes the refresh token
```

**Access tokens** are HS256 JWTs carrying `sub`, `org`, `role`, `iat`, `exp` and a `jti`, with
a 30-minute lifetime. The role in the token is informational — the server re-reads the
membership on every request, so revoking a role takes effect immediately rather than at the
next token expiry.

**Refresh tokens** are 48 bytes of `secrets.token_urlsafe`, stored only as a SHA-256 hash,
valid 14 days, and **single use**: redemption sets `revoked_at`, so a replayed refresh token
fails. The frontend runs refresh single-flight so concurrent 401s do not each burn a token.

**Passwords** are bcrypt (cost 12) over a base64 SHA-256 pre-hash. bcrypt silently truncates
at 72 bytes; pre-hashing means a long passphrase is not quietly cut down to its first 72
characters. Login returns an identical error for an unknown email and a wrong password.

**API keys** are `lf_` plus 32 random URL-safe bytes. Only the SHA-256 hash is stored; the raw
value is returned exactly once at creation. A key resolves to `org_admin` within its
organization, can be given an expiry, and can be revoked. Keys cannot approve AI evaluations —
that endpoint requires a user principal.

## Authorization

Six roles with a server-side permission map (`models/tenancy.py`):

| Permission | platform_admin | org_admin | instructor | reviewer | support_agent | learner |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `org:manage`, `member:manage` | x | x | | | | |
| `learner:manage` | x | x | x | | | |
| `document:write`, `course:write`, `assessment:write` | x | x | x | | | |
| `course:publish` | x | x | x | | | |
| `evaluation:approve` | x | x | x | x | | |
| `workflow:write` | x | x | | | | |
| `workflow:run` | x | x | x | | | |
| `ticket:manage` | x | x | | | x | |
| `analytics:read` | x | x | x | x | x | |
| `apikey:manage`, `audit:read` | x | x | | | | |
| `rag:query` | x | x | x | x | x | x |

Routes declare what they need: `dependencies=[Depends(require("course:publish"))]`.

## Tenant isolation

Isolation is a property of the data layer, not a convention endpoints follow.

`TenantRepo` is constructed from the request principal and:

- refuses to build a statement against a tenant-scoped model without the organization filter;
- raises `TenantIsolationError` (a bug, never a 404) if handed a non-tenant model;
- stamps `organization_id` on every write, so a caller cannot create a row in another
  organization even by supplying the id;
- returns a **404 identical** to "does not exist" for another tenant's object, so ids cannot
  be probed.

Vector isolation is by Chroma collection per organization, not a metadata filter — a forgotten
filter leaks; a wrong collection name returns nothing.

Three tests hold this down: cross-tenant read returns 404, cross-tenant list returns zero
rows, and a cross-tenant RAG query refuses with no citations.

## Webhook security

```
X-LearnFlow-Signature: t=<unix seconds>,v1=<hex hmac-sha256>
mac = HMAC_SHA256(WEBHOOK_SIGNING_SECRET, f"{t}.{raw_body}")
```

- `hmac.compare_digest`, so there is no timing oracle.
- Timestamps outside plus or minus 300 seconds are rejected.
- The mac is inserted into `webhook_receipts` under a unique constraint, so a replay inside
  the freshness window fails at the database rather than in application logic.
- Signing covers the raw bytes; a re-serialised copy will not verify.
- The inbound callback resolves the execution **before** writing the receipt, because the
  receipt is tenant-scoped and the tenant is only knowable from the execution.

## Input validation

- Uploads are checked on extension, declared content type, size and **magic bytes**. A `.pdf`
  that does not start with `%PDF-` is rejected as hostile, not treated as a typo.
- Encrypted PDFs are rejected rather than silently yielding empty text.
- Every request body is a Pydantic model; unknown fields are ignored, invalid ones produce a
  422 with per-field detail.
- All model output validated against a schema is re-validated on the way back; the gateway
  never hands unvalidated model output to a caller.

## Rate limiting

Fixed-window counter per API key, bearer token or client IP, in Redis, default 120 requests
per minute. Health and docs paths are exempt. If Redis is unavailable the limiter degrades
open and logs — an availability trade-off chosen deliberately, since the alternative is
Redis becoming a single point of failure for the whole API.

`ponytail:` the window is fixed, not sliding, so a burst can straddle the boundary. A token
bucket in Lua is the upgrade if abuse becomes real.

## Secrets and logging

- Every secret comes from the environment. `docker-compose.yml` refuses to start without
  `JWT_SECRET`, `WEBHOOK_SIGNING_SECRET` and `N8N_ENCRYPTION_KEY`.
- `.env` is gitignored; `.env.example` contains only placeholders.
- The structlog pipeline redacts any key containing password, secret, token, api_key,
  authorization, signature or key_hash before a record reaches a sink.
- Error responses never include SQL, constraint names or stack traces; the detail goes to the
  log with a request id the caller can quote.
- CI fails the build if a credential-shaped literal (`sk-...`, `AKIA...`) is committed.

## Audit logging

Privileged actions write an `audit_logs` row with actor type and label, action, resource type
and id, request id, IP address and non-sensitive details: registration, login, member changes,
document upload, re-index and delete, course generate and publish, assessment generate,
submission, evaluation review, workflow create, trigger and retry, API key create and revoke.

## Known gaps

Honest list of what is not implemented:

- No MFA, no SSO/SAML/OIDC, no password reset flow (there is no mail server in the base setup;
  new members get an explicitly-set initial password).
- API key scopes are stored but not yet enforced per route — a key acts as `org_admin`.
- No CSRF tokens: the API is token-authenticated with no cookie auth, so there is no ambient
  authority to forge.
- Rate limiting is per process window in Redis, not a distributed token bucket.
- No field-level encryption at rest; that is delegated to disk or database encryption.
- Uploaded files are not virus scanned.
