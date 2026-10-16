"""Run with python -m app; local synthetic simulation, no server or network."""
import argparse
import asyncio
import sys
from pathlib import Path

from app.api.contracts import MAX_INPUT_BYTES, InputRejected, parse_case
from app.bootstrap import create_runner, synthetic_example
from app.core.state_manager import StateManager
from app.observability.summary import summarize


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MEDRIXA: simulación local, solo datos sintéticos")
    parser.add_argument("--case", type=Path, help="Archivo JSON sintético; omitir para ejecutar el ejemplo")
    args = parser.parse_args(argv)
    try:
        if args.case:
            with args.case.open("rb") as handle:
                case = parse_case(handle.read(MAX_INPUT_BYTES + 1))
        else:
            case = synthetic_example()
        state = asyncio.run(create_runner().run(StateManager(case)))
    except (InputRejected, OSError):
        print('{"error":"INVALID_INPUT"}', file=sys.stderr)
        return 2
    summary = summarize(state)
    print(summary.model_dump_json(indent=2))
    return 0 if state.stop_reason in ("REVIEW_REQUIRED", "MISSING_DATA") else 1


if __name__ == "__main__":
    raise SystemExit(main())
