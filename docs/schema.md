# golded-ftn-sqlite format (schema version 1)

This document is the public format contract for a
`golded-ftn-sqlite` database. It is written so another language, package or
application can read and write the same files without depending on the Python
reference implementation.

Changing existing columns, constraints or meaning requires a migration and a new
`user_version`. Silently reinterpreting version 1 is not allowed.

Related operational choice: [single-area default, multi-area format](adr/0001-single-area-default-multi-area-format.md).

## Identification

| Pragma | Value | Meaning |
| --- | --- | --- |
| `application_id` | `0x47465453` (`GFTS`) | Identifies this format |
| `user_version` | `1` | Schema version |

A conforming reader **must** reject a database where either value differs.

ASCII mnemonic for the application ID: `G` `F` `T` `S`
(`0x47 0x46 0x54 0x53`).

## File layout

- One SQLite database file holds one or more **areas**.
- The ordinary deployment is **one area per file**. The format still permits
  several areas in one file for servers and tossers.
- Message numbers are positive, monotonic and **local to each area**.
- Soft-deleted message numbers remain consumed; they are never returned to the
  allocator.

Recommended filename extension: `.sqlite3`. The extension is not part of the
format contract.

## Connection requirements

On every open connection, a conforming implementation **must**:

1. Verify `application_id` and `user_version` as above.
2. Enable foreign keys: `PRAGMA foreign_keys = ON`.

Created databases use:

- `PRAGMA journal_mode = WAL`
- `PRAGMA synchronous = NORMAL`

Readers and writers may open an existing database without changing journal mode,
but they **must** tolerate WAL. Do not assume rollback-journal-only behaviour.

Suggested busy handling for writers that compete for locks: a positive busy
timeout (the Python reference uses five seconds by default).

## Concurrency and transactions

| Operation | Recommended transaction | Notes |
| --- | --- | --- |
| Allocate `msgno` and insert | `BEGIN IMMEDIATE` | Serializes allocation |
| Update or soft-delete | `BEGIN IMMEDIATE` | Serializes mutation |
| Snapshot read of one area | `BEGIN` (deferred) | SQLite snapshot semantics |

Writers that allocate or mutate **must** use an immediate transaction (or an
equivalent exclusive write lock) covering:

1. Read of `areas.next_message_number` (when appending)
2. Insert or update of `messages` and related child rows
3. Advance of `next_message_number` (when appending)
4. Commit

Failed appends **must not** advance `next_message_number`.

## Area selection

Area codes are compared case-insensitively (`COLLATE NOCASE` on `areas.code`).

API-level helpers may omit an area code only when the database contains exactly
one area. Multi-area databases require an explicit area code. That selection
rule is application policy; the tables themselves do not encode it.

## DDL

The following statements define schema version 1. Implementations that create a
new database **must** produce an equivalent schema (same tables, columns, types,
constraints, indexes and triggers). Table and column names are case-insensitive
in SQLite; use the spellings below for portability.

```sql
PRAGMA application_id = 1195783251;  -- 0x47465453
PRAGMA user_version = 1;

CREATE TABLE areas (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('netmail', 'echomail')),
    sort_order INTEGER NOT NULL DEFAULT 0,
    meta_key TEXT,
    origin_address TEXT
        CHECK (
            origin_address IS NULL
            OR (origin_address != '' AND origin_address = trim(origin_address))
        ),
    next_message_number INTEGER NOT NULL DEFAULT 1
        CHECK (next_message_number > 0)
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
    from_address TEXT
        CHECK (
            from_address IS NULL
            OR (from_address != '' AND from_address = trim(from_address))
        ),
    to_address TEXT
        CHECK (
            to_address IS NULL
            OR (to_address != '' AND to_address = trim(to_address))
        ),
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
    AND (NEW.from_address IS NULL OR NEW.to_address IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'netmail requires origin and destination addresses');
END;

CREATE TRIGGER messages_netmail_addresses_update
BEFORE UPDATE OF area_id, from_address, to_address ON messages
WHEN (SELECT kind FROM areas WHERE id = NEW.area_id) = 'netmail'
    AND (NEW.from_address IS NULL OR NEW.to_address IS NULL)
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
```

All tables are `STRICT`. Child tables use `WITHOUT ROWID`.

## FTN address text

Addresses are stored as a single canonical `TEXT` value and mapped to/from a
structured DTO in application code. Absent address is SQL `NULL`, never an empty
string. Present values must have no surrounding whitespace (enforced by CHECK).

Canonical grammar:

```text
address = zone ":" net "/" node [ "." point ] [ "@" domain ]
zone, net, node, point = unsigned decimal digits (no sign, no spaces)
domain = [A-Za-z0-9] [A-Za-z0-9._-]*
```

