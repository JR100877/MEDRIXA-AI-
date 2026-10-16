"""P7.4: PostgreSQL least privilege — application role with DML only, verified by the database."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

from app.persistence.pg_admin import grant_application_privileges, migrate
from app.persistence.postgres import PostgresRunLeases, PostgresStateRepository, SchemaNotReady, StoreUnavailable
from tests.persistence.pg_support import as_role, create_application_role, fresh_database_dsn
from tests.persistence.support import state_chain
from tests.persistence.test_leases import LeaseContract
from tests.persistence.test_repository_contract import RepositoryContract

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def provisioned():
    """Fresh database migrated by its owner, plus an application role with least privilege."""
    owner = fresh_database_dsn()
    migrate(owner)
    role = create_application_role()
    grant_application_privileges(owner, role)
    return owner, as_role(owner, role), role


class AppRoleRepositoryContractTests(RepositoryContract, unittest.TestCase):
    """The unchanged P4 contract suite, executed by the least-privilege role."""

    def make_repo(self):
        _, app_dsn, _ = provisioned()
        return PostgresStateRepository(app_dsn, migrate=False)

    def peer(self, repo):
        return PostgresStateRepository(repo._dsn, migrate=False)


class AppRoleLeaseContractTests(LeaseContract, unittest.TestCase):
    def make_leases(self):
        _, app_dsn, _ = provisioned()
        return PostgresRunLeases(app_dsn, migrate=False)

    def peer(self, leases):
        return PostgresRunLeases(leases._db._dsn, migrate=False)


class PrivilegeBoundaryTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        self.psycopg = psycopg
        self.owner, self.app_dsn, self.role = provisioned()
        self.repo = PostgresStateRepository(self.app_dsn, migrate=False)
        chain, _ = state_chain()
        self.repo.create(chain[0])
        self.repo.save(chain[1], 0)
        self.run_id = chain[0].run_id
        self.expected = chain[1]

    def as_app(self, statement):
        with self.psycopg.connect(self.app_dsn, autocommit=True) as connection:
            connection.execute(statement)

    def test_database_refuses_everything_outside_the_grant(self):
        forbidden = {
            "delete_runs": "DELETE FROM medrixa_runs",
            "truncate_runs": "TRUNCATE medrixa_runs",
            "update_events": "UPDATE medrixa_run_events SET kind = 'RUN_STARTED'",
            "delete_events": "DELETE FROM medrixa_run_events",
            "truncate_events": "TRUNCATE medrixa_run_events",
            "disable_triggers": "ALTER TABLE medrixa_run_events DISABLE TRIGGER USER",
            "drop_table": "DROP TABLE medrixa_run_events",
            "alter_table": "ALTER TABLE medrixa_runs ADD COLUMN x int",
            "write_migrations": "INSERT INTO medrixa_schema_migrations (version) VALUES (99)",
            "create_table": "CREATE TABLE medrixa_shadow (x int)",
            "replace_trigger_function": "CREATE OR REPLACE FUNCTION medrixa_events_append_only() RETURNS trigger "
                                        "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
            "delete_leases": "DELETE FROM medrixa_run_leases",
        }
        for name, statement in forbidden.items():
            with self.subTest(name), self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                self.as_app(statement)
        self.assertEqual(self.repo.load(self.run_id), self.expected)

    def test_application_role_cannot_regrant(self):
        # PostgreSQL does not raise here: without GRANT OPTION it only warns
        # "no privileges were granted". The effect is what matters: nothing granted.
        self.as_app("GRANT SELECT, DELETE ON medrixa_runs TO PUBLIC")
        with self.psycopg.connect(self.owner) as connection:
            leaked = connection.execute(
                "SELECT count(*) FROM information_schema.role_table_grants "
                "WHERE grantee = 'PUBLIC' AND table_name LIKE 'medrixa_%'").fetchone()[0]
        self.assertEqual(leaked, 0)

    def test_application_role_cannot_migrate(self):
        with self.assertRaises(StoreUnavailable):
            PostgresStateRepository(self.app_dsn, migrate=True)
        with self.assertRaises(StoreUnavailable):
            PostgresRunLeases(self.app_dsn, migrate=True)

    def test_grants_match_the_declared_minimum(self):
        with self.psycopg.connect(self.owner) as connection:
            rows = connection.execute(
                "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
                "WHERE grantee = %s ORDER BY 1, 2", (self.role,)).fetchall()
        granted = {}
        for table, privilege in rows:
            granted.setdefault(table, set()).add(privilege)
        # Independent specification (not derived from the code under test):
        self.assertEqual(granted, {"medrixa_runs": {"SELECT", "INSERT", "UPDATE"},
                                   "medrixa_run_events": {"SELECT", "INSERT"},
                                   "medrixa_run_leases": {"SELECT", "INSERT", "UPDATE"},
                                   "medrixa_schema_migrations": {"SELECT"}})

    def test_grant_is_idempotent_and_revokes_excess(self):
        with self.psycopg.connect(self.owner, autocommit=True) as connection:
            connection.execute(f'GRANT DELETE, TRUNCATE ON medrixa_runs TO "{self.role}"')
        grant_application_privileges(self.owner, self.role)
        grant_application_privileges(self.owner, self.role)
        with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
            self.as_app("DELETE FROM medrixa_runs")

    def test_invalid_role_names_rejected(self):
        for role in ("Robert'); DROP TABLE medrixa_runs;--", "UPPER", "a-b", "", "x" * 64):
            with self.subTest(role=role[:20]), self.assertRaises(ValueError):
                grant_application_privileges(self.owner, role)

    def test_unmigrated_or_outdated_schema_fails_closed(self):
        empty = fresh_database_dsn()
        with self.assertRaises(SchemaNotReady):
            PostgresStateRepository(empty, migrate=False)
        PostgresStateRepository(empty, migrate=True)            # v1 only
        with self.assertRaises(SchemaNotReady):
            PostgresRunLeases(empty, migrate=False)              # leases need v2
        PostgresStateRepository(empty, migrate=False)            # v1 is enough for runs


class AdminCliTests(unittest.TestCase):
    def run_admin(self, *args, **env):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("MEDRIXA_")}
        environment.update(env)
        return subprocess.run([sys.executable, "-m", "app.persistence.pg_admin", *args], cwd=PROJECT_ROOT,
                              env=environment, capture_output=True, text=True, timeout=120)

    def test_migrate_and_grant_through_cli_then_serve_with_app_role(self):
        owner = fresh_database_dsn()
        role = create_application_role()
        migrated = self.run_admin("migrate", MEDRIXA_PG_ADMIN_DSN=owner)
        self.assertEqual((migrated.returncode, migrated.stdout.strip()), (0, '{"schema_version":2}'), migrated.stderr)
        granted = self.run_admin("grant", "--role", role, MEDRIXA_PG_ADMIN_DSN=owner)
        self.assertEqual(granted.returncode, 0, granted.stderr)
        self.assertNotIn("host=", granted.stdout + granted.stderr)
        self.assertEqual(self.run_admin("migrate").returncode, 2)
        bad = self.run_admin("migrate", MEDRIXA_PG_ADMIN_DSN="host=/nonexistent dbname=x password=SECRET_MARKER")
        self.assertEqual((bad.returncode, bad.stderr.strip()), (1, '{"error":"STORE_UNAVAILABLE"}'))

        from tests.web.support import case_body
        from fastapi.testclient import TestClient
        from app.web.app import create_app
        from app.web.audit import MemoryAuditSink
        from app.web.auth import issue_token
        from app.web.settings import Settings
        secret = "least-privilege-http-secret-0123456789-abcdef"
        settings = Settings(environment="test", auth_secret=secret, postgres_dsn=as_role(owner, role),
                            postgres_migrate=False, allowed_hosts=("testserver",))
        client = TestClient(create_app(settings, audit=MemoryAuditSink()))
        headers = {"Authorization": f"Bearer {issue_token(secret, 'u1', ['case_runner'], ['case-a'])}",
                   "Idempotency-Key": "least-priv-key-000001"}
        response = client.post("/v1/runs", json=case_body(), headers=headers)
        self.assertEqual((response.status_code, response.json()["stop_reason"]), (201, "REVIEW_REQUIRED"))


if __name__ == "__main__":
    unittest.main()
