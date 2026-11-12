"""Tiny SQLite store: merchant records, AI analyses, submissions, audit log."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

_DB_PATH = os.path.join(os.getenv("DATA_DIR", "data"), "claimpilot.db")
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    c = sqlite3.connect(_DB_PATH)
    c.execute("CREATE TABLE IF NOT EXISTS kv (ns TEXT, k TEXT, v TEXT, PRIMARY KEY(ns, k))")
    c.execute("CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, "
              "dispute_id TEXT, actor TEXT, action TEXT, detail TEXT)")
    return c


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def put(ns: str, k: str, v) -> None:
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO kv VALUES (?,?,?)", (ns, k, json.dumps(v)))


def get(ns: str, k: str, default=None):
    with _lock, _conn() as c:
        row = c.execute("SELECT v FROM kv WHERE ns=? AND k=?", (ns, k)).fetchone()
    return json.loads(row[0]) if row else default


def audit(dispute_id: str, actor: str, action: str, detail: dict | str = "") -> None:
    if not isinstance(detail, str):
        detail = json.dumps(detail)[:4000]
    with _lock, _conn() as c:
        c.execute("INSERT INTO audit (ts, dispute_id, actor, action, detail) VALUES (?,?,?,?,?)",
                  (now(), dispute_id, actor, action, detail))


def audit_for(dispute_id: str | None = None, limit: int = 100) -> list[dict]:
    with _lock, _conn() as c:
        if dispute_id:
            rows = c.execute("SELECT ts, dispute_id, actor, action, detail FROM audit WHERE dispute_id=? "
                             "ORDER BY id DESC LIMIT ?", (dispute_id, limit)).fetchall()
        else:
            rows = c.execute("SELECT ts, dispute_id, actor, action, detail FROM audit "
                             "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(zip(["ts", "dispute_id", "actor", "action", "detail"], r)) for r in rows]
