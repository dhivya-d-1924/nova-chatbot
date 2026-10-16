"""Consistent SQLite backups using SQLite's online backup API (safe while the app is running), with verification.
For MySQL use mysqldump --single-transaction (see DEPLOYMENT.md)."""
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def sqlite_path(url):
    if not url.startswith("sqlite:///"):
        raise SystemExit("This command supports SQLite. For MySQL use: mysqldump --single-transaction ... (see DEPLOYMENT.md)")
    return Path(url[len("sqlite:///"):])


def verify(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    if ok != "ok" or "users" not in tables:
        raise SystemExit(f"Backup verification failed for {path}: {ok}")
    return True


def backup(url, destination=None):
    src = sqlite_path(url)
    if not src.exists():
        raise SystemExit(f"Database file not found: {src}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = Path(destination) if destination else src.parent / "backups" / f"{src.stem}-{stamp}.db"
    if dest.is_dir():
        dest = dest / f"{src.stem}-{stamp}.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(str(src))
    target = sqlite3.connect(str(dest))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    verify(dest)
    return dest


def restore(url, source):
    dest = sqlite_path(url)
    src = Path(source)
    verify(src)
    if dest.exists():
        safety = dest.with_suffix(f".before-restore-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.db")
        shutil.copy2(dest, safety)
    for suffix in ("-wal", "-shm"):
        Path(str(dest) + suffix).unlink(missing_ok=True)
    shutil.copy2(src, dest)
    verify(dest)
    return dest
