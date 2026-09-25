# Database backup and restore

SQLite development:

```bash
python scripts/backup_db.py
```

The backup is a copy of the SQLite database after an integrity check.
Restore by stopping the API and replacing the configured SQLite file with the backup.

PostgreSQL production:

```bash
DB_BACKEND=postgres DATABASE_URL='postgresql://...' python scripts/backup_db.py
pg_restore --clean --if-exists --no-owner -d 'postgresql://...' backups/<file>.dump
```

Use a managed PostgreSQL backup/PITR facility for production and test restores regularly.
