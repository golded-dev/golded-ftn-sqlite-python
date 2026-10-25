from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import pytest
from golded_ftn import (
    ConflictError,
    ControlLine,
    FtnAddress,
    MessageControlLines,
    MessagePatch,
    MessageProvenance,
    OutgoingMessage,
    ReaderOptions,
    WriterOptions,
)

from golded_ftn_sqlite import (
    AreaDefinition,
    AreaKind,
    SqliteDatabase,
    SqliteReader,
    SqliteWriter,
)


def test_append_echomail_and_read_it_through_public_api(tmp_path: Path) -> None:
    path = tmp_path / "general.sqlite3"
    SqliteDatabase.create(
        path,
        area=AreaDefinition(
            code="FIDO.GENERAL",
            name="FidoNet General",
            kind=AreaKind.ECHOMAIL,
            default_origin=FtnAddress(zone=2, net=230, node=0),
        ),
    )
    outgoing = OutgoingMessage(
        from_name="Alice",
        to_name="All",
        subject="SQLite",
        body_text="One database file. No fossilized index sidecar.",
        external_id="2:230/0 71A1B2C3",
        from_address=FtnAddress(zone=2, net=230, node=0),
        posted_at=datetime(2026, 10, 23, 12, 34, 56, tzinfo=UTC),
        attributes_raw=0x101,
    )

    with SqliteWriter().open(path) as session:
        written = session.append(outgoing)
    (message,) = SqliteReader().read(path)

    assert (
        written.identity.format,
        written.identity.msgno,
        message.msgno,
        message.area_code,
        message.from_name,
        message.to_name,
        message.subject,
        message.body_text,
        message.external_id,
        message.from_address,
        message.posted_at,
        message.attributes_raw,
    ) == (
        "sqlite",
        1,
        1,
        "FIDO.GENERAL",
        "Alice",
        "All",
        "SQLite",
        "One database file. No fossilized index sidecar.",
        "2:230/0 71A1B2C3",
        "2:230/0",
        datetime(2026, 10, 23, 12, 34, 56, tzinfo=UTC),
        0x101,
    )


def test_reader_streams_by_default(tmp_path: Path) -> None:
    path = tmp_path / "stream.sqlite3"
    SqliteDatabase.create(
        path,
        area=AreaDefinition(
            code="FIDO.SQLITE",
            name="SQLite",
            kind=AreaKind.ECHOMAIL,
        ),
    )
    with SqliteWriter().open(path) as session:
        session.append(
            OutgoingMessage(
                from_name="Alice",
                to_name="All",
                subject="Stream",
                body_text="Yield rows from the cursor.",
            )
        )

    messages = SqliteReader().read(path)

    assert not isinstance(messages, tuple)
    assert [message.subject for message in messages] == ["Stream"]


def test_reader_can_load_messages_eagerly(tmp_path: Path) -> None:
    path = tmp_path / "eager.sqlite3"
    SqliteDatabase.create(
        path,
        area=AreaDefinition(
            code="FIDO.SQLITE",
            name="SQLite",
            kind=AreaKind.ECHOMAIL,
        ),
    )
    with SqliteWriter().open(path) as session:
        session.append(
            OutgoingMessage(
                from_name="Alice",
                to_name="All",
                subject="Eager",
                body_text="Load the snapshot before returning.",
            )
        )

    messages = SqliteReader().read(path, ReaderOptions(eager=True))

    assert isinstance(messages, tuple)
    assert messages[0].subject == "Eager"


def test_control_lines_routing_and_reply_links_keep_source_order(
    tmp_path: Path,
) -> None:
    path = tmp_path / "routing.sqlite3"
    SqliteDatabase.create(
        path,
        area=AreaDefinition(
            code="FIDO.SQLITE",
            name="SQLite",
            kind=AreaKind.ECHOMAIL,
        ),
    )

    controls = (
        ControlLine(name="PID", value="First", raw="\x01PID: First"),
        ControlLine(name="X-ODD", value="one", raw="\x01X-ODD: one"),
        ControlLine(name="X-ODD", value="two", raw="\x01X-ODD: two"),
        ControlLine(name="REPLY", value="2:230/0 old", raw="\x01REPLY: 2:230/0 old"),
        ControlLine(name="CHRS", value="UTF-8 4", raw="\x01CHRS: UTF-8 4"),
    )

    with SqliteWriter().open(path) as session:
        session.append(
            OutgoingMessage(
                from_name="Alice",
                to_name="All",
                subject="Routing",
                body_text="Still ordered.",
                external_id="2:230/0 new",
                control_lines=controls,
                reply_to_msgno=7,
                reply1st_msgno=8,
                reply_next_msgno=9,
                reply_list=(11, 12),
                routing_seen_by=("230/0 1", "230/2"),
                routing_path=("230/0", "203/0"),
            )
        )
    (message,) = SqliteReader().read(path)

    assert (
        message.control_lines,
        message.reply_to_msgno,
        message.reply1st_msgno,
        message.reply_next_msgno,
    ) == (
        MessageControlLines(
            kludges=controls,
            msgid="2:230/0 new",
            reply="2:230/0 old",
            charset="UTF-8 4",
            seen_by=("230/0 1", "230/2"),
            path=("230/0", "203/0"),
        ),
        7,
        8,
        9,
    )


