# Security

Treat SQLite message bases as untrusted input. Open only databases with the expected
application ID and schema version, and keep foreign-key validation enabled for writes.

Report vulnerabilities privately through GitHub security advisories once the public
repository exists. Do not attach private message bases, message contents, credentials
or access material to public reports.

WAL databases may have live `-wal` and `-shm` sidecars. Use SQLite's backup API for
a consistent copy while writers are active.
