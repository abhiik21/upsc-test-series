# v58 — Production hardening

- Added request correlation IDs, request timing and conservative security headers.
- Added liveness and database-backed readiness endpoints.
- Added SQLite/PostgreSQL backup helper and restore guidance.
- Hardened Docker Compose secrets/configuration and API healthcheck.
- Hardened Nginx headers, proxy forwarding and static-asset caching.
- Disabled FastAPI Swagger/ReDoc in production; retained them for development.
- Added v58 regression coverage and verified v57 + v58 tests.
