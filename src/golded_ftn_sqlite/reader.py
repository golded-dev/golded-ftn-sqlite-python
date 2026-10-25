from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from datetime import datetime
from os import PathLike
from pathlib import Path

from golded_ftn import (
    ControlLine,
    MessageControlLines,
    MessageProvenance,
    ParsedMessage,
    ReaderOptions,
    parse_message,
)

from .database import connect_database, select_area


def _message_from_row(
    connection: sqlite3.Connection,
    database_path: Path,
    area: sqlite3.Row,
    row: sqlite3.Row,
) -> ParsedMessage:
    controls = tuple(
        ControlLine(name=item["name"], value=item["value"], raw=item["raw"])
        for item in connection.execute(
            """
            SELECT name, value, raw FROM control_lines
            WHERE message_id = ? ORDER BY position
            """,
            (row["id"],),
        )
    )
    routing = {
        kind: tuple(
            item["value"]
            for item in connection.execute(
                """
                SELECT value FROM routing_entries
                WHERE message_id = ? AND kind = ? ORDER BY position
                """,
                (row["id"], kind),
            )
        )
        for kind in ("seen_by", "path")
    }
    parsed_body = parse_message(row["body_text"])
    by_name: dict[str, str] = {}
    for control in controls:
        by_name.setdefault(control.name.upper(), control.value)
    provenance = (
        MessageProvenance(
            source_type=row["source_type"],
            source_path=row["source_path"],
            source_id=row["source_id"],
            source_offset=row["source_offset"],
        )
        if row["source_type"] is not None
        else MessageProvenance(
            source_type="sqlite",
            source_path=str(database_path),
            source_id=str(row["id"]),
        )
    )
    return ParsedMessage(
        msgno=row["msgno"],
        from_name=row["from_name"],
        to_name=row["to_name"],
        subject=row["subject"],
        body_text=row["body_text"],
        attributes_raw=row["attributes_raw"],
        posted_at=(
            datetime.fromisoformat(row["posted_at"])
            if row["posted_at"] is not None
            else None
        ),
        external_id=row["external_id"],
        from_address=row["from_address"],
        to_address=row["to_address"],
        reply_to_msgno=row["reply_to_msgno"],
        reply1st_msgno=row["reply1st_msgno"],
        reply_next_msgno=row["reply_next_msgno"],
        area_code=area["code"],
        area_name=area["name"],
        area_sort_order=area["sort_order"],
        area_meta_key=area["meta_key"],
        control_lines=MessageControlLines(
            kludges=controls,
            msgid=row["external_id"] or by_name.get("MSGID"),
            reply=by_name.get("REPLY"),
            charset=by_name.get("CHRS") or by_name.get("CHARSET"),
            seen_by=routing["seen_by"],
            path=routing["path"],
            tearline=parsed_body.tearline,
            origin=parsed_body.origin,
            origin_address=parsed_body.origin_address,
        ),
        provenance=provenance,
    )


class SqliteReader:
    """Read undeleted messages for one area in msgno order.

    Default streaming yields rows from a live cursor. Pass
    ``ReaderOptions(eager=True)`` to load every message before returning.
    """

    def __init__(self, area_code: str | None = None) -> None:
        self.area_code = area_code

    def read(
        self,
        path: str | PathLike[str],
        options: ReaderOptions | None = None,
    ) -> Iterable[ParsedMessage]:
        options = options or ReaderOptions()
        database_path = Path(path).resolve()
        connection = connect_database(database_path, read_only=True)
        try:
            connection.execute("BEGIN")
            area = select_area(connection, self.area_code)
            rows = connection.execute(
                """
                SELECT * FROM messages
                WHERE area_id = ? AND deleted_at IS NULL
                ORDER BY msgno
                """,
                (area["id"],),
            )
            if options.eager:
                messages = tuple(
                    _message_from_row(connection, database_path, area, row)
                    for row in rows.fetchall()
                )
                connection.commit()
                connection.close()
                return messages
            return self._stream(connection, database_path, area, rows)
        except BaseException:
            connection.rollback()
            connection.close()
            raise

    @staticmethod
    def _stream(
        connection: sqlite3.Connection,
        database_path: Path,
        area: sqlite3.Row,
        rows: sqlite3.Cursor,
    ) -> Iterator[ParsedMessage]:
        try:
            for row in rows:
                yield _message_from_row(connection, database_path, area, row)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
