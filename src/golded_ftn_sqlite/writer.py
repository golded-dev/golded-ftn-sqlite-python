from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path
from types import TracebackType
from typing import Self

from golded_ftn import (
    ConflictError,
    FtnAddress,
    MessageIdentity,
    MessagePatch,
    OutgoingMessage,
    RevisionToken,
    SessionMessage,
    Unset,
    WriterError,
    WriteResult,
    WriterOptions,
)

from .database import connect_database, select_area
from .reader import _message_from_row


def _address_values(address: FtnAddress | None) -> tuple[object, ...]:
    if address is None:
        return (None, None, None, None, None)
    return (address.zone, address.net, address.node, address.point, address.domain)


def _row_address(row: sqlite3.Row, prefix: str) -> FtnAddress | None:
    if row[f"{prefix}_zone"] is None:
        return None
    return FtnAddress(
        zone=row[f"{prefix}_zone"],
        net=row[f"{prefix}_net"],
        node=row[f"{prefix}_node"],
        point=row[f"{prefix}_point"],
        domain=row[f"{prefix}_domain"],
    )


def _identity(path: Path, area_code: str, msgno: int) -> MessageIdentity:
    return MessageIdentity(
        format="sqlite",
        base=f"{path}#{area_code}",
        msgno=msgno,
    )


