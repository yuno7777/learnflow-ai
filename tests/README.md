# Tests

Tests live next to the code they cover:

| Suite | Location | Command |
|---|---|---|
| Backend unit and integration (49) | `apps/api/tests/` | `cd apps/api && pytest -q` |
| Frontend unit (10) | `apps/web/tests/` | `cd apps/web && npm test` |
| End-to-end (Playwright) | `apps/web/e2e/` | `cd apps/web && E2E_RUN=1 npx playwright test` |

See [docs/testing.md](../docs/testing.md) for what each suite covers and how the backend
suite runs without Postgres, Redis, Chroma or an API key.
