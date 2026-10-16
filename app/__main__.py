"""Run with python -m app; local synthetic simulation, no server or network."""
import argparse
import asyncio
import sys
from pathlib import Path

from app.api.contracts import MAX_INPUT_BYTES, InputRejected, parse_case
from app.bootstrap import create_runner, synthetic_example
from app.core.state_manager import StateManager
from app.observability.summary import summarize, summarize_recovery


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MEDRIXA: simulación local, solo datos sintéticos")
    parser.add_argument("--case", type=Path, help="Archivo JSON sintético; omitir para ejecutar el ejemplo")
    parser.add_argument("--store", type=Path,
                        help="Directorio del adaptador local PROVISIONAL de ejecuciones (no PostgreSQL)")
    parser.add_argument("--recover", action="store_true",
                        help="Llevar a revisión humana las ejecuciones interrumpidas de --store; no ejecuta agentes")
    args = parser.parse_args(argv)
    if args.recover and (args.store is None or args.case is not None):
        print('{"error":"INVALID_INPUT"}', file=sys.stderr)
        return 2
    repository = None
    if args.store is not None:
        # Imported lazily: the in-memory default path does not touch the filesystem.
        from app.persistence.leases import LocalFileRunLeases
        from app.persistence.local_file import ProvisionalLocalFileRepository
        try:
            repository = ProvisionalLocalFileRepository(args.store)
            leases = LocalFileRunLeases(args.store / "leases")  # P6: liveness of executors
        except OSError:
            print('{"error":"STORE_UNAVAILABLE"}', file=sys.stderr)
            return 2
    if args.recover:
        from app.persistence.recovery import recover_interrupted_runs
        report = summarize_recovery(recover_interrupted_runs(repository, leases))
        print(report.public_json(indent=2))
        return 0 if report.corrupt_records == 0 else 1
    try:
        if args.case:
            with args.case.open("rb") as handle:
                case = parse_case(handle.read(MAX_INPUT_BYTES + 1))
        else:
            case = synthetic_example()
    except (InputRejected, OSError):
        print('{"error":"INVALID_INPUT"}', file=sys.stderr)
        return 2
    if repository is None:
        state = asyncio.run(create_runner().run(StateManager(case)))
    else:
        from app.persistence.leases import LeaseHeld, LeaseLost
        from app.persistence.leasing import LeasedExecutor
        from app.persistence.manager import PersistentStateManager
        from app.persistence.repository import RepositoryError, StateConflict
        try:
            manager = PersistentStateManager(case, repository)
            state = asyncio.run(LeasedExecutor(leases).run(create_runner(), manager))
        except (RepositoryError, StateConflict, OSError, LeaseHeld, LeaseLost):
            # Stored state keeps its last persisted revision; recover with --recover.
            print('{"error":"STORE_UNAVAILABLE"}', file=sys.stderr)
            return 1
    summary = summarize(state)
    print(summary.model_dump_json(indent=2))
    return 0 if state.stop_reason in ("REVIEW_REQUIRED", "MISSING_DATA") else 1


if __name__ == "__main__":
    raise SystemExit(main())