def _revision(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    identity: MessageIdentity,
) -> RevisionToken:
    revision = int(row["revision"])
    message_id = int(row["id"])
    related = (
        tuple(
            tuple(item)
            for item in connection.execute(
                """
                SELECT position, name, value, raw FROM control_lines
                WHERE message_id = ? ORDER BY position
                """,
                (message_id,),
            )
        ),
        tuple(
            tuple(item)
            for item in connection.execute(
                """
                SELECT kind, position, value FROM routing_entries
                WHERE message_id = ? ORDER BY kind, position
                """,
                (message_id,),
            )
        ),
        tuple(
            tuple(item)
            for item in connection.execute(
                """
                SELECT position, msgno FROM reply_list_entries
                WHERE message_id = ? ORDER BY position
                """,
                (message_id,),
            )
        ),
    )
    digest = hashlib.sha256(
        json.dumps(
            (tuple(row), related), ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return RevisionToken(
        identity=identity,
        location=(message_id, revision),
        digest=digest,
    )


class SqliteWriter:
    def __init__(self, area_code: str | None = None) -> None:
        self.area_code = area_code

    def open(
        self,
        path: str | PathLike[str],
        options: WriterOptions | None = None,
    ) -> SqliteSession:
        return SqliteSession(
            Path(path).resolve(), self.area_code, options or WriterOptions()
        )


class SqliteSession:
    def __init__(
        self, path: Path, area_code: str | None, options: WriterOptions
    ) -> None:
        self.path = path
        self.area_code = area_code
        self._connection = connect_database(
            path, read_only=False, timeout=options.lock_timeout
        )
        self._closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def append(self, message: OutgoingMessage) -> WriteResult:
        if self._closed:
            raise WriterError("SQLite session is closed")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            area = select_area(connection, self.area_code)
            default_origin = (
                FtnAddress(
                    zone=area["origin_zone"],
                    net=area["origin_net"],
                    node=area["origin_node"],
                    point=area["origin_point"],
                    domain=area["origin_domain"],
                )
                if area["origin_zone"] is not None
                else None
            )
            from_address = message.from_address or default_origin
            if area["kind"] == "netmail":
                if from_address is None:
                    raise ValueError("netmail requires an origin address")
                if message.to_address is None:
                    raise ValueError("netmail requires a destination address")
            msgno = int(area["next_message_number"])
            cursor = connection.execute(
                """
                INSERT INTO messages (
                    area_id, msgno, from_name, to_name, subject, body_text,
                    posted_at, external_id, attributes_raw,
                    from_zone, from_net, from_node, from_point, from_domain,
                    to_zone, to_net, to_node, to_point, to_domain,
                    reply_to_msgno, reply1st_msgno, reply_next_msgno,
                    source_type, source_path, source_id, source_offset
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    area["id"],
                    msgno,
                    message.from_name,
                    message.to_name,
                    message.subject,
                    message.body_text,
                    message.posted_at.isoformat() if message.posted_at else None,
                    message.external_id,
                    message.attributes_raw if message.attributes_raw is not None else 0,
                    *_address_values(from_address),
                    *_address_values(message.to_address),
                    message.reply_to_msgno,
                    message.reply1st_msgno,
                    message.reply_next_msgno,
                    message.provenance.source_type if message.provenance else None,
                    message.provenance.source_path if message.provenance else None,
                    message.provenance.source_id if message.provenance else None,
                    message.provenance.source_offset if message.provenance else None,
                ),
            )
            if cursor.lastrowid is None:
                raise WriterError("SQLite did not return a message row ID")
            message_id = cursor.lastrowid
            connection.executemany(
                """
                INSERT INTO control_lines (message_id, position, name, value, raw)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (message_id, position, line.name, line.value, line.raw)
                    for position, line in enumerate(message.control_lines)
                ),
            )
            connection.executemany(
                """
                INSERT INTO routing_entries (message_id, kind, position, value)
                VALUES (?, 'seen_by', ?, ?)
                """,
                (
                    (message_id, position, value)
                    for position, value in enumerate(message.routing_seen_by)
                ),
            )
            connection.executemany(
                """
                INSERT INTO routing_entries (message_id, kind, position, value)
                VALUES (?, 'path', ?, ?)
                """,
                (
                    (message_id, position, value)
                    for position, value in enumerate(message.routing_path)
                ),
            )
            connection.executemany(
                """
                INSERT INTO reply_list_entries (message_id, position, msgno)
                VALUES (?, ?, ?)
                """,
                (
                    (message_id, position, value)
                    for position, value in enumerate(message.reply_list)
                ),
            )
            connection.execute(
                "UPDATE areas SET next_message_number = ? WHERE id = ?",
                (msgno + 1, area["id"]),
            )
            stored = connection.execute(
                "SELECT * FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
            assert stored is not None
            identity = _identity(self.path, area["code"], msgno)
            revision = _revision(connection, stored, identity)
            connection.commit()
            return WriteResult(
                identity=identity,
                revision=revision,
            )
        except BaseException:
            connection.rollback()
            raise

    def read(self, msgno: int) -> SessionMessage:
        if self._closed:
            raise WriterError("SQLite session is closed")
        connection = self._connection
        try:
            connection.execute("BEGIN")
            area = select_area(connection, self.area_code)
            row = connection.execute(
                """
                SELECT * FROM messages
                WHERE area_id = ? AND msgno = ? AND deleted_at IS NULL
                """,
                (area["id"], msgno),
            ).fetchone()
            if row is None:
                raise KeyError(msgno)
            identity = _identity(self.path, area["code"], msgno)
            result = SessionMessage(
                message=_message_from_row(connection, self.path, area, row),
                identity=identity,
                revision=_revision(connection, row, identity),
            )
            connection.commit()
            return result
        except BaseException:
            connection.rollback()
            raise

    def update(
        self,
        identity: MessageIdentity,
        patch: MessagePatch,
        expected_revision: RevisionToken,
    ) -> WriteResult:
        if self._closed:
            raise WriterError("SQLite session is closed")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            area = select_area(connection, self.area_code)
            row = connection.execute(
                """
                SELECT * FROM messages
                WHERE area_id = ? AND msgno = ? AND deleted_at IS NULL
                """,
                (area["id"], identity.msgno),
            ).fetchone()
            if row is None:
                raise KeyError(identity.msgno)
            actual_identity = _identity(self.path, area["code"], identity.msgno)
            actual_revision = _revision(connection, row, actual_identity)
            if (
                identity != actual_identity
                or expected_revision.identity != actual_identity
                or expected_revision != actual_revision
            ):
                raise ConflictError("SQLite message revision changed")
            from_name = (
                row["from_name"]
                if isinstance(patch.from_name, Unset)
                else patch.from_name
            )
            to_name = (
                row["to_name"] if isinstance(patch.to_name, Unset) else patch.to_name
            )
            subject = (
                row["subject"] if isinstance(patch.subject, Unset) else patch.subject
            )
            body_text = (
                row["body_text"]
                if isinstance(patch.body_text, Unset)
                else patch.body_text
            )
            if None in (from_name, to_name, subject, body_text):
                raise ValueError("names, subject and body_text cannot be None")
            external_id = (
                row["external_id"]
                if isinstance(patch.external_id, Unset)
                else patch.external_id
            )
            from_address = (
                _row_address(row, "from")
                if isinstance(patch.from_address, Unset)
                else patch.from_address
            )
            to_address = (
                _row_address(row, "to")
                if isinstance(patch.to_address, Unset)
                else patch.to_address
            )
            if isinstance(patch.posted_at, Unset):
                posted_at = row["posted_at"]
            elif patch.posted_at is None:
                posted_at = None
            else:
                posted_at = patch.posted_at.isoformat()
            attributes_raw = (
                row["attributes_raw"]
                if isinstance(patch.attributes_raw, Unset)
                else patch.attributes_raw
            )
            if attributes_raw is None:
                raise ValueError("attributes_raw cannot be None")
            reply_to_msgno = (
                row["reply_to_msgno"]
                if isinstance(patch.reply_to_msgno, Unset)
                else patch.reply_to_msgno
            )
            reply1st_msgno = (
                row["reply1st_msgno"]
                if isinstance(patch.reply1st_msgno, Unset)
                else patch.reply1st_msgno
            )
            reply_next_msgno = (
                row["reply_next_msgno"]
                if isinstance(patch.reply_next_msgno, Unset)
                else patch.reply_next_msgno
            )
            if isinstance(patch.provenance, Unset):
                provenance_values = (
                    row["source_type"],
                    row["source_path"],
                    row["source_id"],
                    row["source_offset"],
                )
            elif patch.provenance is None:
                provenance_values = (None, None, None, None)
            else:
                provenance_values = (
                    patch.provenance.source_type,
                    patch.provenance.source_path,
                    patch.provenance.source_id,
                    patch.provenance.source_offset,
                )
            if area["kind"] == "netmail":
                if from_address is None:
                    raise ValueError("netmail requires an origin address")
                if to_address is None:
                    raise ValueError("netmail requires a destination address")
            connection.execute(
                """
                UPDATE messages SET
                    from_name = ?, to_name = ?, subject = ?, body_text = ?,
                    posted_at = ?, external_id = ?, attributes_raw = ?,
                    from_zone = ?, from_net = ?, from_node = ?,
                    from_point = ?, from_domain = ?,
                    to_zone = ?, to_net = ?, to_node = ?,
                    to_point = ?, to_domain = ?,
                    reply_to_msgno = ?, reply1st_msgno = ?, reply_next_msgno = ?,
                    source_type = ?, source_path = ?, source_id = ?, source_offset = ?,
                    revision = revision + 1
                WHERE id = ?
                """,
                (
                    from_name,
                    to_name,
                    subject,
                    body_text,
                    posted_at,
                    external_id,
                    attributes_raw,
                    *_address_values(from_address),
                    *_address_values(to_address),
                    reply_to_msgno,
                    reply1st_msgno,
                    reply_next_msgno,
                    *provenance_values,
                    row["id"],
                ),
            )
            if not isinstance(patch.control_lines, Unset):
                connection.execute(
                    "DELETE FROM control_lines WHERE message_id = ?", (row["id"],)
                )
                connection.executemany(
                    """
                    INSERT INTO control_lines
                        (message_id, position, name, value, raw)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        (row["id"], position, line.name, line.value, line.raw)
                        for position, line in enumerate(patch.control_lines or ())
                    ),
                )
            for kind, values in (
                ("seen_by", patch.routing_seen_by),
                ("path", patch.routing_path),
            ):
                if isinstance(values, Unset):
                    continue
                connection.execute(
                    "DELETE FROM routing_entries WHERE message_id = ? AND kind = ?",
                    (row["id"], kind),
                )
                connection.executemany(
                    """
                    INSERT INTO routing_entries (message_id, kind, position, value)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        (row["id"], kind, position, value)
                        for position, value in enumerate(values or ())
                    ),
                )
            if not isinstance(patch.reply_list, Unset):
                connection.execute(
                    "DELETE FROM reply_list_entries WHERE message_id = ?",
                    (row["id"],),
                )
                connection.executemany(
                    """
                    INSERT INTO reply_list_entries (message_id, position, msgno)
                    VALUES (?, ?, ?)
                    """,
                    (
                        (row["id"], position, msgno)
                        for position, msgno in enumerate(patch.reply_list or ())
                    ),
                )
            stored = connection.execute(
                "SELECT * FROM messages WHERE id = ?", (row["id"],)
            ).fetchone()
            assert stored is not None
            revision = _revision(connection, stored, actual_identity)
            connection.commit()
            return WriteResult(
                identity=actual_identity,
                revision=revision,
            )
        except BaseException:
            connection.rollback()
            raise

    def delete(
        self,
        identity: MessageIdentity,
        expected_revision: RevisionToken,
    ) -> MessageIdentity:
        if self._closed:
            raise WriterError("SQLite session is closed")
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            area = select_area(connection, self.area_code)
            row = connection.execute(
                """
                SELECT * FROM messages
                WHERE area_id = ? AND msgno = ? AND deleted_at IS NULL
                """,
                (area["id"], identity.msgno),
            ).fetchone()
            if row is None:
                raise KeyError(identity.msgno)
            actual_identity = _identity(self.path, area["code"], identity.msgno)
            actual_revision = _revision(connection, row, actual_identity)
            if (
                identity != actual_identity
                or expected_revision.identity != actual_identity
                or expected_revision != actual_revision
            ):
                raise ConflictError("SQLite message revision changed")
            connection.execute(
                """
                UPDATE messages
                SET deleted_at = ?, revision = revision + 1
                WHERE id = ?
                """,
                (datetime.now(UTC).isoformat(), row["id"]),
            )
            connection.commit()
            return actual_identity
        except BaseException:
            connection.rollback()
            raise