Examples: `2:230/0`, `1:105/42.3`, `2:236/77.1@fidonet`.

Writers **must** store the canonical form (no leading zeros required beyond the
literal digits, no spaces, point omitted when absent, domain omitted when
absent). Readers that expose a structured address type **must** parse this
grammar; fine-grained validation beyond “non-empty trimmed text” is an
application concern. SQLite triggers only require netmail `from_address` and
`to_address` to be non-NULL.

## Tables

### `areas`

Catalog of netmail and echomail areas inside the database.

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | INTEGER | Surrogate primary key |
| `code` | TEXT | Area identifier; unique, case-insensitive |
| `name` | TEXT | Display name |
| `kind` | TEXT | `'netmail'` or `'echomail'` |
| `sort_order` | INTEGER | UI / listing order hint; default `0` |
| `meta_key` | TEXT | Optional opaque grouping key for clients |
| `origin_address` | TEXT | Optional default origin; see address grammar |
| `next_message_number` | INTEGER | Next `msgno` to allocate; starts at `1` |

**Allocator.** On append, take `msgno = next_message_number`, insert the message,
then set `next_message_number = msgno + 1` in the same transaction. Never reuse a
number after soft delete.

### `messages`

One row per stored message, including soft-deleted ones.

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | INTEGER | Surrogate primary key; stable for child FKs |
| `area_id` | INTEGER | Owning area |
| `msgno` | INTEGER | Area-local message number; unique with `area_id` |
| `from_name`, `to_name`, `subject`, `body_text` | TEXT | Unicode message content; never NULL |
| `posted_at` | TEXT | Optional timestamp as ISO 8601 text |
| `external_id` | TEXT | Optional network-visible FTN MSGID (data, not identity) |
| `attributes_raw` | INTEGER | Opaque FTN attribute bitfield; never NULL |
| `from_address` | TEXT | Optional origin address; see address grammar |
| `to_address` | TEXT | Optional destination address; see address grammar |
| `reply_to_msgno` | INTEGER | Optional reply-to message number |
| `reply1st_msgno` | INTEGER | Optional first-reply message number |
| `reply_next_msgno` | INTEGER | Optional next-reply message number |
| `source_type` … `source_offset` | TEXT / INTEGER | Optional import provenance |
| `revision` | INTEGER | Monotonic row revision; starts at `1` |
| `deleted_at` | TEXT | Soft-delete marker; NULL means live |

**Identity.** The durable public identity of a message is
`(database file, area code, msgno)`. The surrogate `id` is an implementation
detail for foreign keys and must not be treated as the network or user-facing
message identity.

**Live messages.** Ordinary readers list rows where `deleted_at IS NULL`.

**Soft delete.** Set `deleted_at` to an ISO 8601 timestamp and increment
`revision`. Do not delete the row or recycle `msgno`. Child rows remain unless a
hard delete of the message row cascades them.

**Timestamps.** `posted_at` and `deleted_at` store ISO 8601 strings suitable for
`datetime.fromisoformat` / equivalent parsers (for example
`2026-10-23T12:34:56+00:00`). Prefer UTC with an explicit offset.

**Addresses.** Netmail inserts and updates with a NULL `from_address` or
`to_address` are aborted by the triggers above. Echomail may omit either
address. Area `origin_address` may supply the netmail origin when the message
row omits `from_address` at the API layer; the stored message row still carries
the resolved text.

**Provenance.** If `source_type` is NULL, then `source_path`, `source_id` and
`source_offset` must all be NULL. When present, `source_type` names the import
source (for example `jam`, `msg`, `squish`). `source_path` / `source_id` /
`source_offset` are opaque to the format.

**Attributes.** `attributes_raw` is stored as an integer bitfield. The format
does not decode individual bits; clients share FTN attribute conventions outside
this schema.

### `control_lines`

Ordered FTN control / kludge lines for one message.

| Column | Type | Meaning |
| --- | --- | --- |
| `message_id` | INTEGER | Parent `messages.id` |
| `position` | INTEGER | Zero-based order within the message |
| `name` | TEXT | Kludge name; case-insensitive |
| `value` | TEXT | Parsed value without the `^Aname:` prefix |
| `raw` | TEXT | Exact stored line, including leading SOH (`0x01`) when present |

Primary key: `(message_id, position)`. Positions for a message **must** be dense
starting at `0` when written by a conforming writer (`0 .. n-1`). Readers **must**
order by `position`.

Repeated names and unknown names are retained as separate rows. Do not collapse
this table into a JSON object keyed by name.

### `routing_entries`

Ordered `SEEN-BY` and `PATH` fragments.

