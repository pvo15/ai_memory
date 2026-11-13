"""
Automatic backups of memory.db: every working day (Mon-Fri) at 10:00 local time.

- Uses SQLite's online backup API, so it is safe while the server is running.
- Writes to /backups (a separate folder on the Mac: ~/Documents/ai-memory/backups),
  so deleting data/ by accident doesn't take the backups with it.
- Catch-up: if the Mac was asleep or Docker was off at 10:00, the backup runs as soon
  as the server is back (same working day, any time after 10:00).
- Keeps the newest BACKUP_KEEP files (default 30 = ~6 weeks of working days).
"""
import os
import sqlite3
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

BACKUP_DIR = os.environ.get("BACKUP_DIR", "/backups")
BACKUP_TZ = ZoneInfo(os.environ.get("BACKUP_TZ", "UTC"))
BACKUP_HOUR = int(os.environ.get("BACKUP_HOUR", "10"))
BACKUP_DAYS = {int(d) for d in os.environ.get("BACKUP_WEEKDAYS", "0,1,2,3,4").split(",")}  # Mon=0
BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "30"))
CHECK_EVERY_S = int(os.environ.get("BACKUP_CHECK_SECONDS", "300"))  # look every 5 min
PREFIX = "memory-"


def path_for(day: datetime) -> str:
    return os.path.join(BACKUP_DIR, f"{PREFIX}{day:%Y-%m-%d}.db")


def due(now: datetime) -> bool:
    """A working day, at/after 10:00, and today's backup doesn't exist yet."""
    return (now.weekday() in BACKUP_DAYS and now.hour >= BACKUP_HOUR
            and not os.path.exists(path_for(now)))


def make_backup(db_path: str, now: datetime) -> str:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    final = path_for(now)
    tmp = final + ".tmp"
    src = sqlite3.connect(db_path, timeout=30)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)  # consistent snapshot, even while other requests write
        dst.execute("PRAGMA journal_mode=DELETE")  # one self-contained file, no -wal/-shm
    finally:
        dst.close()
        src.close()
    os.replace(tmp, final)  # appears only when complete
    prune()
    return final


def prune() -> None:
    files = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith(PREFIX) and f.endswith(".db"))
    for old in files[:-BACKUP_KEEP] if BACKUP_KEEP > 0 else []:
        for suffix in ("", "-wal", "-shm", "-journal"):
            p = os.path.join(BACKUP_DIR, old + suffix)
            if os.path.exists(p):
                os.remove(p)


def start_scheduler(db_path: str) -> None:
    """Background thread (HTTP server only)."""
    if os.environ.get("BACKUP_ENABLED", "1") == "0":
        print("auto-backup: off", flush=True)
        return

    def loop():
        while True:
            try:
                now = datetime.now(BACKUP_TZ)
                if due(now):
                    out = make_backup(db_path, now)
                    print(f"auto-backup: wrote {os.path.basename(out)} "
                          f"({os.path.getsize(out) // 1024} KB) at {now:%H:%M %Z}", flush=True)
            except Exception as e:
                print(f"auto-backup failed (will retry): {e}", flush=True)
            time.sleep(CHECK_EVERY_S)

    threading.Thread(target=loop, name="auto-backup", daemon=True).start()
    days = "Mon-Fri" if BACKUP_DAYS == {0, 1, 2, 3, 4} else ",".join(map(str, sorted(BACKUP_DAYS)))
    print(f"auto-backup: {days} at {BACKUP_HOUR:02d}:00 {BACKUP_TZ.key}, keep {BACKUP_KEEP}, "
          f"to {BACKUP_DIR}", flush=True)
