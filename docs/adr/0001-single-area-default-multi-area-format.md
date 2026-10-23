# Single-area default, multi-area format

Schema version 1 permits several areas in one SQLite database, while documentation and automatic area selection treat one area per file as the normal layout. This keeps GoldED-style configuration, backup and failure isolation simple without baking that operational choice into the format; servers and tossers can group areas later without a migration.
