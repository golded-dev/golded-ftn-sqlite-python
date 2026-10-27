# Changelog

## 1.1.0 — 2026-10-27

- Rename reader, writer and session area_code parameters and attributes to area.
- Return area metadata through ParsedMessage.area.
- Close database connections after create, add_area and areas operations.
- Require golded-ftn 1.5.0 or newer.

## 1.0.0 — 2026-10-25

- Add schema version 1 for single- and multi-area SQLite message bases.
- Add netmail and echomail reading, transactional append, full update and soft delete.
- Preserve addresses, ordered controls, routing, reply metadata and import provenance.
- Add optimistic revisions covering message and related metadata rows.
