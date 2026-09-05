"""CLI entry point: contract / doctor / run (STEP 11/18 of the task spec).

    qc contract --json
    qc doctor --json
    qc run <request.json | -> --json [--workspace DIR] [--allowed-input-root DIR]...

PathPolicy roots are supplied on the command line by whoever invokes this
process (an agent, a script, a human) - never by the JSON request body
itself, so a request can never grant itself a wider filesystem view than
its caller intended.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import SKILL_ID, VERSION
from .cache import QCReportCache
from .capabilities import detect_capabilities
from .contract import doctor_report, skill_contract
from .engine import ExecutionContext, run_report
from .errors import QCError, error
from .schemas import parse_request
from .security import PathPolicy


def _print_json(doc: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(doc, sort_keys=True, separators=(",", ":")))
    sys.stdout.write("\n")


def _read_request_document(source: str) -> Any:
    if source == "-":
        raw = sys.stdin.read()
    else:
        try:
            with open(source, "r", encoding="utf-8") as fh:
                raw = fh.read()
        except OSError as exc:
            raise error("INVALID_REQUEST", f"could not read request file: {exc}", path=source)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise error("INVALID_REQUEST", f"request is not valid JSON: {exc}")


def _failure_envelope(err: QCError) -> Dict[str, Any]:
    return {
        "schema": "qc/response@1",
        "status": "failed",
        "skill": {"id": SKILL_ID, "version": VERSION},
        "error": err.to_dict(),
    }


def cmd_contract(args: argparse.Namespace) -> int:
    doc = skill_contract()
    if args.json:
        _print_json(doc)
    else:
        sys.stdout.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    workspace = args.workspace or os.getcwd()
    report = doctor_report(workspace)
    if args.json:
        _print_json(report)
    else:
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if report["status"] == "ok":
        return 0
    if report["status"] == "degraded":
        return 2
    return 1


def cmd_run(args: argparse.Namespace) -> int:
    workspace = args.workspace or os.getcwd()
    path_policy = PathPolicy(workspace=workspace, allowed_input_roots=args.allowed_input_root or None)

    try:
        doc = _read_request_document(args.request)
        request = parse_request(doc)

        cache = None
        if not args.no_cache:
            cache_dir = args.cache_dir or os.path.join(workspace, ".qc-cache")
            cache = QCReportCache(directory=Path(cache_dir))

        caps = detect_capabilities()
        ctx = ExecutionContext(path_policy=path_policy, capabilities=caps, cache=cache)
        response = run_report(request, ctx)
    except QCError as err:
        if args.json:
            _print_json(_failure_envelope(err))
        else:
            sys.stderr.write(f"{err.code}: {err.message}\n")
        return err.exit_code
    except Exception as exc:  # pragma: no cover - defensive last resort
        err = error("INTERNAL_ERROR", f"unexpected error: {exc}")
        if args.json:
            _print_json(_failure_envelope(err))
        else:
            sys.stderr.write(f"{err.code}: {err.message}\n")
        return err.exit_code

    if args.json:
        _print_json(response)
    else:
        sys.stdout.write(json.dumps(response, indent=2, sort_keys=True) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qc", description="Media quality control / validation skill")
    sub = parser.add_subparsers(dest="command", required=True)

    p_contract = sub.add_parser("contract", help="print the skill contract")
    p_contract.add_argument("--json", action="store_true")
    p_contract.set_defaults(func=cmd_contract)

    p_doctor = sub.add_parser("doctor", help="check dependencies and self-consistency")
    p_doctor.add_argument("--json", action="store_true")
    p_doctor.add_argument("--workspace", default=None)
    p_doctor.set_defaults(func=cmd_doctor)

    p_run = sub.add_parser("run", help="run inspect/check/validate against a request document")
    p_run.add_argument("request", help="path to a request JSON file, or '-' for stdin")
    p_run.add_argument("--json", action="store_true")
    p_run.add_argument("--workspace", default=None, help="root for cache/report writes (default: cwd)")
    p_run.add_argument(
        "--allowed-input-root", action="append", default=None,
        help="restrict input files to this root (repeatable); default: any readable regular file",
    )
    p_run.add_argument("--cache-dir", default=None, help="override the report cache directory")
    p_run.add_argument("--no-cache", action="store_true", help="disable report reuse entirely")
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
