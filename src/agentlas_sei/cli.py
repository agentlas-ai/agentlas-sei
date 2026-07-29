from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .assurance import (
    initialize_project,
    project_status,
    record_interview,
    refresh_maps,
    run_inspection,
    validate_project_state,
    write_inspection_report,
)
from .dashboard import serve_dashboard
from .debt import audit_product_debt
from .errors import SEIError
from .gate import (
    collect_change_set,
    evaluate_gate,
    install_hooks,
    load_review_answers,
    record_change_review,
    review_template,
    run_review_interactive,
    uninstall_hooks,
)
from .i18n import LOCALE_CHOICES
from .interviewer import load_answers, question_template, run_interactive
from .llm import OpenAICompatibleProvider
from .store import ProjectStore
from .util import atomic_write_json, safe_project_path
from .version import __version__


def _print(value: Any, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if isinstance(value, str):
        print(value)
        return
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _project(raw: str) -> Path:
    return safe_project_path(raw)


def _add_project_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "project",
        nargs="?",
        default=".",
        help="Local project directory (default: current directory)",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON output")


def _add_push_updates_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--push-updates",
        type=Path,
        help=argparse.SUPPRESS,
    )


def _add_dashboard_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Serve without opening the default browser",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Local port; 0 selects a free port",
    )
    parser.add_argument(
        "--lang",
        choices=LOCALE_CHOICES,
        default="auto",
        help="Dashboard language: auto, ko, en, ja, or zh",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sei",
        description="Local-first System Epistemic Integrity agent",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Attach, interview, inspect, and open the local dashboard",
    )
    _add_project_argument(run_parser)
    run_parser.add_argument(
        "--answers",
        type=Path,
        help="Load non-interactive interview answers from JSON",
    )
    run_parser.add_argument(
        "--accept-as",
        help="Accountable owner accepting interview answers as product intent",
    )
    run_parser.add_argument(
        "--llm",
        action="store_true",
        help="Ask an explicitly configured LLM for bounded hypotheses",
    )
    _add_dashboard_arguments(run_parser)

    init_parser = subparsers.add_parser("init", help="Attach SEI to a local project")
    _add_project_argument(init_parser)
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace generated maps while preserving append-only ledgers",
    )

    map_parser = subparsers.add_parser("map", help="Refresh project and code maps")
    _add_project_argument(map_parser)

    interview_parser = subparsers.add_parser(
        "interview",
        help="Run the built-in product-intent interview",
    )
    _add_project_argument(interview_parser)
    interview_parser.add_argument(
        "--answers",
        type=Path,
        help="Load non-interactive answers from JSON",
    )
    interview_parser.add_argument(
        "--accept-as",
        help="Accountable owner accepting the answers as product intent",
    )

    template_parser = subparsers.add_parser(
        "interview-template",
        help="Create a blank interview answer template",
    )
    template_parser.add_argument(
        "--output",
        type=Path,
        help="Write JSON to a file instead of stdout",
    )
    template_parser.add_argument("--json", action="store_true")

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Generate deterministic contradiction and debt candidates",
    )
    _add_project_argument(inspect_parser)
    inspect_parser.add_argument(
        "--llm",
        action="store_true",
        help="Send bounded summaries to an explicitly configured LLM provider",
    )
    inspect_parser.add_argument(
        "--no-report",
        action="store_true",
        help="Do not write .sei/reports/latest-inspection.md",
    )

    status_parser = subparsers.add_parser("status", help="Show assurance state")
    _add_project_argument(status_parser)

    dashboard_parser = subparsers.add_parser(
        "dashboard",
        help="Open the plain-language local assurance dashboard",
    )
    _add_project_argument(dashboard_parser)
    _add_dashboard_arguments(dashboard_parser)

    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate local SEI state and safety invariants",
    )
    _add_project_argument(validate_parser)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check runtime and optional provider configuration",
    )
    _add_project_argument(doctor_parser)

    audit_parser = subparsers.add_parser(
        "self-audit",
        help="Audit this SEI implementation or another SEI source tree",
    )
    _add_project_argument(audit_parser)

    review_parser = subparsers.add_parser(
        "review",
        help="Review a risky staged or pre-push change for cognitive and technical debt",
    )
    _add_project_argument(review_parser)
    review_parser.add_argument(
        "--stage",
        choices=("pre-commit", "pre-push"),
        default="pre-commit",
    )
    review_parser.add_argument("--reviewer", required=True)
    review_parser.add_argument(
        "--answers",
        type=Path,
        help="Load non-interactive review answers from JSON",
    )
    review_parser.add_argument(
        "--template",
        type=Path,
        help="Write a digest-bound answer template and exit",
    )
    _add_push_updates_argument(review_parser)

    gate_parser = subparsers.add_parser(
        "gate",
        help="Enforce a digest-bound change review when risk signals require it",
    )
    _add_project_argument(gate_parser)
    gate_parser.add_argument(
        "--stage",
        choices=("pre-commit", "pre-push"),
        default="pre-commit",
    )
    _add_push_updates_argument(gate_parser)

    hooks_parser = subparsers.add_parser(
        "hooks",
        help="Install or remove managed pre-commit and pre-push hooks",
    )
    hooks_parser.add_argument("action", choices=("install", "uninstall"))
    _add_project_argument(hooks_parser)
    hooks_parser.add_argument(
        "--force",
        action="store_true",
        help="Back up conflicting hooks before installation",
    )

    return parser


