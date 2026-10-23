# Schema version 1

The database carries SQLite application ID `0x47465453` and `user_version = 1`.
Readers reject databases where either value differs.

`areas` defines netmail and echomail areas. `next_message_number` allocates positive,
monotonic numbers inside a `BEGIN IMMEDIATE` transaction. `(area_id, msgno)` is
unique, and a soft-deleted number is never returned to the allocator.

`messages` stores Unicode names, subject and body, ISO 8601 timestamps, canonical
address components, attributes, reply links, optional import provenance and a
revision counter. Address checks require zone, net and node together; point and
domain remain optional.

`control_lines`, `routing_entries` and `reply_list_entries` use explicit positions.
Unknown and repeated kludges therefore survive without being collapsed into a JSON
object. `routing_entries.kind` distinguishes `seen_by` from `path` while retaining
order inside each sequence.

The database uses strict tables, foreign keys and WAL journaling. Writers serialize
allocation and mutation with `BEGIN IMMEDIATE`; readers retain SQLite snapshot
semantics. Copy an open database with SQLite's backup API. A copy of only the main
file may omit committed pages still present in the WAL.

The schema is a public format boundary. Changing existing columns, constraints or
meaning requires a migration and a new `user_version`; silently reinterpreting
version 1 is not allowed.
