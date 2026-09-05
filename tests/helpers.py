from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from qc_skill.capabilities import detect_capabilities
from qc_skill.engine import ExecutionContext, run_report
from qc_skill.schemas import parse_request
from qc_skill.security import PathPolicy

CAPS = detect_capabilities()


def make_ctx(workspace: Path, allowed_roots=None, cache=None) -> ExecutionContext:
    return ExecutionContext(
        path_policy=PathPolicy(workspace=str(workspace), allowed_input_roots=allowed_roots),
        capabilities=CAPS,
        cache=cache,
    )


def run(doc: Dict[str, Any], workspace: Path, allowed_roots=None, cache=None) -> Dict[str, Any]:
    request = parse_request(doc)
    ctx = make_ctx(workspace, allowed_roots=allowed_roots, cache=cache)
    return run_report(request, ctx)


def checks_by_id(response: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {c["check_id"]: c for c in response["report"]["checks"]}


def measurements_by_id(response: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {m["id"]: m for m in response["report"]["measurements"]}
