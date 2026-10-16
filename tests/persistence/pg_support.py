"""Ephemeral local PostgreSQL for tests (P5). Never points at a shared or real database.

Resolution order:
1. ``MEDRIXA_TEST_PG_ADMIN_DSN``: an existing *test* server where the role may
   CREATE DATABASE (one fresh database per test).
2. Otherwise a throwaway cluster: ``initdb`` in a private temporary directory,
   Unix socket only (``listen_addresses=''``), ``peer`` authentication, removed
   at exit. When tests run as root, the cluster runs as an unprivileged OS user
   (PostgreSQL refuses root) and a non-superuser LOGIN CREATEDB role is created
   for the current user.
3. If neither is possible, PostgreSQL tests are SKIPPED with the reason; they
   are never reported as passed.
"""
import atexit
import getpass
import glob
import os
import pwd
import shutil
import subprocess
import tempfile
import unittest
import uuid

_cluster = None
_failure = None


def _pg_bin() -> str | None:
    override = os.environ.get("MEDRIXA_TEST_PG_BIN")
    if override and os.path.exists(os.path.join(override, "initdb")):
        return override
    found = sorted(glob.glob("/usr/lib/postgresql/*/bin/initdb")) or ([shutil.which("initdb")] if shutil.which("initdb") else [])
    return os.path.dirname(found[-1]) if found else None


class _EphemeralCluster:
    def __init__(self, bin_dir: str):
        self.bin = bin_dir
        self.client_user = getpass.getuser()
        self.os_user = self.client_user
        if os.geteuid() == 0:
            for candidate in (os.environ.get("MEDRIXA_TEST_PG_OS_USER"), "postgres", "claude", "nobody"):
                if candidate and candidate != "root":
                    try:
                        pwd.getpwnam(candidate)
                        self.os_user = candidate
                        break
                    except KeyError:
                        continue
        self.dir = tempfile.mkdtemp(prefix="medrixa-pg-")
        os.chmod(self.dir, 0o700)
        if self.os_user != self.client_user:
            shutil.chown(self.dir, user=self.os_user)
        self.data = os.path.join(self.dir, "data")
        self._as_owner(["initdb", "-D", self.data, "-A", "peer", "-U", self.os_user, "--no-instructions",
                        "-E", "UTF8"])
        self._as_owner(["pg_ctl", "-D", self.data, "-l", os.path.join(self.dir, "server.log"), "-w", "-o",
                        f"-k {self.dir} -c listen_addresses=''", "start"])
        self.running = True
        atexit.register(self.stop)
        if self.os_user != self.client_user:
            self._as_owner(["psql", "-h", self.dir, "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-q", "-c",
                            f'CREATE ROLE "{self.client_user}" LOGIN CREATEDB'])

    def _as_owner(self, argv):
        command = [os.path.join(self.bin, argv[0]), *argv[1:]]
        if self.os_user != self.client_user:
            command = ["runuser", "-u", self.os_user, "--", *command]
        subprocess.run(command, check=True, capture_output=True, timeout=120)

    def admin_dsn(self) -> str:
        return f"host={self.dir} dbname=postgres user={self.client_user}"

    def stop(self):
        if getattr(self, "running", False):
            self.running = False
            try:
                self._as_owner(["pg_ctl", "-D", self.data, "-m", "fast", "-w", "stop"])
            finally:
                shutil.rmtree(self.dir, ignore_errors=True)


def _admin_dsn() -> str:
    global _cluster, _failure
    if os.environ.get("MEDRIXA_TEST_PG", "").lower() == "off":
        raise unittest.SkipTest("PostgreSQL tests disabled by MEDRIXA_TEST_PG=off")
    external = os.environ.get("MEDRIXA_TEST_PG_ADMIN_DSN")
    if external:
        return external
    if _cluster is None and _failure is None:
        bin_dir = _pg_bin()
        if bin_dir is None:
            _failure = "PostgreSQL binaries not found (set MEDRIXA_TEST_PG_ADMIN_DSN or MEDRIXA_TEST_PG_BIN)"
        else:
            try:
                _cluster = _EphemeralCluster(bin_dir)
            except (OSError, subprocess.SubprocessError) as error:
                _failure = f"could not start ephemeral PostgreSQL: {type(error).__name__}"
    if _failure:
        raise unittest.SkipTest(_failure)
    return _cluster.admin_dsn()


def fresh_database_dsn() -> str:
    """Create an empty database and return its DSN (skips the test if unavailable)."""
    try:
        import psycopg
    except ImportError:
        raise unittest.SkipTest("psycopg not installed (requirements-postgres.txt)") from None
    admin = _admin_dsn()
    name = f"medrixa_t_{uuid.uuid4().hex[:16]}"
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{name}"')
    parts = dict(item.split("=", 1) for item in admin.split())
    parts["dbname"] = name
    return " ".join(f"{k}={v}" for k, v in parts.items())