KNOWN_COMMANDS = {
    "run",
    "init",
    "map",
    "interview",
    "interview-template",
    "inspect",
    "status",
    "dashboard",
    "validate",
    "doctor",
    "self-audit",
    "review",
    "gate",
    "hooks",
}


def normalize_argv(argv: Sequence[str] | None) -> list[str]:
    values = list(sys.argv[1:] if argv is None else argv)
    if not values:
        return ["run", "."]
    first = values[0]
    if first in {"-h", "--help", "--version"} or first in KNOWN_COMMANDS:
        return values
    candidate = Path(first).expanduser()
    if candidate.exists():
        return ["run", *values]
    return values


def _doctor(project: Path) -> dict[str, Any]:
    store = ProjectStore(project)
    initialized = store.config_path.exists() and store.boundary_path.exists()
    llm_base = bool(os.environ.get("SEI_LLM_BASE_URL", "").strip())
    llm_model = bool(os.environ.get("SEI_LLM_MODEL", "").strip())
    return {
        "schemaVersion": "sei.doctor.v1",
        "toolVersion": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "project": str(project),
        "initialized": initialized,
        "llm": {
            "configured": llm_base and llm_model,
            "baseUrlPresent": llm_base,
            "modelPresent": llm_model,
            "apiKeyPresent": bool(os.environ.get("SEI_LLM_API_KEY", "").strip()),
            "networkCalled": False,
        },
        "defaultMode": "read-only",
    }


def _push_update_text(path: Path | None) -> str | None:
    if path is None:
        return None
    return path.expanduser().resolve().read_text(encoding="utf-8")


def _installed_command() -> tuple[str, ...]:
    invoked = Path(sys.argv[0]).expanduser()
    if invoked.name == "sei" and invoked.exists():
        return (str(invoked.resolve()),)
    return (str(Path(sys.executable).resolve()), "-m", "agentlas_sei")


def _has_interview(project: Path) -> bool:
    store = ProjectStore(project)
    return bool(store.records("interviews"))