def test_netmail_uses_area_origin_and_requires_explicit_destination(
    tmp_path: Path,
) -> None:
    path = tmp_path / "netmail.sqlite3"
    SqliteDatabase.create(
        path,
        area=AreaDefinition(
            code="NETMAIL",
            name="Netmail",
            kind=AreaKind.NETMAIL,
            default_origin=FtnAddress(zone=2, net=230, node=0),
        ),
    )

    with SqliteWriter().open(path) as session:
        session.append(
            OutgoingMessage(
                from_name="Alice",
                to_name="Bob",
                subject="Direct",
                body_text="Point to point.",
                to_address=FtnAddress(zone=1, net=105, node=42, point=3),
            )
        )
    (message,) = SqliteReader().read(path)

    assert (message.from_address, message.to_address) == (
        "2:230/0",
        "1:105/42.3",
    )


def test_rejected_netmail_does_not_consume_a_message_number(tmp_path: Path) -> None:
    path = tmp_path / "netmail.sqlite3"
    SqliteDatabase.create(
        path,
        area=AreaDefinition(
            code="NETMAIL",
            name="Netmail",
            kind=AreaKind.NETMAIL,
            default_origin=FtnAddress(zone=2, net=230, node=0),
        ),
    )

    with SqliteWriter().open(path) as session:
        with pytest.raises(ValueError, match="destination address"):
            session.append(
                OutgoingMessage(
                    from_name="Alice",
                    to_name="Nowhere",
                    subject="Missing route",
                    body_text="This must not be stored.",
                )
            )
        written = session.append(
            OutgoingMessage(
                from_name="Alice",
                to_name="Bob",
                subject="Routed",
                body_text="This one is valid.",
                to_address=FtnAddress(zone=1, net=105, node=42),
            )
        )

    assert written.identity.msgno == 1


def test_writer_session_reads_the_message_with_its_revision(tmp_path: Path) -> None:
    path = tmp_path / "session.sqlite3"
    SqliteDatabase.create(
        path,
        area=AreaDefinition(
            code="FIDO.SQLITE",
            name="SQLite",
            kind=AreaKind.ECHOMAIL,
        ),
    )

    with SqliteWriter().open(path) as session:
        written = session.append(
            OutgoingMessage(
                from_name="Alice",
                to_name="All",
                subject="Revision",
                body_text="Read me back.",
            )
        )
        stored = session.read(1)

    assert (
        stored.identity,
        stored.revision,
        stored.message.subject,
        stored.message.body_text,
    ) == (
        written.identity,
        written.revision,
        "Revision",
        "Read me back.",
    )


def test_update_changes_selected_fields_and_advances_revision(tmp_path: Path) -> None:
    path = tmp_path / "update.sqlite3"
    SqliteDatabase.create(
        path,
        area=AreaDefinition(
            code="FIDO.SQLITE",
            name="SQLite",
            kind=AreaKind.ECHOMAIL,
        ),
    )

    with SqliteWriter().open(path) as session:
        written = session.append(
            OutgoingMessage(
                from_name="Alice",
                to_name="All",
                subject="Before",
                body_text="Keep this body.",
            )
        )
        updated = session.update(
            written.identity,
            MessagePatch(subject="After"),
            written.revision,
        )
        stored = session.read(1)

    assert (
        stored.message.subject,
        stored.message.body_text,
        updated.identity,
        updated.revision.location,
        updated.revision != written.revision,
    ) == (
        "After",
        "Keep this body.",
        written.identity,
        (written.revision.location[0], 2),
        True,
    )


