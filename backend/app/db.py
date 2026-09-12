import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(os.environ.get("DATA_DIR", "./data"))


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return str(uuid.uuid4())


def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DATA / "exam.db", timeout=20)
    c.execute("PRAGMA journal_mode=WAL")
    return c


def migrate():
    with connect() as c:
        c.execute("CREATE TABLE IF NOT EXISTS migrations (version INTEGER PRIMARY KEY)")
        c.execute(
            "CREATE TABLE IF NOT EXISTS entities (kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(kind,id))"
        )
        c.execute("INSERT OR IGNORE INTO migrations VALUES (1)")


def all_items(kind):
    with connect() as c:
        return [
            json.loads(r[0])
            for r in c.execute(
                "SELECT payload FROM entities WHERE kind=? ORDER BY rowid", (kind,)
            )
        ]


def get(kind, id):
    with connect() as c:
        row = c.execute(
            "SELECT payload FROM entities WHERE kind=? AND id=?", (kind, id)
        ).fetchone()
    return json.loads(row[0]) if row else None


def put(kind, item):
    item = {**item, "id": item.get("id") or uid(), "updated_at": now()}
    item.setdefault("created_at", now())
    with connect() as c:
        c.execute(
            "INSERT INTO entities VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload",
            (kind, item["id"], json.dumps(item, ensure_ascii=False)),
        )
    return item


def put_many(kind, items):
    with connect() as c:
        for item in items:
            c.execute(
                "INSERT INTO entities VALUES (?,?,?)",
                (kind, item["id"], json.dumps(item, ensure_ascii=False)),
            )
