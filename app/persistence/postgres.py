"""PostgreSQL adapter for StateRepository (P5).

Satisfies the same contract suite as the provisional adapters
(tests/persistence/test_repository_contract.py). Requires ``psycopg`` 3
(requirements-postgres.txt); imported lazily so the core never depends on it.

Storage model (migration v1, idempotent and non-destructive):
- ``medrixa_runs``: one row per run. The snapshot is stored as canonical JSON
  TEXT (not JSONB, which renormalises numbers and would break the checksum)
  plus SHA-256 and indexed columns (case_id, revision, status).
- ``medrixa_run_events``: one row per audit event, PK (run_id, sequence),
  append-only: UPDATE, DELETE and TRUNCATE are rejected by triggers.

``save`` runs in one transaction: ``SELECT ... FOR UPDATE`` -> ``check_successor``
-> ``UPDATE ... WHERE revision = expected`` -> INSERT of the new events. Every
read revalidates checksum, State, indexed columns and the event table.

Not provided here: connection pooling, TLS configuration, role provisioning,
backups, encryption at rest. Credentials come only from the environment and
never appear in exceptions.
"""
import hashlib
import json

from pydantic import ValidationError

from app.core.state import State
from app.persistence.leases import _IDENTIFIER, Lease, LeaseHeld, LeaseLost, RunLeases, check_ids, check_ttl
from app.persistence.repository import (CorruptRecord, DuplicateRun, RepositoryError, RunIndexEntry, RunNotFound,
                                        StateRepository, check_new_run, check_successor, validate_run_id)

ADAPTER_NAME = "postgresql"
SCHEMA_VERSION = 1

MIGRATION_V1 = """
CREATE TABLE IF NOT EXISTS medrixa_schema_migrations (
    version integer PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS medrixa_runs (
    run_id text PRIMARY KEY CHECK (char_length(run_id) BETWEEN 1 AND 80),
    case_id text NOT NULL CHECK (char_length(case_id) BETWEEN 1 AND 80),
    revision integer NOT NULL CHECK (revision >= 0),
    status text NOT NULL CHECK (status IN ('created', 'running', 'awaiting_human_review')),
    snapshot text NOT NULL,
    snapshot_sha256 text NOT NULL CHECK (snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS medrixa_runs_case_idx ON medrixa_runs (case_id);
CREATE TABLE IF NOT EXISTS medrixa_run_events (
    run_id text NOT NULL REFERENCES medrixa_runs (run_id) ON DELETE RESTRICT,
    sequence integer NOT NULL CHECK (sequence >= 1),
    kind text NOT NULL CHECK (kind IN ('RUN_STARTED', 'AGENT_DISPATCHED', 'RESULT_ACCEPTED', 'REVIEW_REQUESTED')),
    at timestamptz NOT NULL,
    message_id text,
    reason text,
    PRIMARY KEY (run_id, sequence)
);
CREATE OR REPLACE FUNCTION medrixa_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'medrixa_run_events is append-only' USING ERRCODE = 'insufficient_privilege';
END $$;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'medrixa_events_no_update_delete') THEN
        CREATE TRIGGER medrixa_events_no_update_delete BEFORE UPDATE OR DELETE ON medrixa_run_events
            FOR EACH ROW EXECUTE FUNCTION medrixa_events_append_only();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'medrixa_events_no_truncate') THEN
        CREATE TRIGGER medrixa_events_no_truncate BEFORE TRUNCATE ON medrixa_run_events
            FOR EACH STATEMENT EXECUTE FUNCTION medrixa_events_append_only();
    END IF;
END $$;
INSERT INTO medrixa_schema_migrations (version) VALUES (1) ON CONFLICT (version) DO NOTHING;
"""


MIGRATION_V2_LEASES = """
CREATE TABLE IF NOT EXISTS medrixa_run_leases (
    run_id text PRIMARY KEY CHECK (char_length(run_id) BETWEEN 1 AND 80),
    owner text NOT NULL CHECK (char_length(owner) BETWEEN 1 AND 80),
    epoch integer NOT NULL CHECK (epoch >= 1),
    expires_at timestamptz NOT NULL
);
INSERT INTO medrixa_schema_migrations (version) VALUES (2) ON CONFLICT (version) DO NOTHING;
"""