def test_update_replaces_message_metadata_atomically(tmp_path: Path) -> None:
    path = tmp_path / "metadata-update.sqlite3"
    SqliteDatabase.create(
        path,
        area=AreaDefinition(
            code="FIDO.SQLITE",
            name="SQLite",
            kind=AreaKind.ECHOMAIL,
        ),
    )
    new_control = ControlLine(name="PID", value="New", raw="\x01PID: New")

    with SqliteWriter().open(path) as session:
        written = session.append(
            OutgoingMessage(
                from_name="Alice",
                to_name="All",
                subject="Before",
                body_text="Before body",
                external_id="old-id",
                control_lines=(
                    ControlLine(name="PID", value="Old", raw="\x01PID: Old"),
                ),
                routing_seen_by=("230/0",),
                routing_path=("230/0",),
            )
        )
        updated = session.update(
            written.identity,
            MessagePatch(
                from_name="Carol",
                to_name="Everyone",
                subject="After",
                body_text="After body",
                external_id="new-id",
                from_address=FtnAddress(zone=2, net=230, node=1, point=4),
                posted_at=datetime(2026, 10, 23, 15, 0, tzinfo=UTC),
                attributes_raw=0x42,
                control_lines=(new_control,),
                reply_to_msgno=3,
                reply1st_msgno=4,
                reply_next_msgno=5,
                reply_list=(6, 7),
                routing_seen_by=("230/1 2",),
                routing_path=("230/1",),
            ),
            written.revision,
        )
        stored = session.read(1)

    assert (
        stored.message.from_name,
        stored.message.to_name,
        stored.message.subject,
        stored.message.body_text,
        stored.message.external_id,
        stored.message.from_address,
        stored.message.posted_at,
        stored.message.attributes_raw,
        stored.message.control_lines,
        stored.message.reply_to_msgno,
        stored.message.reply1st_msgno,
        stored.message.reply_next_msgno,
        updated.revision.location,
    ) == (
        "Carol",
        "Everyone",
        "After",
        "After body",
        "new-id",
        "2:230/1.4",
        datetime(2026, 10, 23, 15, 0, tzinfo=UTC),
        0x42,
        MessageControlLines(
            kludges=(new_control,),
            msgid="new-id",
            seen_by=("230/1 2",),
            path=("230/1",),
        ),
        3,
        4,
        5,
        (written.revision.location[0], 2),
    )


def test_delete_hides_message_without_reusing_its_number(tmp_path: Path) -> None:
    path = tmp_path / "delete.sqlite3"
    SqliteDatabase.create(
        path,
        area=AreaDefinition(
            code="FIDO.SQLITE",
            name="SQLite",
            kind=AreaKind.ECHOMAIL,
        ),
    )

    with SqliteWriter().open(path) as session:
        first = session.append(
            OutgoingMessage(
                from_name="Alice",
                to_name="All",
                subject="Remove",
                body_text="Soft delete me.",
            )
        )
        deleted = session.delete(first.identity, first.revision)
        with pytest.raises(ConflictError, match="missing"):
            session.read(1)
        second = session.append(
            OutgoingMessage(
                from_name="Alice",
                to_name="All",
                subject="Keep",
                body_text="I am next.",
            )
        )

    assert (
        deleted,
        second.identity.msgno,
        tuple(message.msgno for message in SqliteReader().read(path)),
    ) == (first.identity, 2, (2,))