| Column | Type | Meaning |
| --- | --- | --- |
| `message_id` | INTEGER | Parent `messages.id` |
| `kind` | TEXT | `'seen_by'` or `'path'` |
| `position` | INTEGER | Zero-based order within that kind |
| `value` | TEXT | One routing token / fragment string |

Primary key: `(message_id, kind, position)`. Order is independent per `kind`.
Writers that replace routing for one kind delete existing rows of that kind and
re-insert positions from `0`.

Values are opaque strings as received from the FTN source (for example
`230/0 1`). The format does not expand or normalize node lists.

### `reply_list_entries`

Ordered extra reply-list message numbers beyond the three scalar reply columns.

| Column | Type | Meaning |
| --- | --- | --- |
| `message_id` | INTEGER | Parent `messages.id` |
| `position` | INTEGER | Zero-based order |
| `msgno` | INTEGER | Referenced message number; must be > 0 |

Primary key: `(message_id, position)`.

## Message number allocation

Pseudocode for a conforming append:

```text
BEGIN IMMEDIATE
  area := SELECT * FROM areas WHERE code = ?   -- or the sole area
  msgno := area.next_message_number
  INSERT INTO messages (..., msgno, revision, ...) VALUES (..., msgno, 1, ...)
  INSERT child rows for control_lines / routing_entries / reply_list_entries
  UPDATE areas SET next_message_number = msgno + 1 WHERE id = area.id
COMMIT
```

Invariants:

- `msgno` is unique within `area_id`.
- Soft-deleted rows keep their `msgno`.
- A rolled-back append leaves `next_message_number` unchanged.
- Concurrent appends that use `BEGIN IMMEDIATE` allocate each number once.

## Updates and optimistic concurrency

Every successful update or soft-delete of a live message **must** increment
`messages.revision` by 1.

Clients that expose compare-and-swap editing **must** treat the stored message
plus its ordered child rows as one logical unit. A change to any of the following
invalidates a previously observed revision for that message:

- any column of the `messages` row
- any `control_lines` row for that message
- any `routing_entries` row for that message
- any `reply_list_entries` row for that message

The Python reference encodes that unit as a `RevisionToken` whose `location` is
`(messages.id, messages.revision)` and whose digest covers the message row and
the ordered child rows. Other implementations may use an equivalent token, but
they **must** reject stale updates when any of those rows changed, including
changes made by a direct SQL client.

Updates that replace a child sequence **must** delete the previous rows for that
sequence and re-insert them with dense positions from `0` in the same
transaction as the `messages` update.

## Text, charset and body

- All `TEXT` columns are Unicode (SQLite UTF-8 storage).
- `body_text` stores the message body as Unicode text. Tearline / origin parsing
  is a reader concern; the format stores the body as given.
- Control-line `raw` preserves the on-wire form when available. Prefer keeping
  the leading SOH byte in `raw` when the source had one.

## Indexes

| Index | Purpose |
| --- | --- |
| `messages_area_posted` | Live messages by area, post time, then `msgno` |
| `messages_external_id` | Lookup by MSGID among live messages |
| `control_lines_name` | Find messages by kludge name |

Partial indexes exclude soft-deleted messages where noted.

## Backup and copy

Do not copy an open WAL database as a lone `.sqlite3` file. Use SQLite's backup
API, or close and checkpoint first. A copy of only the main file may omit
committed pages still present in the WAL.

A complete backup of a live WAL database includes the main file plus matching
`-wal` and `-shm` sidecars, or a consistent copy produced by the backup API.

## Minimal implementor checklist

A new reader is conforming for schema version 1 when it:

1. Rejects wrong `application_id` or `user_version`
2. Enables foreign keys
3. Reads areas and live messages (`deleted_at IS NULL`)
4. Joins child tables ordered by `position` (and by `kind` for routing)
5. Treats `(area code, msgno)` as identity, not `messages.id`

A new writer is conforming when it also:

1. Creates the DDL above (or opens only validated databases)
2. Allocates `msgno` under `BEGIN IMMEDIATE` as specified
3. Enforces netmail `from_address` / `to_address` non-NULL and stores canonical address text
4. Stores ordered control, routing and reply-list rows without collapsing duplicates
5. Soft-deletes by setting `deleted_at` and never reuses numbers
6. Increments `revision` on every successful update or soft-delete
7. Rejects stale compare-and-swap edits against the full logical message unit

## Non-goals of version 1

Schema version 1 does **not** define:

- last-read / user state
- attachment blobs
- tosser / dupe databases
- charset conversion rules beyond Unicode storage
- bit meanings inside `attributes_raw`

Those may appear in later versions or in companion formats.