class SchemaNotReady(RepositoryError):
    """The database schema is missing or older than this code requires. Run the
    migration with the owner role (``python -m app.persistence.pg_admin migrate``)."""


class StoreUnavailable(RepositoryError):
    """Database unreachable or failing. Never carries the DSN or driver message."""


def _canonical(state: State) -> str:
    return json.dumps(state.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class PostgresStateRepository(StateRepository):
    adapter_name = ADAPTER_NAME

    def __init__(self, dsn: str, *, migrate: bool = True, connect_timeout: int = 5, verify: bool = True,
                 pool_size: int = 0):
        """``migrate=True`` (development/test default) applies migrations with this DSN's role.
        With least privilege (P7.4) the application role cannot migrate: pass
        ``migrate=False`` and the schema version is verified instead (fail closed).
        ``pool_size > 0`` (P7.6) reuses connections through ``psycopg_pool``; 0 opens one
        connection per operation. Call ``close()`` when done with a pooled repository."""
        try:
            import psycopg
        except ImportError:  # pragma: no cover - exercised only without the optional extra
            raise RepositoryError("psycopg is not installed (requirements-postgres.txt)") from None
        if type(pool_size) is not int or not 0 <= pool_size <= 100:
            raise ValueError("pool_size must be an integer between 0 and 100")
        self._psycopg = psycopg
        self._dsn = dsn
        self._connect_timeout = connect_timeout
        self._pool = None
        if pool_size:
            try:
                from psycopg_pool import ConnectionPool
            except ImportError:  # pragma: no cover
                raise RepositoryError("psycopg-pool is not installed (requirements-postgres.txt)") from None
            # check_connection: one cheap round trip so a connection broken by a database
            # restart is replaced instead of failing the next request.
            self._pool = ConnectionPool(dsn, min_size=1, max_size=pool_size, open=True, timeout=connect_timeout,
                                        kwargs={"connect_timeout": connect_timeout, "autocommit": False},
                                        check=ConnectionPool.check_connection, name="medrixa")
        try:
            if migrate:
                self.migrate()
            elif verify:
                self.require_schema(1)
        except BaseException:
            self.close()  # never leave pool worker threads reconnecting after a failed start
            raise

    def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.close()

    def schema_version(self) -> int:
        def read(cursor):
            cursor.execute("SELECT to_regclass('medrixa_schema_migrations') IS NOT NULL")
            if not cursor.fetchone()[0]:
                return 0
            cursor.execute("SELECT coalesce(max(version), 0) FROM medrixa_schema_migrations")
            return int(cursor.fetchone()[0])
        return self._run(read)

    def ping(self) -> None:
        """Readiness: database reachable and schema migrated (one round trip)."""
        self.require_schema(1)

    def require_schema(self, minimum: int) -> None:
        if self.schema_version() < minimum:
            raise SchemaNotReady("database schema not migrated to the required version")

    def __repr__(self) -> str:  # never expose the DSN
        return f"{type(self).__name__}(adapter={self.adapter_name!r})"

    # -- infrastructure --------------------------------------------------------
    def _connect(self):
        try:
            return self._psycopg.connect(self._dsn, connect_timeout=self._connect_timeout, autocommit=False)
        except self._psycopg.Error:
            raise StoreUnavailable("database unavailable") from None

    def _run(self, operation):
        """Execute ``operation(cursor)`` in one transaction (commit on success, rollback on
        any exception); map driver errors. Pooled or one connection per operation."""
        try:
            if self._pool is not None:
                with self._pool.connection(timeout=self._connect_timeout) as connection:
                    with connection.cursor() as cursor:
                        return operation(cursor)
            connection = self._connect()
            try:
                with connection:
                    with connection.cursor() as cursor:
                        return operation(cursor)
            finally:
                connection.close()
        except RepositoryError:
            raise
        except self._psycopg.errors.UniqueViolation:
            raise DuplicateRun("run already exists") from None
        except self._psycopg.Error:  # includes psycopg_pool.PoolTimeout
            raise StoreUnavailable("database operation failed") from None

    def migrate(self) -> int:
        def apply(cursor):
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('medrixa_schema_migrations'))")
            cursor.execute(MIGRATION_V1)
            cursor.execute("SELECT max(version) FROM medrixa_schema_migrations")
            return cursor.fetchone()[0]
        return self._run(apply)

    # -- record verification ---------------------------------------------------
    @staticmethod
    def _verify(row, events, expected_run_id: str | None) -> State:
        try:
            run_id, case_id, revision, status, snapshot, sha = row
            if _sha(snapshot) != sha:
                raise ValueError("checksum")
            state = State.model_validate_json(snapshot)
            if _canonical(state) != snapshot:
                raise ValueError("non-canonical snapshot")
            if (state.run_id != run_id or state.case.case_id != case_id or state.revision != revision
                    or state.status != status or (expected_run_id is not None and run_id != expected_run_id)):
                raise ValueError("indexed columns disagree with snapshot")
            stored_events = [(e.sequence, e.kind, e.at, e.message_id, e.reason) for e in state.events]
            if [tuple(event) for event in events] != stored_events:
                raise ValueError("event table disagrees with snapshot")
            return state
        except (ValueError, TypeError, ValidationError):
            raise CorruptRecord("unreadable run record") from None

    _SELECT_RUN = ("SELECT run_id, case_id, revision, status, snapshot, snapshot_sha256 FROM medrixa_runs "
                   "WHERE run_id = %s")
    _SELECT_RUN_FOR_UPDATE = _SELECT_RUN + " FOR UPDATE"  # constants only; values always bound as parameters

    @classmethod
    def _select(cls, cursor, run_id: str, lock: bool):
        cursor.execute(cls._SELECT_RUN_FOR_UPDATE if lock else cls._SELECT_RUN, (run_id,))
        row = cursor.fetchone()
        if row is None:
            return None, None
        cursor.execute("SELECT sequence, kind, at, message_id, reason FROM medrixa_run_events "
                       "WHERE run_id = %s ORDER BY sequence", (run_id,))
        return row, cursor.fetchall()

    @staticmethod
    def _insert_events(cursor, state: State, start: int) -> None:
        for event in state.events[start:]:
            cursor.execute("INSERT INTO medrixa_run_events (run_id, sequence, kind, at, message_id, reason) "
                           "VALUES (%s, %s, %s, %s, %s, %s)",
                           (state.run_id, event.sequence, event.kind, event.at, event.message_id, event.reason))

    # -- port --------------------------------------------------------------------
    def create(self, state: State) -> State:
        state = check_new_run(state)
        snapshot = _canonical(state)

        def insert(cursor):
            cursor.execute("INSERT INTO medrixa_runs (run_id, case_id, revision, status, snapshot, snapshot_sha256) "
                           "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (run_id) DO NOTHING",
                           (state.run_id, state.case.case_id, state.revision, state.status, snapshot, _sha(snapshot)))
            if cursor.rowcount != 1:
                raise DuplicateRun("run already exists")
            return state
        return self._run(insert)

    def load(self, run_id: str) -> State:
        run_id = validate_run_id(run_id)

        def read(cursor):
            row, events = self._select(cursor, run_id, lock=False)
            if row is None:
                raise RunNotFound("run not found")
            return self._verify(row, events, run_id)
        return self._run(read)

    def save(self, state: State, expected_revision: int) -> State:
        state = State.model_validate(state)

        def update(cursor):
            row, events = self._select(cursor, state.run_id, lock=True)
            if row is None:
                raise RunNotFound("run not found")
            stored = self._verify(row, events, state.run_id)
            candidate = check_successor(stored, state, expected_revision)
            snapshot = _canonical(candidate)
            cursor.execute("UPDATE medrixa_runs SET revision = %s, status = %s, snapshot = %s, snapshot_sha256 = %s, "
                           "updated_at = now() WHERE run_id = %s AND revision = %s",
                           (candidate.revision, candidate.status, snapshot, _sha(snapshot),
                            candidate.run_id, stored.revision))
            if cursor.rowcount != 1:  # defensive: the row lock already serialises writers
                raise RepositoryError("compare-and-swap lost")
            self._insert_events(cursor, candidate, len(stored.events))
            return candidate
        return self._run(update)

    def scan(self) -> tuple[RunIndexEntry, ...]:
        def run_ids(cursor):
            cursor.execute("SELECT run_id FROM medrixa_runs ORDER BY run_id")
            return [r[0] for r in cursor.fetchall()]
        entries = []
        for run_id in self._run(run_ids):
            try:
                state = self.load(run_id)
            except (CorruptRecord, RunNotFound):  # StoreUnavailable propagates: never report "empty"
                entries.append(RunIndexEntry(key=_sha(run_id), readable=False))
                continue
            entries.append(RunIndexEntry(key=state.run_id, run_id=state.run_id, case_id=state.case.case_id,
                                         revision=state.revision, status=state.status, readable=True))
        return tuple(entries)


