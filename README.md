# golded-ftn-sqlite

`golded-ftn-sqlite` stores FTN netmail and echomail in SQLite through the
[`golded-ftn`](https://github.com/golded-dev/golded-ftn-python) message models.
It requires Python 3.12 or newer.

The format supports several areas in one database. The ordinary layout is still
one database file per area: it maps cleanly to GoldED configuration, keeps backup
and recovery local, and limits the blast radius when a disk has opinions.

## Use it

```python
from golded_ftn import FtnAddress, OutgoingMessage
from golded_ftn_sqlite import (
    AreaDefinition,
    AreaKind,
    SqliteDatabase,
    SqliteReader,
    SqliteWriter,
)

path = "fido-general.sqlite3"
SqliteDatabase.create(
    path,
    area=AreaDefinition(
        code="FIDO.GENERAL",
        name="FidoNet General",
        kind=AreaKind.ECHOMAIL,
        default_origin=FtnAddress(zone=2, net=230, node=0),
    ),
)

with SqliteWriter().open(path) as session:
    result = session.append(
        OutgoingMessage(
            from_name="Alice",
            to_name="All",
            subject="Hello",
            body_text="Stored transactionally.",
        )
    )
    stored = session.read(result.identity.msgno)

messages = tuple(SqliteReader().read(path))
```

When a database contains several areas, pass the area code to `SqliteReader` and
`SqliteWriter`. Omitting it is allowed only for a single-area database. Message
numbers are monotonic and local to each area.

Netmail requires origin and destination addresses. The origin may come from the
area's `default_origin`; the destination is always explicit. Echomail keeps ordered
control lines, repeated unknown kludges, `SEEN-BY`, `PATH`, reply links and import
provenance.

Writer sessions provide `read`, `append`, `update` and soft `delete`. Updates and
deletes require a revision token. The token covers the message row and its ordered
control, routing and reply-list rows, including changes made by another SQLite
client.

## SQLite format

Schema version 1 uses SQLite application ID `0x47465453` (`GFTS`), strict tables,
foreign keys and WAL journaling. The schema is public; see [the schema guide](docs/schema.md).

Do not copy an open WAL database as a lone `.sqlite3` file. Use SQLite's backup API
or close/checkpoint it first.

## Development

```sh
uv sync --python 3.14
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python -m mypy.stubtest golded_ftn_sqlite
uv build
uv run twine check dist/*
uv run python scripts/verify_distribution.py
```

No repository, release, tag or PyPI publication is created by this checkout.
