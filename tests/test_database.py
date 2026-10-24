from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from golded_ftn import FtnAddress

from golded_ftn_sqlite import AreaDefinition, AreaKind, SqliteDatabase


def test_create_single_area_database_is_a_readable_sqlite_format(
    tmp_path: Path,
) -> None:
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

    with sqlite3.connect(path) as connection:
        observed = (
            connection.execute("PRAGMA application_id").fetchone()[0],
            connection.execute("PRAGMA user_version").fetchone()[0],
            connection.execute(
                """
                SELECT code, name, kind, origin_address, next_message_number
                FROM areas
                """
            ).fetchone(),
        )

    assert observed == (
        0x47465453,
        1,
        ("FIDO.GENERAL", "FidoNet General", "echomail", "2:230/0", 1),
    )


def test_database_can_catalog_more_than_one_area(tmp_path: Path) -> None:
    path = tmp_path / "messages.sqlite3"
    echomail = AreaDefinition(
        code="FIDO.GENERAL",
        name="FidoNet General",
        kind=AreaKind.ECHOMAIL,
        default_origin=FtnAddress(zone=2, net=230, node=0),
    )
    netmail = AreaDefinition(
        code="NETMAIL",
        name="Netmail",
        kind=AreaKind.NETMAIL,
        default_origin=FtnAddress(zone=2, net=230, node=0),
    )
    database = SqliteDatabase.create(path, area=echomail)

    database.add_area(netmail)

    assert database.areas() == (echomail, netmail)


def test_created_database_uses_wal_for_concurrent_readers_and_writers(
    tmp_path: Path,
) -> None:
    path = tmp_path / "wal.sqlite3"
    SqliteDatabase.create(
        path,
        area=AreaDefinition(
            code="FIDO.SQLITE",
            name="SQLite",
            kind=AreaKind.ECHOMAIL,
        ),
    )

    with sqlite3.connect(path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert journal_mode == "wal"


def test_area_catalog_keeps_sort_order_and_meta_key(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    area = AreaDefinition(
        code="FIDO.SQLITE",
        name="SQLite",
        kind=AreaKind.ECHOMAIL,
        sort_order=17,
        meta_key="group:technical",
    )

    database = SqliteDatabase.create(path, area=area)

    assert database.areas() == (area,)


def test_open_validates_the_database_format(tmp_path: Path) -> None:
    valid_path = tmp_path / "valid.sqlite3"
    area = AreaDefinition(
        code="FIDO.SQLITE",
        name="SQLite",
        kind=AreaKind.ECHOMAIL,
    )
    SqliteDatabase.create(valid_path, area=area)
    unrelated_path = tmp_path / "unrelated.sqlite3"
    sqlite3.connect(unrelated_path).close()

    reopened = SqliteDatabase.open(valid_path)

    assert reopened.areas() == (area,)
    with pytest.raises(ValueError, match="not a supported"):
        SqliteDatabase.open(unrelated_path)


def test_schema_rejects_netmail_without_complete_addresses(tmp_path: Path) -> None:
    path = tmp_path / "netmail.sqlite3"
    SqliteDatabase.create(
        path,
        area=AreaDefinition(
            code="NETMAIL",
            name="Netmail",
            kind=AreaKind.NETMAIL,
        ),
    )

    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="netmail requires"):
            connection.execute(
                """
                INSERT INTO messages (
                    area_id, msgno, from_name, to_name, subject, body_text,
                    attributes_raw
                ) VALUES (1, 1, 'Alice', 'Bob', 'Invalid', 'No addresses', 0)
                """
            )


def test_schema_rejects_blank_address_text(tmp_path: Path) -> None:
    path = tmp_path / "blank-address.sqlite3"
    SqliteDatabase.create(
        path,
        area=AreaDefinition(
            code="FIDO.SQLITE",
            name="SQLite",
            kind=AreaKind.ECHOMAIL,
        ),
    )

    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO messages (
                    area_id, msgno, from_name, to_name, subject, body_text,
                    attributes_raw, from_address
                ) VALUES (1, 1, 'Alice', 'All', 'Blank', 'No', 0, '')
                """
            )