class PostgresRunLeases(RunLeases):
    """P6: RunLeases on PostgreSQL. Expiry uses the DATABASE clock (clock_timestamp()),
    so executors on different hosts agree on liveness. Each operation is one atomic
    statement; table created by migration v2 (additive, idempotent)."""

    adapter_name = ADAPTER_NAME

    _ACQUIRE = ("INSERT INTO medrixa_run_leases (run_id, owner, epoch, expires_at) "
                "VALUES (%s, %s, 1, clock_timestamp() + make_interval(secs => %s)) "
                "ON CONFLICT (run_id) DO UPDATE SET owner = EXCLUDED.owner, epoch = medrixa_run_leases.epoch + 1, "
                "expires_at = EXCLUDED.expires_at WHERE medrixa_run_leases.expires_at <= clock_timestamp() "
                "RETURNING run_id, owner, epoch, expires_at")
    _RENEW = ("UPDATE medrixa_run_leases SET expires_at = clock_timestamp() + make_interval(secs => %s) "
              "WHERE run_id = %s AND owner = %s AND epoch = %s AND expires_at > clock_timestamp() "
              "RETURNING run_id, owner, epoch, expires_at")
    _RELEASE = ("UPDATE medrixa_run_leases SET expires_at = clock_timestamp() "
                "WHERE run_id = %s AND owner = %s AND epoch = %s AND expires_at > clock_timestamp()")
    _LIVE = ("SELECT run_id, owner, epoch, expires_at FROM medrixa_run_leases "
             "WHERE run_id = %s AND expires_at > clock_timestamp()")

    def __init__(self, dsn: str, *, migrate: bool = True, connect_timeout: int = 5,
                 repository: "PostgresStateRepository | None" = None):
        # Reuses the repository's connection handling and error mapping (never leaks the DSN).
        # ``repository`` (P7.6) shares its connection pool instead of opening another.
        self._db = repository or PostgresStateRepository(dsn, migrate=False, connect_timeout=connect_timeout,
                                                         verify=False)
        if migrate:
            self.migrate()
        else:
            self._db.require_schema(2)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(adapter={self.adapter_name!r})"

    def migrate(self) -> int:
        def apply(cursor):
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('medrixa_schema_migrations'))")
            cursor.execute(MIGRATION_V1)
            cursor.execute(MIGRATION_V2_LEASES)
            cursor.execute("SELECT max(version) FROM medrixa_schema_migrations")
            return cursor.fetchone()[0]
        return self._db._run(apply)

    @staticmethod
    def _lease(row):
        return Lease(run_id=row[0], owner=row[1], epoch=row[2], expires_at=row[3])

    def acquire(self, run_id, owner, ttl_seconds):
        run_id, owner = check_ids(run_id, owner)
        ttl = check_ttl(ttl_seconds)
        row = self._db._run(lambda c: c.execute(self._ACQUIRE, (run_id, owner, ttl)).fetchone())
        if row is None:
            raise LeaseHeld("lease is held")
        return self._lease(row)

    def renew(self, lease, ttl_seconds):
        lease = Lease.model_validate(lease)
        ttl = check_ttl(ttl_seconds)
        row = self._db._run(lambda c: c.execute(self._RENEW, (ttl, lease.run_id, lease.owner, lease.epoch)).fetchone())
        if row is None:
            raise LeaseLost("lease lost")
        return self._lease(row)

    def release(self, lease):
        lease = Lease.model_validate(lease)
        self._db._run(lambda c: c.execute(self._RELEASE, (lease.run_id, lease.owner, lease.epoch)))

    def live(self, run_id):
        run_id = _IDENTIFIER.validate_python(run_id)
        row = self._db._run(lambda c: c.execute(self._LIVE, (run_id,)).fetchone())
        return self._lease(row) if row else None
