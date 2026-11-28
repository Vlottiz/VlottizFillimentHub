"""SQLite storage. Everything lives in one file you can copy to back up."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS printers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS filaments (
    id INTEGER PRIMARY KEY,
    brand TEXT, name TEXT NOT NULL, material TEXT NOT NULL,
    colour_name TEXT, colour_hex TEXT,
    diameter REAL DEFAULT 1.75, weight_g REAL, price REAL, currency TEXT,
    purchased_on TEXT, store TEXT, url TEXT,
    spools INTEGER DEFAULT 1, spools_left INTEGER,
    notes TEXT, spec TEXT NOT NULL DEFAULT '{}',
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY,
    filament_id INTEGER REFERENCES filaments(id) ON DELETE SET NULL,
    printer_id INTEGER REFERENCES printers(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    material TEXT,
    inherits TEXT,
    settings TEXT NOT NULL DEFAULT '{}',
    sources TEXT NOT NULL DEFAULT '{}',
    notes TEXT NOT NULL DEFAULT '[]',
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS profile_versions (
    id INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    settings TEXT NOT NULL, sources TEXT NOT NULL,
    reason TEXT, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS calibrations (
    id INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    test TEXT NOT NULL, inputs TEXT NOT NULL DEFAULT '{}',
    setting TEXT, old_value TEXT, new_value TEXT,
    applied INTEGER DEFAULT 0, notes TEXT,
    photos TEXT NOT NULL DEFAULT '[]',
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS exports (
    id INTEGER PRIMARY KEY,
    profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
    preset_name TEXT, content TEXT NOT NULL,
    target TEXT, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS troubleshoots (
    id INTEGER PRIMARY KEY,
    profile_id INTEGER REFERENCES profiles(id) ON DELETE CASCADE,
    trial_profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
    description TEXT,
    photos TEXT NOT NULL DEFAULT '[]',
    diagnosis TEXT NOT NULL DEFAULT '{}',
    changes TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'diagnosed',
    created REAL NOT NULL,
    updated REAL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orca_snapshots (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL, name TEXT, content TEXT NOT NULL,
    created REAL NOT NULL
);
"""

JSON_COLS = {"data", "spec", "settings", "sources", "notes", "inputs", "photos", "diagnosis", "changes", "walls"}


class DB:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns that newer versions need to databases made by older ones."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(profiles)")}
        if "trial_of" not in cols:
            self.conn.execute("ALTER TABLE profiles ADD COLUMN trial_of INTEGER")
        if "walls" not in cols:
            self.conn.execute("ALTER TABLE profiles ADD COLUMN walls TEXT NOT NULL DEFAULT '{}'")
        self.conn.commit()

    # -- generic helpers -------------------------------------------------
    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        out = dict(row)
        for k in JSON_COLS & out.keys():
            # filaments.notes is plain text; profiles.notes is a JSON list
            if out[k] is None:
                continue
            try:
                out[k] = json.loads(out[k])
            except (TypeError, ValueError):
                pass
        return out

    def all(self, sql: str, args=()) -> list[dict]:
        return [self._row(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> dict | None:
        return self._row(self.conn.execute(sql, args).fetchone())

    def insert(self, table: str, values: dict) -> int:
        values = {k: (json.dumps(v) if k in JSON_COLS and not isinstance(v, str) else v)
                  for k, v in values.items()}
        values.setdefault("created", time.time())
        cols = ", ".join(values)
        qs = ", ".join("?" for _ in values)
        cur = self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", list(values.values()))
        self.conn.commit()
        return cur.lastrowid

    def update(self, table: str, row_id: int, values: dict) -> None:
        if not values:
            return
        values = {k: (json.dumps(v) if k in JSON_COLS and not isinstance(v, str) else v)
                  for k, v in values.items()}
        sets = ", ".join(f"{k} = ?" for k in values)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*values.values(), row_id])
        self.conn.commit()

    def delete(self, table: str, row_id: int) -> None:
        self.conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
        self.conn.commit()

    # -- app settings (JSON values) ---------------------------------------
    def get_setting(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value) -> None:
        self.conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))
        self.conn.commit()

    # -- profiles with history -------------------------------------------
    def save_profile_version(self, profile_id: int, reason: str) -> None:
        p = self.one("SELECT settings, sources FROM profiles WHERE id = ?", (profile_id,))
        if p:
            self.insert("profile_versions", {
                "profile_id": profile_id, "settings": p["settings"],
                "sources": p["sources"], "reason": reason,
            })

    def set_profile_settings(self, profile_id: int, settings: dict, sources: dict, reason: str) -> None:
        self.update("profiles", profile_id, {
            "settings": settings, "sources": sources, "updated": time.time(),
        })
        self.save_profile_version(profile_id, reason)
