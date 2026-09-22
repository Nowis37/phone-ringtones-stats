"""SQLite, one file on the NAS volume. Two tables: devices and events.

A device is a random identifier the app draws on first launch and keeps in its
own storage: no advertising identifier, no account, nothing that follows the
person to another app. Reinstalling draws a new one.

Kept 13 months, then deleted: the limit the CNIL sets for audience measurement
exempt from consent (identifier and data alike).
"""

import json
import sqlite3
import threading
import time
from pathlib import Path

RETENTION_SECONDS = 395 * 24 * 3600  # 13 months
PURGE_EVERY = 24 * 3600

SCHEMA = """
create table if not exists devices (
    id text primary key,
    first_seen integer not null,
    last_seen integer not null,
    app text, os text, model text, lang text, country text,
    is_test integer not null default 0
);
create table if not exists events (
    id integer primary key,
    device text not null references devices(id) on delete cascade,
    name text not null,
    at integer not null,
    props text not null default '{}'
);
create index if not exists events_name_at on events(name, at);
create index if not exists events_device on events(device, at);
create table if not exists dropped (
    name text primary key,
    count integer not null
);
create table if not exists meta (key text primary key, value text);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.execute("pragma journal_mode=wal")
        self.db.execute("pragma foreign_keys=on")
        self.db.executescript(SCHEMA)

    def record(self, device: str, envelope: dict, events: list[tuple[str, int, dict]],
               dropped: list[str], now: int | None = None) -> None:
        now = now or int(time.time())
        with self._lock:
            self.db.execute("begin")
            self.db.execute(
                """insert into devices (id, first_seen, last_seen, app, os, model, lang, country, is_test)
                   values (:id, :now, :now, :app, :os, :model, :lang, :country, :test)
                   on conflict (id) do update set last_seen = :now, app = :app, os = :os, model = :model,
                       lang = :lang, country = coalesce(:country, devices.country), is_test = :test""",
                {"id": device, "now": now, **envelope},
            )
            self.db.executemany(
                "insert into events (device, name, at, props) values (?, ?, ?, ?)",
                [(device, name, at, json.dumps(props, sort_keys=True)) for name, at, props in events],
            )
            for name in dropped:
                self.db.execute(
                    "insert into dropped (name, count) values (?, 1) on conflict (name) do update set count = count + 1",
                    (name[:64],),
                )
            self.db.execute("commit")
        self.purge_if_due(now)

    def forget(self, device: str) -> None:
        with self._lock:
            self.db.execute("delete from devices where id = ?", (device,))

    def purge_if_due(self, now: int) -> None:
        last = self.db.execute("select value from meta where key = 'purged'").fetchone()
        if last and now - int(last[0]) < PURGE_EVERY:
            return
        cutoff = now - RETENTION_SECONDS
        with self._lock:
            self.db.execute("begin")
            self.db.execute("delete from events where at < ?", (cutoff,))
            self.db.execute("delete from devices where last_seen < ?", (cutoff,))
            self.db.execute("insert or replace into meta (key, value) values ('purged', ?)", (str(now),))
            self.db.execute("commit")

    def query(self, sql: str, params: tuple | dict = ()) -> list[tuple]:
        return self.db.execute(sql, params).fetchall()
