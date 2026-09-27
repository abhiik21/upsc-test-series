"""Initialize the UPSC Test Series runtime schema in PostgreSQL.

Usage:
  DB_BACKEND=postgres DATABASE_URL=postgresql://... python scripts/init_postgres.py
"""

import os

if os.getenv("DB_BACKEND", "postgres").lower() != "postgres":
    raise SystemExit("Set DB_BACKEND=postgres before running this script.")
if not os.getenv("DATABASE_URL"):
    raise SystemExit("DATABASE_URL is required.")

import app
app.init_db()
print("PostgreSQL schema initialized successfully.")
