# UPSC Test Series v53

Deployment and production-readiness scaffold.

## Added
- Docker image for the FastAPI backend.
- Docker Compose stack for PostgreSQL, API and Nginx web serving.
- PostgreSQL initialisation from the prepared schema.
- Nginx `/api/` reverse-proxy configuration.
- Production environment template.
- Provider-neutral payment interface and generic webhook HMAC helper.
- Production security checklist.

## Important
The active application repository in this build still uses SQLite. PostgreSQL is
provided as a prepared deployment target; the database repository migration itself
is the next implementation step. Payment gateway integration also remains provider-specific
and must be wired before enabling real checkout/refunds.
