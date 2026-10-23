from __future__ import annotations

import os
import sqlite3
from os import PathLike
from pathlib import Path
from typing import Self, cast

from golded_ftn import FtnAddress

from .models import AreaDefinition, AreaKind

APPLICATION_ID = 0x47465453
SCHEMA_VERSION = 1

_SCHEMA = f"""
PRAGMA application_id = {APPLICATION_ID};
PRAGMA user_version = {SCHEMA_VERSION};

CREATE TABLE areas (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('netmail', 'echomail')),
    sort_order INTEGER NOT NULL DEFAULT 0,
    meta_key TEXT,
    origin_zone INTEGER,
    origin_net INTEGER,
    origin_node INTEGER,
    origin_point INTEGER,
    origin_domain TEXT,
    next_message_number INTEGER NOT NULL DEFAULT 1
        CHECK (next_message_number > 0),
    CHECK (
        (origin_zone IS NULL AND origin_net IS NULL AND origin_node IS NULL
            AND origin_point IS NULL AND origin_domain IS NULL)
        OR
        (origin_zone >= 0 AND origin_net >= 0 AND origin_node >= 0
            AND (origin_point IS NULL OR origin_point >= 0))
    )
) STRICT;

CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    area_id INTEGER NOT NULL REFERENCES areas(id),
    msgno INTEGER NOT NULL CHECK (msgno > 0),
    from_name TEXT NOT NULL,
    to_name TEXT NOT NULL,
    subject TEXT NOT NULL,
    body_text TEXT NOT NULL,
    posted_at TEXT,
    external_id TEXT,
    attributes_raw INTEGER NOT NULL,
    from_zone INTEGER,
    from_net INTEGER,
    from_node INTEGER,
    from_point INTEGER,
    from_domain TEXT,
    to_zone INTEGER,
    to_net INTEGER,
    to_node INTEGER,
    to_point INTEGER,
    to_domain TEXT,
    reply_to_msgno INTEGER,
    reply1st_msgno INTEGER,
    reply_next_msgno INTEGER,
    source_type TEXT,
    source_path TEXT,
    source_id TEXT,
    source_offset INTEGER,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    deleted_at TEXT,
    UNIQUE (area_id, msgno),
    CHECK (
        (from_zone IS NULL AND from_net IS NULL AND from_node IS NULL
            AND from_point IS NULL AND from_domain IS NULL)
        OR
        (from_zone >= 0 AND from_net >= 0 AND from_node >= 0
            AND (from_point IS NULL OR from_point >= 0))
    ),
    CHECK (
        (to_zone IS NULL AND to_net IS NULL AND to_node IS NULL
            AND to_point IS NULL AND to_domain IS NULL)
        OR
        (to_zone >= 0 AND to_net >= 0 AND to_node >= 0
            AND (to_point IS NULL OR to_point >= 0))
    ),
    CHECK (
        source_type IS NOT NULL
        OR (source_path IS NULL AND source_id IS NULL AND source_offset IS NULL)
    )
) STRICT;

CREATE INDEX messages_area_posted
    ON messages (area_id, posted_at, msgno)
    WHERE deleted_at IS NULL;

CREATE INDEX messages_external_id
    ON messages (external_id)
    WHERE external_id IS NOT NULL AND deleted_at IS NULL;

CREATE TRIGGER messages_netmail_addresses_insert
BEFORE INSERT ON messages
WHEN (SELECT kind FROM areas WHERE id = NEW.area_id) = 'netmail'
    AND (
        NEW.from_zone IS NULL OR NEW.from_net IS NULL OR NEW.from_node IS NULL
        OR NEW.to_zone IS NULL OR NEW.to_net IS NULL OR NEW.to_node IS NULL
    )
BEGIN
    SELECT RAISE(ABORT, 'netmail requires origin and destination addresses');
END;

CREATE TRIGGER messages_netmail_addresses_update
BEFORE UPDATE OF area_id, from_zone, from_net, from_node,
                 to_zone, to_net, to_node ON messages
WHEN (SELECT kind FROM areas WHERE id = NEW.area_id) = 'netmail'
    AND (
        NEW.from_zone IS NULL OR NEW.from_net IS NULL OR NEW.from_node IS NULL
        OR NEW.to_zone IS NULL OR NEW.to_net IS NULL OR NEW.to_node IS NULL
    )
BEGIN
    SELECT RAISE(ABORT, 'netmail requires origin and destination addresses');
END;

CREATE TABLE control_lines (
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 0),
    name TEXT NOT NULL COLLATE NOCASE,
    value TEXT NOT NULL,
    raw TEXT NOT NULL,
    PRIMARY KEY (message_id, position)
) WITHOUT ROWID, STRICT;

CREATE INDEX control_lines_name
    ON control_lines (name, message_id);

CREATE TABLE routing_entries (
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('seen_by', 'path')),
    position INTEGER NOT NULL CHECK (position >= 0),
    value TEXT NOT NULL,
    PRIMARY KEY (message_id, kind, position)
) WITHOUT ROWID, STRICT;

CREATE TABLE reply_list_entries (
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 0),
    msgno INTEGER NOT NULL CHECK (msgno > 0),
    PRIMARY KEY (message_id, position)
) WITHOUT ROWID, STRICT;
"""


class SqliteDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def open(cls, path: str | PathLike[str]) -> Self:
        database_path = Path(path).resolve()
        connection = connect_database(database_path, read_only=True)
        connection.close()
        return cls(database_path)

    @classmethod
    def create(
        cls,
        path: str | PathLike[str],
        *,
        area: AreaDefinition,
    ) -> Self:
        database_path = Path(path).resolve()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(database_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(descriptor)
        try:
            with sqlite3.connect(database_path) as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = NORMAL")
                connection.executescript(_SCHEMA)
                origin = area.default_origin
                connection.execute(
                    """
                    INSERT INTO areas (
                        code, name, kind, sort_order, meta_key,
                        origin_zone, origin_net, origin_node,
                        origin_point, origin_domain
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        area.code,
                        area.name,
                        area.kind.value,
                        area.sort_order,
                        area.meta_key,
                        origin.zone if origin is not None else None,
                        origin.net if origin is not None else None,
                        origin.node if origin is not None else None,
                        origin.point if origin is not None else None,
                        origin.domain if origin is not None else None,
                    ),
                )
        except BaseException:
            database_path.unlink(missing_ok=True)
            raise
        return cls(database_path)

    def add_area(self, area: AreaDefinition) -> None:
        origin = area.default_origin
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO areas (
                    code, name, kind, sort_order, meta_key,
                    origin_zone, origin_net, origin_node,
                    origin_point, origin_domain
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    area.code,
                    area.name,
                    area.kind.value,
                    area.sort_order,
                    area.meta_key,
                    origin.zone if origin is not None else None,
                    origin.net if origin is not None else None,
                    origin.node if origin is not None else None,
                    origin.point if origin is not None else None,
                    origin.domain if origin is not None else None,
                ),
            )

    def areas(self) -> tuple[AreaDefinition, ...]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                """
                SELECT code, name, kind, sort_order, meta_key,
                       origin_zone, origin_net, origin_node,
                       origin_point, origin_domain
                FROM areas
                ORDER BY id
                """
            ).fetchall()
        result = []
        for row in rows:
            origin = (
                FtnAddress(
                    zone=row[5],
                    net=row[6],
                    node=row[7],
                    point=row[8],
                    domain=row[9],
                )
                if row[5] is not None
                else None
            )
            result.append(
                AreaDefinition(
                    code=row[0],
                    name=row[1],
                    kind=AreaKind(row[2]),
                    default_origin=origin,
                    sort_order=row[3],
                    meta_key=row[4],
                )
            )
        return tuple(result)


def connect_database(
    path: Path, *, read_only: bool, timeout: float = 5.0
) -> sqlite3.Connection:
    if read_only:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=timeout,
            isolation_level=None,
        )
    else:
        connection = sqlite3.connect(path, timeout=timeout, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {round(timeout * 1000)}")
    application_id = connection.execute("PRAGMA application_id").fetchone()[0]
    schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if application_id != APPLICATION_ID or schema_version != SCHEMA_VERSION:
        connection.close()
        raise ValueError("not a supported golded-ftn-sqlite database")
    return connection


def select_area(connection: sqlite3.Connection, area_code: str | None) -> sqlite3.Row:
    if area_code is not None:
        row = connection.execute(
            "SELECT * FROM areas WHERE code = ?", (area_code,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown area [{area_code}]")
        return cast(sqlite3.Row, row)
    rows = connection.execute("SELECT * FROM areas ORDER BY id LIMIT 2").fetchall()
    if len(rows) != 1:
        raise ValueError("area_code is required unless the database has one area")
    return cast(sqlite3.Row, rows[0])
