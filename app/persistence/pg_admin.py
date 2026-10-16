"""P7.4: PostgreSQL administration for least-privilege operation.

Two roles (created by the database administrator, outside MEDRIXA):
- an OWNER/migration role that owns the MEDRIXA tables and runs migrations;
- an APPLICATION role (``LOGIN``, no ownership, no ``CREATE``) used by the API.

``grant_application_privileges`` gives the application role exactly:
  medrixa_runs               SELECT, INSERT, UPDATE   (no DELETE, no TRUNCATE)
  medrixa_run_events         SELECT, INSERT           (append-only, also enforced by triggers)
  medrixa_run_leases         SELECT, INSERT, UPDATE
  medrixa_schema_migrations  SELECT                   (schema version check)
Because it does not own the tables it cannot ALTER them, disable the
append-only triggers or drop anything.

Command line (DSNs are secrets: MEDRIXA_PG_ADMIN_DSN or MEDRIXA_PG_ADMIN_DSN_FILE):
    python -m app.persistence.pg_admin migrate
    python -m app.persistence.pg_admin grant --role medrixa_app
"""
import argparse
import re
import sys

from app.persistence.postgres import PostgresRunLeases, PostgresStateRepository
from app.persistence.repository import RepositoryError
from app.security.secrets import EnvironmentSecretProvider, SecretError

ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
APPLICATION_PRIVILEGES = (
    ("medrixa_runs", ("SELECT", "INSERT", "UPDATE")),
    ("medrixa_run_events", ("SELECT", "INSERT")),
    ("medrixa_run_leases", ("SELECT", "INSERT", "UPDATE")),
    ("medrixa_schema_migrations", ("SELECT",)),
)


def migrate(owner_dsn: str) -> int:
    """Apply all migrations with the owner role; returns the schema version."""
    PostgresStateRepository(owner_dsn, migrate=True)
    return PostgresRunLeases(owner_dsn, migrate=True).migrate()


def grant_application_privileges(owner_dsn: str, role: str) -> None:
    """Idempotent: revoke everything on MEDRIXA tables from ``role``, then grant the minimum."""
    if not ROLE_NAME.match(role):
        raise ValueError("invalid role name")
    from psycopg import sql
    repository = PostgresStateRepository(owner_dsn, migrate=False, verify=False)
    repository.require_schema(2)

    def apply(cursor):
        identifier = sql.Identifier(role)
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(identifier))
        for table, privileges in APPLICATION_PRIVILEGES:
            cursor.execute(sql.SQL("REVOKE ALL ON TABLE {} FROM {}").format(sql.Identifier(table), identifier))
            cursor.execute(sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                sql.SQL(", ").join(sql.SQL(p) for p in privileges), sql.Identifier(table), identifier))
    repository._run(apply)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.persistence.pg_admin",
                                     description="MEDRIXA PostgreSQL administration (owner role)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Apply schema migrations")
    grant = commands.add_parser("grant", help="Grant least privilege to the application role")
    grant.add_argument("--role", required=True)
    args = parser.parse_args(argv)
    try:
        dsn = EnvironmentSecretProvider().get("PG_ADMIN_DSN")
    except SecretError as error:
        print(f'{{"error":"CONFIGURATION","detail":"{error}"}}', file=sys.stderr)
        return 2
    if dsn is None:
        print('{"error":"CONFIGURATION","detail":"MEDRIXA_PG_ADMIN_DSN[_FILE] is required"}', file=sys.stderr)
        return 2
    try:
        if args.command == "migrate":
            print(f'{{"schema_version":{migrate(dsn.get_secret_value())}}}')
        else:
            grant_application_privileges(dsn.get_secret_value(), args.role)
            print('{"granted":true}')
    except ValueError:
        print('{"error":"INVALID_INPUT"}', file=sys.stderr)
        return 2
    except RepositoryError:
        print('{"error":"STORE_UNAVAILABLE"}', file=sys.stderr)  # never the DSN or driver message
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