def _run_guided(args: argparse.Namespace, project: Path) -> None:
    store = ProjectStore(project)
    if not store.config_path.exists() or not store.boundary_path.exists():
        print("1/4 프로젝트를 안전한 읽기 전용 모드로 연결합니다.")
        initialize_project(project)
    if not _has_interview(project):
        print("2/4 먼저 제품 목적과 절대 깨지면 안 되는 흐름을 확인합니다.")
        if args.answers:
            answers = load_answers(args.answers.expanduser().resolve())
        else:
            if not sys.stdin.isatty():
                raise SEIError(
                    "인터뷰 답변이 필요합니다. 대화형 터미널에서 다시 실행하거나 "
                    "--answers <파일>을 사용하세요."
                )
            answers = run_interactive()
        record_interview(project, answers, args.accept_as)
    else:
        print("2/4 기존 인터뷰 기록을 사용합니다.")
    print("3/4 현재 코드 지도와 확인 후보를 새로 만듭니다.")
    refresh_maps(project)
    provider = OpenAICompatibleProvider.from_environment() if args.llm else None
    result = run_inspection(project, llm_provider=provider)
    write_inspection_report(ProjectStore(project), result)
    print("4/4 쉬운 설명 대시보드를 엽니다.")
    serve_dashboard(
        project,
        port=args.port,
        open_browser=not args.no_open,
        locale=args.lang,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(normalize_argv(argv))
    try:
        if args.command == "interview-template":
            template = question_template()
            if args.output:
                output = args.output.expanduser().resolve()
                atomic_write_json(output, template)
                _print({"written": str(output)}, args.json)
            else:
                _print(template, True)
            return 0

        project = _project(args.project)
        if args.command == "run":
            _run_guided(args, project)
            return 0
        if args.command == "init":
            result = initialize_project(project, force=args.force)
            _print(result, args.json)
            return 0
        if args.command == "map":
            result = refresh_maps(project)
            _print(result, args.json)
            return 0
        if args.command == "interview":
            answers = (
                load_answers(args.answers.expanduser().resolve())
                if args.answers
                else run_interactive()
            )
            result = record_interview(project, answers, args.accept_as)
            _print(result, args.json)
            return 0
        if args.command == "inspect":
            provider = OpenAICompatibleProvider.from_environment() if args.llm else None
            result = run_inspection(project, llm_provider=provider)
            if not args.no_report:
                report = write_inspection_report(ProjectStore(project), result)
                result["report"] = str(report)
            _print(result, args.json)
            return 0
        if args.command == "status":
            _print(project_status(project), args.json)
            return 0
        if args.command == "dashboard":
            serve_dashboard(
                project,
                port=args.port,
                open_browser=not args.no_open,
                locale=args.lang,
            )
            return 0
        if args.command == "validate":
            result = validate_project_state(project)
            _print(result, args.json)
            return 0 if result["valid"] else 2
        if args.command == "doctor":
            _print(_doctor(project), args.json)
            return 0
        if args.command == "self-audit":
            result = audit_product_debt(project)
            _print(result, args.json)
            return 0 if result["status"] == "pass" else 3
        if args.command == "review":
            store = ProjectStore(project)
            store.require_initialized()
            change_set = collect_change_set(
                project,
                args.stage,
                _push_update_text(args.push_updates),
            )
            if args.template:
                output = args.template.expanduser().resolve()
                atomic_write_json(output, review_template(change_set))
                _print(
                    {"written": str(output), "diffDigest": change_set.diff_digest},
                    args.json,
                )
                return 0
            answers = (
                load_review_answers(
                    args.answers.expanduser().resolve(), change_set.diff_digest
                )
                if args.answers
                else run_review_interactive(change_set)
            )
            result = record_change_review(
                store,
                change_set,
                answers,
                args.reviewer,
            )
            gate_result = evaluate_gate(store, change_set)
            _print(
                {
                    "review": result,
                    "gate": gate_result,
                },
                args.json,
            )
            return 0 if gate_result["allowed"] else 4
        if args.command == "gate":
            store = ProjectStore(project)
            store.require_initialized()
            result = evaluate_gate(
                store,
                collect_change_set(
                    project,
                    args.stage,
                    _push_update_text(args.push_updates),
                ),
            )
            _print(result, args.json)
            return 0 if result["allowed"] else 4
        if args.command == "hooks":
            result = (
                install_hooks(
                    project,
                    force=args.force,
                    command=_installed_command(),
                )
                if args.action == "install"
                else uninstall_hooks(project)
            )
            _print(result, args.json)
            return 0
        parser.error(f"Unknown command: {args.command}")
    except (SEIError, OSError) as exc:
        print(f"sei: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
