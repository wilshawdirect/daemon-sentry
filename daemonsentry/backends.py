"""Pluggable heartbeat stores: SQLite and atomic JSON-file.

Backends are deliberately dumb key-value stores keyed by (name, host).
All health logic lives in :class:`daemonsentry.core.Sentry` so that adding a
new backend (Redis, Postgres, a REST shim) never re-implements verdict rules.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from abc import ABC, abstractmethod
from dataclasses import asdict

from .models import DaemonRow

_COLS = (
    "name", "host", "cadence_s", "registered_at", "last_beat_at",
    "last_ok_at", "last_error", "last_error_at", "claimed_status", "claimed_at", "retired_at",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daemons (
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    cadence_s REAL NOT NULL,
    registered_at REAL NOT NULL,
    last_beat_at REAL,
    last_ok_at REAL,
    last_error TEXT,
    last_error_at REAL,
    claimed_status TEXT,
    claimed_at REAL,
    retired_at REAL,
    PRIMARY KEY (name, host)
)
"""


class Backend(ABC):
    """Minimal contract a heartbeat store must satisfy."""

    @abstractmethod
    def get(self, name: str, host: str) -> DaemonRow | None:
        """Return the row for (name, host), or None if unregistered."""

    @abstractmethod
    def put(self, row: DaemonRow) -> None:
        """Insert or replace the row keyed by (row.name, row.host)."""

    @abstractmethod
    def all_rows(self) -> list[DaemonRow]:
        """Return every stored row."""

    def close(self) -> None:
        """Release held resources. No-op by default."""


class SQLiteBackend(Backend):
    """Heartbeat store backed by a single SQLite table.

    Suitable for one monitoring host; SQLite serialises concurrent writers.
    Pass ``":memory:"`` for an ephemeral store (used by the self-test).
    """

    def __init__(self, path: str = "daemonsentry.db") -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute(_SCHEMA)
        # a store created before retirement existed gains the column in place
        have = {r[1] for r in self._conn.execute("PRAGMA table_info(daemons)")}
        if "retired_at" not in have:
            self._conn.execute("ALTER TABLE daemons ADD COLUMN retired_at REAL")
        self._conn.commit()

    def get(self, name: str, host: str) -> DaemonRow | None:
        cur = self._conn.execute(
            f"SELECT {', '.join(_COLS)} FROM daemons WHERE name = ? AND host = ?",
            (name, host),
        )
        row = cur.fetchone()
        return DaemonRow(*row) if row else None

    def put(self, row: DaemonRow) -> None:
        placeholders = ", ".join("?" for _ in _COLS)
        self._conn.execute(
            f"INSERT OR REPLACE INTO daemons ({', '.join(_COLS)}) VALUES ({placeholders})",
            tuple(getattr(row, col) for col in _COLS),
        )
        self._conn.commit()

    def all_rows(self) -> list[DaemonRow]:
        cur = self._conn.execute(
            f"SELECT {', '.join(_COLS)} FROM daemons ORDER BY name, host"
        )
        return [DaemonRow(*r) for r in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()


class JSONFileBackend(Backend):
    """Heartbeat store as one human-readable JSON document.

    Every write goes through temp-file + ``os.replace`` so a crash mid-write
    can never leave a truncated store. Single-writer by design.
    """

    def __init__(self, path: str = "daemonsentry.json") -> None:
        self._path = path

    def _load(self) -> list[dict]:
        try:
            with open(self._path, encoding="utf-8") as fh:
                return json.load(fh)["rows"]
        except FileNotFoundError:
            return []

    def _dump(self, rows: list[dict]) -> None:
        directory = os.path.dirname(os.path.abspath(self._path))
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"rows": rows}, fh, indent=2, sort_keys=True)
            os.replace(tmp, self._path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def get(self, name: str, host: str) -> DaemonRow | None:
        for item in self._load():
            if item["name"] == name and item["host"] == host:
                return DaemonRow(**item)
        return None

    def put(self, row: DaemonRow) -> None:
        rows = self._load()
        rows = [r for r in rows if not (r["name"] == row.name and r["host"] == row.host)]
        rows.append(asdict(row))
        rows.sort(key=lambda r: (r["name"], r["host"]))
        self._dump(rows)

    def all_rows(self) -> list[DaemonRow]:
        return [DaemonRow(**item) for item in self._load()]
