# v54 — PostgreSQL repository migration

- FastAPI repository now supports PostgreSQL through psycopg 3 with SQLite retained for development.
- Added a DB compatibility layer and qmark-to-psycopg parameter translation.
- Added runtime PostgreSQL bootstrap schema matching the active application repository.
- Updated Docker Compose and production environment templates to use PostgreSQL correctly.
- Added a live database health check and PostgreSQL initialization script.
- Verified the complete application flow on the SQLite compatibility backend; PostgreSQL runtime verification is limited by the build environment because no PostgreSQL server/client is available here.
