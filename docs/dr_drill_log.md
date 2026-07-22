# Disaster Recovery Drill Log

## 2026-04-11 — scalacore pg_dump restore drill (pre-launch)

**Operator:** ale (infra hardening session, T-6 to launch)
**Server:** prod `65.108.208.117` (scala-prod, aarch64, Ubuntu 24.04.4, PostgreSQL 16.13)
**Backup file tested:** `/var/backups/pg/scalacore_20260411_1430.dump` (custom format, 510 KB)

### Procedure executed
```
sudo -u postgres psql -c 'CREATE DATABASE scalacore_restore_test OWNER scalaai;'
sudo -u postgres pg_restore --no-owner --no-privileges \
    -d scalacore_restore_test /var/backups/pg/scalacore_20260411_1430.dump
```

### Results — PASS
- `pg_restore` completed with no error output
- Tables in `public` schema after restore: **190**
- `SELECT count(*) FROM users;` → **13** (matches live prod)
- `SELECT count(*) FROM contacts;` → row count matched
- Test DB dropped immediately after verification: `DROP DATABASE scalacore_restore_test;` → OK

### Conclusion
Backup file is restorable end-to-end. The nightly cron `/home/ale/scripts/pg_backup.sh` (03:00 Europe/Rome) produces usable dumps. Retention verified in script (14 days, find -mtime +14 -delete).

### Next drill scheduled
- Before launch: **2026-04-17** (T-1) — full restore on dev `89.167.27.229` to simulate real recovery path
- Post-launch: monthly on first Sunday