def test_revision_detects_control_changes_from_an_independent_sqlite_client(
    tmp_path: Path,
) -> None:
    path = tmp_path / "conflict.sqlite3"
    SqliteDatabase.create(
        path,
        area=AreaDefinition(
            code="FIDO.SQLITE",
            name="SQLite",
            kind=AreaKind.ECHOMAIL,
        ),
    )
    with SqliteWriter().open(path) as session:
        written = session.append(
            OutgoingMessage(
                from_name="Alice",
                to_name="All",
                subject="Conflict",
                body_text="Watch every row.",
                control_lines=(
                    ControlLine(name="PID", value="Before", raw="\x01PID: Before"),
                ),
            )
        )

    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            UPDATE control_lines
            SET value = 'After', raw = char(1) || 'PID: After'
            WHERE message_id = ?
            """,
            (written.revision.location[0],),
        )

    with SqliteWriter().open(path) as session:
        with pytest.raises(ConflictError):
            session.update(
                written.identity,
                MessagePatch(subject="Must not win"),
                written.revision,
            )


def test_concurrent_sessions_allocate_each_message_number_once(tmp_path: Path) -> None:
    path = tmp_path / "concurrent.sqlite3"
    SqliteDatabase.create(
        path,
        area=AreaDefinition(
            code="FIDO.SQLITE",
            name="SQLite",
            kind=AreaKind.ECHOMAIL,
        ),
    )
    workers = 4
    messages_per_worker = 10
    barrier = Barrier(workers)

    def append_batch(worker: int) -> tuple[int, ...]:
        numbers = []
        with SqliteWriter().open(
            path, WriterOptions(concurrent=True, lock_timeout=5)
        ) as session:
            barrier.wait()
            for index in range(messages_per_worker):
                written = session.append(
                    OutgoingMessage(
                        from_name=f"Worker {worker}",
                        to_name="All",
                        subject=f"Message {index}",
                        body_text="Concurrent but serialized.",
                    )
                )
                numbers.append(written.identity.msgno)
        return tuple(numbers)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        batches = tuple(executor.map(append_batch, range(workers)))

    assert sorted(number for batch in batches for number in batch) == list(
        range(1, workers * messages_per_worker + 1)
    )


def test_import_provenance_survives_storage(tmp_path: Path) -> None:
    path = tmp_path / "provenance.sqlite3"
    SqliteDatabase.create(
        path,
        area=AreaDefinition(
            code="FIDO.SQLITE",
            name="SQLite",
            kind=AreaKind.ECHOMAIL,
        ),
    )
    provenance = MessageProvenance(
        source_type="jam",
        source_path="archive/general",
        source_id="42",
        source_offset=1024,
    )

    with SqliteWriter().open(path) as session:
        session.append(
            OutgoingMessage(
                from_name="Alice",
                to_name="All",
                subject="Imported",
                body_text="Keep where I came from.",
                provenance=provenance,
            )
        )
    (stored,) = SqliteReader().read(path)

    assert stored.provenance == provenance


def test_multi_area_database_requires_selection_and_numbers_per_area(
    tmp_path: Path,
) -> None:
    path = tmp_path / "multi.sqlite3"
    database = SqliteDatabase.create(
        path,
        area=AreaDefinition(
            code="FIDO.SQLITE",
            name="SQLite",
            kind=AreaKind.ECHOMAIL,
        ),
    )
    database.add_area(
        AreaDefinition(
            code="NETMAIL",
            name="Netmail",
            kind=AreaKind.NETMAIL,
            default_origin=FtnAddress(zone=2, net=230, node=0),
        )
    )

    with SqliteWriter("FIDO.SQLITE").open(path) as session:
        echo = session.append(
            OutgoingMessage(
                from_name="Alice",
                to_name="All",
                subject="Echo",
                body_text="Area one.",
            )
        )
    with SqliteWriter("NETMAIL").open(path) as session:
        netmail = session.append(
            OutgoingMessage(
                from_name="Alice",
                to_name="Bob",
                subject="Netmail",
                body_text="Area two.",
                to_address=FtnAddress(zone=1, net=105, node=42),
            )
        )

    with pytest.raises(ValueError, match="area_code is required"):
        tuple(SqliteReader().read(path))
    assert (
        echo.identity.msgno,
        netmail.identity.msgno,
        tuple(message.subject for message in SqliteReader("FIDO.SQLITE").read(path)),
        tuple(message.subject for message in SqliteReader("netmail").read(path)),
    ) == (1, 1, ("Echo",), ("Netmail",))


def test_session_read_is_local_to_the_requested_message(tmp_path: Path) -> None:
    path = tmp_path / "local-read.sqlite3"
    SqliteDatabase.create(
        path,
        area=AreaDefinition(
            code="FIDO.SQLITE",
            name="SQLite",
            kind=AreaKind.ECHOMAIL,
        ),
    )
    with SqliteWriter().open(path) as session:
        session.append(
            OutgoingMessage(
                from_name="Alice",
                to_name="All",
                subject="Readable",
                body_text="This row is valid.",
            )
        )
        session.append(
            OutgoingMessage(
                from_name="Alice",
                to_name="All",
                subject="Unrelated",
                body_text="This row will be damaged.",
            )
        )

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE messages SET posted_at = 'not-a-date' WHERE msgno = 2"
        )

    with SqliteWriter().open(path) as session:
        stored = session.read(1)

    assert stored.message.subject == "Readable"
