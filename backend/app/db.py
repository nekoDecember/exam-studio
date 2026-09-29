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


def delete(kind, id):
    with connect() as c:
        result = c.execute(
            "DELETE FROM entities WHERE kind=? AND id=?", (kind, id)
        )
    return result.rowcount > 0


def _remove_question_from_attempt(attempt, question_ids):
    if not isinstance(attempt, dict) or not isinstance(attempt.get("questions"), list):
        return attempt, False

    questions = [
        question
        for question in attempt["questions"]
        if isinstance(question, dict)
        and isinstance(question.get("id"), str)
        and question.get("id") in question_ids
    ]
    kept_ids = {question["id"] for question in questions}
    cleaned = {**attempt, "questions": questions}
    changed = questions != attempt["questions"]
    for key in ("answers", "results", "flags", "notes"):
        values = attempt.get(key)
        if isinstance(values, dict):
            filtered = {question_id: value for question_id, value in values.items() if question_id in kept_ids}
            if filtered != values:
                cleaned[key] = filtered
                changed = True
    if changed:
        cleaned["updated_at"] = now()
        revision = cleaned.get("revision", 0)
        if isinstance(revision, int):
            cleaned["revision"] = revision + 1
        if isinstance(cleaned.get("index"), int):
            cleaned["index"] = min(cleaned["index"], max(0, len(questions) - 1))
    return cleaned, changed


def delete_questions(ids):
    """Permanently delete questions and all snapshots that contain them."""
    requested_ids = list(dict.fromkeys(ids))
    if not requested_ids:
        return []
    requested_set = set(requested_ids)
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        question_rows = c.execute(
            "SELECT id, payload FROM entities WHERE kind=?", ("questions",)
        ).fetchall()
        selected_by_id = {
            question_id: json.loads(payload)
            for question_id, payload in question_rows
            if question_id in requested_set
        }
        deleted_ids = [
            question_id
            for question_id in requested_ids
            if question_id in selected_by_id
        ]
        if not deleted_ids:
            return []
        selected_questions = [
            (question_id, selected_by_id[question_id])
            for question_id in deleted_ids
        ]
        deleted_set = set(deleted_ids)
        set_ids = {
            question.get("question_set_id")
            for _, question in selected_questions
            if isinstance(question.get("question_set_id"), str)
            and question["question_set_id"]
        }
        c.executemany(
            "DELETE FROM entities WHERE kind=? AND id=?",
            [("questions", question_id) for question_id in deleted_ids],
        )

        history_rows = c.execute(
            "SELECT id, payload FROM entities WHERE kind=?", ("history",)
        ).fetchall()
        for history_id, payload in history_rows:
            if json.loads(payload).get("question_id") in deleted_set:
                c.execute(
                    "DELETE FROM entities WHERE kind=? AND id=?",
                    ("history", history_id),
                )

        question_rows = c.execute(
            "SELECT id FROM entities WHERE kind=?", ("questions",)
        ).fetchall()
        question_ids = {item[0] for item in question_rows}
        attempt_rows = c.execute(
            "SELECT id, payload FROM entities WHERE kind=?", ("attempts",)
        ).fetchall()
        for attempt_id, payload in attempt_rows:
            attempt = json.loads(payload)
            cleaned, changed = _remove_question_from_attempt(attempt, question_ids)
            if changed:
                if attempt.get("questions") and not cleaned["questions"]:
                    c.execute(
                        "DELETE FROM entities WHERE kind=? AND id=?",
                        ("attempts", attempt_id),
                    )
                else:
                    c.execute(
                        "UPDATE entities SET payload=? WHERE kind=? AND id=?",
                        (json.dumps(cleaned, ensure_ascii=False), "attempts", attempt_id),
                    )

        if set_ids:
            remaining_rows = c.execute(
                "SELECT payload FROM entities WHERE kind=?", ("questions",)
            ).fetchall()
            remaining_counts = {}
            for (payload,) in remaining_rows:
                set_id = json.loads(payload).get("question_set_id")
                if isinstance(set_id, str) and set_id in set_ids:
                    remaining_counts[set_id] = remaining_counts.get(set_id, 0) + 1
        else:
            remaining_counts = {}
        for set_id in set_ids:
            set_row = c.execute(
                "SELECT payload FROM entities WHERE kind=? AND id=?", ("sets", set_id)
            ).fetchone()
            if set_row:
                set_item = json.loads(set_row[0])
                remaining = remaining_counts.get(set_id, 0)
                if remaining:
                    set_item["question_count"] = remaining
                    c.execute(
                        "UPDATE entities SET payload=? WHERE kind=? AND id=?",
                        (json.dumps(set_item, ensure_ascii=False), "sets", set_id),
                    )
                else:
                    c.execute("DELETE FROM entities WHERE kind=? AND id=?", ("sets", set_id))
    return deleted_ids


def delete_question(id):
    """Permanently delete a question and snapshots that contain its content."""
    return id in delete_questions([id])


def purge_deleted_questions():
    """Remove questions left behind by the former soft-delete behavior."""
    deleted_ids = [
        item["id"]
        for item in all_items("questions")
        if item.get("status") == "deleted"
    ]
    return sum(delete_question(question_id) for question_id in deleted_ids)


def sanitize_attempt(attempt, connection=None):
    """Remove question snapshots that no longer exist from synced attempts."""
    if connection is not None:
        question_ids = {
            row[0]
            for row in connection.execute(
                "SELECT id FROM entities WHERE kind=?", ("questions",)
            )
        }
        return _remove_question_from_attempt(attempt, question_ids)
    with connect() as c:
        question_ids = {
            row[0]
            for row in c.execute(
                "SELECT id FROM entities WHERE kind=?", ("questions",)
            )
        }
    return _remove_question_from_attempt(attempt, question_ids)
