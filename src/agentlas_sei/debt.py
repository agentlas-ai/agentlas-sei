from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .scanner import scan_project
from .util import utc_now
from .version import __version__


def _item(
    debt_id: str,
    facet: str,
    severity: str,
    state: str,
    observation: str,
    next_action: str,
) -> dict[str, str]:
    return {
        "debtId": debt_id,
        "facet": facet,
        "severity": severity,
        "state": state,
        "observation": observation,
        "nextAction": next_action,
    }


def audit_product_debt(root: Path) -> dict[str, Any]:
    project_map, code_map = scan_project(root)
    items: list[dict[str, str]] = []

    required_docs = {
        "README.md": "product entry point",
        "ARCHITECTURE.md": "architecture contract",
        "SECURITY.md": "security boundary",
        "CHANGELOG.md": "version history",
        "CONTRIBUTING.md": "contribution and review contract",
        "AGENTS.md": "agent operating rules",
    }
    for name, purpose in required_docs.items():
        if not (root / name).is_file():
            items.append(
                _item(
                    f"debt:missing-{name.lower()}",
                    "intent",
                    "high",
                    "open",
                    f"{name} is missing, so the {purpose} is not durable.",
                    f"Create and review {name}.",
                )
            )

    if not (root / "tests").is_dir():
        items.append(
            _item(
                "debt:missing-private-verification-suite",
                "evidence",
                "critical",
                "open",
                "No local verification suite exists.",
                "Add isolated tests that cover CLI, storage, scanner, and safety gates.",
            )
        )

    pyproject = root / "pyproject.toml"
    version_file = root / "src" / "agentlas_sei" / "version.py"
    if pyproject.exists() and version_file.exists():
        pyproject_text = pyproject.read_text(encoding="utf-8")
        version_text = version_file.read_text(encoding="utf-8")
        pyproject_match = re.search(
            r'^version\s*=\s*"([^"]+)"', pyproject_text, re.MULTILINE
        )
        module_match = re.search(r'__version__\s*=\s*"([^"]+)"', version_text)
        if (
            not pyproject_match
            or not module_match
            or pyproject_match.group(1) != module_match.group(1)
            or module_match.group(1) != __version__
        ):
            items.append(
                _item(
                    "debt:version-drift",
                    "technical",
                    "high",
                    "open",
                    "Package and runtime versions do not agree.",
                    "Reconcile pyproject.toml, version.py, and the runtime version.",
                )
            )

    gitignore = root / ".gitignore"
    if not gitignore.exists() or ".sei/" not in gitignore.read_text(encoding="utf-8"):
        items.append(
            _item(
                "debt:local-state-publication-risk",
                "governance",
                "critical",
                "open",
                "Local .sei state is not excluded from normal source publication.",
                "Add .sei/ to .gitignore and verify the public manifest.",
            )
        )

    if project_map["coverage"]["truncated"]:
        items.append(
            _item(
                "debt:self-scan-incomplete",
                "observability",
                "medium",
                "open",
                "The product cannot fully map itself within its own scan budget.",
                "Reduce generated content or use a reviewed scoped profile.",
            )
        )

    if code_map["riskSignals"]["silent_catch"]:
        items.append(
            _item(
                "debt:self-silent-catch",
                "technical",
                "critical",
                "open",
                "The SEI implementation contains an empty exception handler.",
                "Replace silent handling with an explicit observable and bounded error.",
            )
        )

    status = (
        "pass"
        if not any(item["severity"] in {"critical", "high"} for item in items)
        else "fail"
    )
    return {
        "schemaVersion": "sei.self-debt-audit.v1",
        "project": root.name,
        "toolVersion": __version__,
        "status": status,
        "items": items,
        "summary": {
            "critical": sum(item["severity"] == "critical" for item in items),
            "high": sum(item["severity"] == "high" for item in items),
            "medium": sum(item["severity"] == "medium" for item in items),
            "low": sum(item["severity"] == "low" for item in items),
        },
        "limitations": [
            "A passing self-audit proves only the implemented deterministic rules.",
            "Unknown runtime and user-outcome behavior remain outside this offline audit.",
        ],
        "auditedAt": utc_now(),
    }
