from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", BACKEND_DIR / "backups"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
BACKEND = os.getenv("DB_BACKEND", "sqlite").lower()

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

if BACKEND == "sqlite":
    source = Path(os.getenv("DEV_SQLITE_PATH", BACKEND_DIR / "data" / "upsc_dev.db"))
    if not source.exists():
        raise SystemExit(f"SQLite database not found: {source}")
    destination = BACKUP_DIR / f"upsc_{stamp}.sqlite3"
    shutil.copy2(source, destination)
    with sqlite3.connect(destination) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        destination.unlink(missing_ok=True)
        raise SystemExit(f"Backup integrity check failed: {result}")
    print(destination)
else:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("DATABASE_URL is required for PostgreSQL backups")
    destination = BACKUP_DIR / f"upsc_{stamp}.dump"
    subprocess.run(["pg_dump", "--format=custom", "--no-owner", "--file", str(destination), database_url], check=True)
    print(destination)
