"""SQLite persistence: one table of JSON documents, each a pydantic model. Small and easy to wipe.

/forget deletes everything about one student. Demo data lives in its own file and is rebuilt on each /demo reset.
"""

import json
import sqlite3
import threading
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .models import CalEvent, Handoff, Item, Leg, Message, Request, User

T = TypeVar("T", bound=BaseModel)

KINDS: dict[type[BaseModel], str] = {User: "user", Item: "item", CalEvent: "event", Request: "request", Leg: "leg",
                                     Handoff: "handoff", Message: "message"}


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("CREATE TABLE IF NOT EXISTS docs (kind TEXT, id TEXT, data TEXT, PRIMARY KEY (kind, id))")
            self.db.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")
            self.db.commit()

    def put(self, doc: BaseModel) -> None:
        key = getattr(doc, "id", None) or getattr(doc, "uid")
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO docs VALUES (?, ?, ?)",
                            (KINDS[type(doc)], key, doc.model_dump_json()))
            self.db.commit()

    def get(self, cls: type[T], id: str | None) -> T | None:
        if id is None:
            return None
        with self.lock:
            row = self.db.execute("SELECT data FROM docs WHERE kind = ? AND id = ?", (KINDS[cls], id)).fetchone()
        return cls.model_validate_json(row[0]) if row else None

    def all(self, cls: type[T], **where) -> list[T]:
        with self.lock:
            rows = self.db.execute("SELECT data FROM docs WHERE kind = ?", (KINDS[cls],)).fetchall()
        docs = [cls.model_validate_json(r[0]) for r in rows]
        return [d for d in docs if all(getattr(d, k) == v for k, v in where.items())]

    def delete(self, cls: type[BaseModel], id: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM docs WHERE kind = ? AND id = ?", (KINDS[cls], id))
            self.db.commit()

    def setting(self, key: str, default: str | None = None) -> str | None:
        with self.lock:
            row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str | None) -> None:
        with self.lock:
            if value is None:
                self.db.execute("DELETE FROM kv WHERE key = ?", (key,))
            else:
                self.db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, value))
            self.db.commit()

    def forget(self, user_id: str) -> int:
        """Deletes the student, their calendar, items, requests, legs, handoffs and messages. Returns how many."""
        doomed: list[tuple[type[BaseModel], str]] = [(User, user_id)]
        items = {i.id for i in self.all(Item, owner_id=user_id)}
        doomed += [(Item, i) for i in items]
        doomed += [(CalEvent, e.uid) for e in self.all(CalEvent, user_id=user_id)]
        doomed += [(Request, r.id) for r in self.all(Request, borrower_id=user_id)]
        legs = [l for l in self.all(Leg) if user_id in (l.holder_id, l.giver_id) or l.item_id in items]
        doomed += [(Leg, l.id) for l in legs]
        leg_ids = {l.id for l in legs}
        doomed += [(Handoff, h.id) for h in self.all(Handoff) if h.leg_id in leg_ids]
        doomed += [(Message, m.id) for m in self.all(Message, user_id=user_id)]
        for cls, id in doomed:
            self.delete(cls, id)
        return len(doomed)


def dumps(doc: BaseModel) -> dict:
    return json.loads(doc.model_dump_json())
