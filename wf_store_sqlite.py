from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SnapshotRow:
    source: str
    name: str
    fetched_at: int
    status: int
    url: str
    sha256: str
    json_text: str | None
    raw: bytes | None


class SqliteStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._ensure_schema()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def _ensure_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              source TEXT NOT NULL,
              name TEXT NOT NULL,
              fetched_at INTEGER NOT NULL,
              status INTEGER NOT NULL,
              url TEXT NOT NULL,
              sha256 TEXT NOT NULL,
              json_text TEXT,
              raw BLOB
            );
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_snapshots_src_name_time ON snapshots(source, name, fetched_at);"
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_history (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              event TEXT NOT NULL,
              key TEXT NOT NULL,
              notified_at INTEGER NOT NULL
            );
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_event_history_event_time ON event_history(event, notified_at);")
        self._conn.commit()

    def put_snapshot(
        self,
        *,
        source: str,
        name: str,
        status: int,
        url: str,
        sha256: str,
        json_obj: Any | None = None,
        raw: bytes | None = None,
        fetched_at: int | None = None,
    ) -> None:
        ts = int(fetched_at if fetched_at is not None else time.time())
        json_text = None
        if json_obj is not None:
            json_text = json.dumps(json_obj, ensure_ascii=False, separators=(",", ":"))
        self._conn.execute(
            """
            INSERT INTO snapshots(source, name, fetched_at, status, url, sha256, json_text, raw)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (source, name, ts, int(status), url, sha256, json_text, raw),
        )
        self._conn.commit()

    def was_notified(self, *, event: str, key: str, within_seconds: int) -> bool:
        cutoff = int(time.time()) - int(max(0, within_seconds))
        cur = self._conn.execute(
            "SELECT 1 FROM event_history WHERE event=? AND key=? AND notified_at>=? LIMIT 1;",
            (event, key, cutoff),
        )
        return cur.fetchone() is not None

    def mark_notified(self, *, event: str, key: str, notified_at: int | None = None) -> None:
        ts = int(notified_at if notified_at is not None else time.time())
        self._conn.execute(
            "INSERT INTO event_history(event, key, notified_at) VALUES(?, ?, ?);",
            (event, key, ts),
        )
        self._conn.commit()

    def prune(self, *, retention_days: int) -> None:
        cutoff = int(time.time()) - int(max(0, retention_days)) * 86400
        self._conn.execute("DELETE FROM snapshots WHERE fetched_at < ?;", (cutoff,))
        self._conn.execute("DELETE FROM event_history WHERE notified_at < ?;", (cutoff,))
        self._conn.commit()


