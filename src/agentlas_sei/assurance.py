from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from .errors import ConfigurationError, ProjectStateError
from .interviewer import build_interview_record
from .llm import LLMProvider
from .scanner import scan_project
from .store import ProjectStore
from .util import (
    atomic_write_json,
    digest_value,
    read_json,
    unique_id,
    utc_now,
)
from .version import __version__

CONFIG_VERSION = "sei.config.v1"
BOUNDARY_VERSION = "sei.boundary.v1"


def _split_items(value: str, limit: int = 12) -> list[str]:
    parts = re.split(r"\s*(?:,|;|\n|\r|→|->)\s*", value)
    return [part.strip() for part in parts if part.strip()][:limit]


def _split_flow_steps(value: str, limit: int = 5) -> list[str]:
    parts = re.split(r"\s*(?:->|→|>)\s*", value)
    return [part.strip() for part in parts if part.strip()][:limit]


def _latest(records: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        record_id = record.get(key)
        if isinstance(record_id, str):
            by_id[record_id] = record
    return list(by_id.values())


def _snapshot_evidence(
    project_map: dict[str, Any],
    code_map: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "projectFingerprint": project_map["projectFingerprint"],
        "projectMapDigest": digest_value(project_map),
        "codeMapDigest": digest_value(code_map),
    }
    return {
        "schemaVersion": "sei.evidence.v1",
        "evidenceId": unique_id("evidence", payload),
        "kind": "code",
        "sourceRef": "local:bounded-project-snapshot",
        "contentDigest": digest_value(payload),
        "collectedAt": utc_now(),
        "validForProjectFingerprint": project_map["projectFingerprint"],
        "independenceGroup": "deterministic-local-scanner",
        "supports": ["map:project", "map:code"],
        "defeats": [],
        "limitations": [
            "Static presence does not prove runtime execution.",
            "Files outside the declared scan budget are not represented.",
            "Source content is not copied into the evidence ledger.",
        ],
        "dataClassification": "internal",
        "integrityState": "applicable",
    }


def initialize_project(project_root: Path, force: bool = False) -> dict[str, Any]:
    store = ProjectStore(project_root)
    if store.root.exists() and not force:
        raise ProjectStateError(
            f"{store.root} already exists. Use `sei map` to refresh it or `--force` "
            "to replace generated maps while preserving append-only ledgers."
        )
    project_map, code_map = scan_project(project_root)
    now = utc_now()
    config = {
        "schemaVersion": CONFIG_VERSION,
        "toolVersion": __version__,
        "projectName": project_root.name,
        "mode": "read-only",
        "llm": {
            "enabledByDefault": False,
            "transmitSourceCode": False,
            "transmitInterviewAnswers": False,
        },
        "privacy": {
            "rawSourceStored": False,
            "rawTelemetryStored": False,
            "secretFilesScanned": False,
        },
        "scan": {
            "symlinksFollowed": False,
            "projectFingerprint": project_map["projectFingerprint"],
        },
        "createdAt": now,
        "updatedAt": now,
    }
    boundary_payload = {
        "project": project_root.name,
        "fingerprint": project_map["projectFingerprint"],
        "languages": code_map["languages"],
    }
    boundary = {
        "schemaVersion": BOUNDARY_VERSION,
        "boundaryId": unique_id("boundary", boundary_payload),
        "projectName": project_root.name,
        "projectFingerprint": project_map["projectFingerprint"],
        "sourceIdentity": project_map["sourceIdentity"],
        "surfaces": ["local-project"],
        "actors": ["maintainer", "user", "software-system"],
        "included": [
            "bounded source tree",
            "manifests and documentation",
            "static code structure",
        ],
        "excluded": [
            "secret files",
            "symlink targets",
            "runtime telemetry",
            "user outcome evidence",
            "external services",
        ],
        "dataClassification": "internal",
        "frozenAt": now,
    }
    store.initialize(config, boundary)
    store.write_map("project", project_map)
    store.write_map("code", code_map)
    store.append_evidence(_snapshot_evidence(project_map, code_map))
    status = build_status(store)
    store.write_status(status)
    return status


def refresh_maps(project_root: Path) -> dict[str, Any]:
    store = ProjectStore(project_root)
    store.require_initialized()
    project_map, code_map = scan_project(project_root)
    store.write_map("project", project_map)
    store.write_map("code", code_map)
    store.append_evidence(_snapshot_evidence(project_map, code_map))
    config = store.config()
    config["toolVersion"] = __version__
    config["scan"]["projectFingerprint"] = project_map["projectFingerprint"]
    config["updatedAt"] = utc_now()
    atomic_write_json(store.config_path, config)
    boundary = store.boundary()
    boundary["projectFingerprint"] = project_map["projectFingerprint"]
    boundary["sourceIdentity"] = project_map["sourceIdentity"]
    boundary["frozenAt"] = utc_now()
    atomic_write_json(store.boundary_path, boundary)
    status = build_status(store)
    store.write_status(status)
    return status


def _claim(
    project: str,
    statement: str,
    layer: str,
    owner: str,
    observable: str,
    importance: str,
    accepted: bool,
) -> dict[str, Any]:
    now = utc_now()
    payload = {"project": project, "statement": statement, "layer": layer}
    return {
        "schemaVersion": "sei.claim.v1",
        "claimId": unique_id("claim", payload),
        "statement": statement,
        "layer": layer,
        "scope": {"project": project, "version": "current-boundary"},
        "owner": owner,
        "importance": importance,
        "state": "unknown",
        "intentStatus": "accepted" if accepted else "inferred",
        "observable": observable,
        "minimumEvidenceLevel": "E3" if importance == "critical" else "E2",
        "supportingEvidence": [],
        "defeatingEvidence": [],
        "validFrom": now,
        "expiresAt": None,
        "lastEvaluatedAt": now,
    }


def _build_claims(
    project: str,
    answers: dict[str, str],
    accepted_by: str | None,
) -> list[dict[str, Any]]:
    owner = accepted_by or "unassigned"
    accepted = accepted_by is not None
    return [
        _claim(
            project,
            answers["product-purpose"],
            "intent",
            owner,
            answers["success-oracle"],
            "critical",
            accepted,
        ),
        _claim(
            project,
            f"Success is observable when: {answers['success-oracle']}",
            "outcome",
            owner,
            answers["success-oracle"],
            "critical",
            accepted,
        ),
        _claim(
            project,
            f"Prohibited state: {answers['forbidden-states']}",
            "intent",
            owner,
            f"The prohibited state is absent: {answers['forbidden-states']}",
            "critical",
            accepted,
        ),
        _claim(
            project,
            f"Fallback policy: {answers['fallback-policy']}",
            "runtime",
            owner,
            "Primary, degraded, and fallback execution are reported separately.",
            "high",
            accepted,
        ),
    ]


def _build_flows(
    project: str,
    answers: dict[str, str],
    owner: str,
) -> list[dict[str, Any]]:
    journeys = _split_items(answers["critical-journeys"], limit=3)
    flows: list[dict[str, Any]] = []
    for journey_index, journey in enumerate(journeys, 1):
        macro_id = unique_id("macro", {"project": project, "journey": journey})
        meso_names = _split_flow_steps(
            answers.get(f"flow-{journey_index}-meso", journey)
        ) or [journey]
        meso_flows: list[dict[str, Any]] = []
        previous_step = "not-started"
        for meso_index, meso_name in enumerate(meso_names, 1):
            meso_id = unique_id(
                "meso", {"macro": macro_id, "name": meso_name}
            )
            micro_names = _split_flow_steps(
                answers.get(
                    f"flow-{journey_index}-micro-{meso_index}", meso_name
                )
            ) or [meso_name]
            micro_flows: list[dict[str, Any]] = []
            for micro_index, micro_name in enumerate(micro_names, 1):
                micro_id = unique_id(
                    "micro", {"meso": meso_id, "name": micro_name}
                )
                transition_id = unique_id(
                    "transition",
                    {"micro": micro_id, "event": "user-initiates"},
                )
                is_last = (
                    meso_index == len(meso_names)
                    and micro_index == len(micro_names)
                )
                after_state = (
                    answers["success-oracle"]
                    if is_last
                    else f"“{micro_name}” 단계가 완료됨"
                )
                micro_flows.append(
                    {
                        "microId": micro_id,
                        "name": micro_name,
                        "beforeState": previous_step,
                        "expectedAfterState": after_state,
                        "prohibitedStates": [answers["forbidden-states"]],
                        "codeLinks": {
                            "entry": [],
                            "exit": [],
                            "state": "unmapped",
                        },
                        "transitions": [
                            {
                                "transitionId": transition_id,
                                "branchKind": "primary",
                                "event": "user-initiates",
                                "fromState": previous_step,
                                "toState": "unknown-until-observed",
                                "observable": answers["success-oracle"],
                                "oracleState": "unverified",
                            }
                        ],
                    }
                )
                previous_step = f"{micro_name}:completed"
            meso_flows.append(
                {
                    "mesoId": meso_id,
                    "name": meso_name,
                    "responsibility": meso_name,
                    "microFlows": micro_flows,
                }
            )
        flows.append(
            {
                "schemaVersion": "sei.flow.v1",
                "flowId": macro_id,
                "level": "macro",
                "name": journey,
                "userGoal": journey,
                "owner": owner,
                "startState": "not-started",
                "terminalStates": ["achieved", "failed", "cancelled", "unknown"],
                "mesoFlows": meso_flows,
                "coverageState": "candidate",
                "createdAt": utc_now(),
            }
        )
    return flows


def record_interview(
    project_root: Path,
    answers: dict[str, str],
    accepted_by: str | None,
) -> dict[str, Any]:
    store = ProjectStore(project_root)
    store.require_initialized()
    interview = build_interview_record(project_root.name, answers, accepted_by)
    store.append_interview(interview)
    for claim in _build_claims(project_root.name, answers, accepted_by):
        store.append_claim(claim)
    for flow in _build_flows(
        project_root.name,
        answers,
        accepted_by or "unassigned",
    ):
        store.append_flow(flow)
    status = build_status(store)
    store.write_status(status)
    return {
        "interview": interview,
        "claimsCreated": 4,
        "flowsCreated": len(_split_items(answers["critical-journeys"], limit=3)),
        "status": status,
    }


def _finding(
    project: str,
    rule_id: str,
    title: str,
    severity: str,
    observation: str,
    evidence_refs: list[str],
    next_action: str,
) -> dict[str, Any]:
    payload = {"project": project, "rule": rule_id}
    return {
        "schemaVersion": "sei.finding.v1",
        "findingId": unique_id("finding", payload),
        "ruleId": rule_id,
        "title": title,
        "severity": severity,
        "state": "candidate",
        "observation": observation,
        "evidenceRefs": evidence_refs,
        "nextAction": next_action,
        "verdict": "unknown",
        "createdAt": utc_now(),
        "limitations": [
            "A deterministic candidate is not a confirmed defect.",
            "Closure requires an oracle and post-change outcome evidence.",
        ],
    }


def _top_signal_paths(
    code_map: dict[str, Any],
    signal: str,
    limit: int = 5,
) -> str:
    ranked = sorted(
        (
            (int(item.get("riskSignals", {}).get(signal, 0)), item.get("path", ""))
            for item in code_map.get("files", [])
        ),
        key=lambda item: (-item[0], item[1]),
    )
    visible = [
        f"{path} ({count})"
        for count, path in ranked
        if count > 0 and isinstance(path, str) and path
    ][:limit]
    return ", ".join(visible)


def deterministic_findings(
    store: ProjectStore,
    current_project_map: dict[str, Any],
    current_code_map: dict[str, Any],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    project = store.project_root.name
    claims = _latest(store.records("claims"), "claimId")
    flows = _latest(store.records("flows"), "flowId")
    interviews = store.records("interviews")
    stored_map = store.read_map("project")
    signals = current_code_map["riskSignals"]
    code_files = current_code_map["files"]
    test_files = [
        node for node in current_project_map["nodes"] if node["kind"] == "test"
    ]

    if not interviews:
        findings.append(
            _finding(
                project,
                "rule:missing-intent-interview",
                "Product intent has not been interviewed",
                "high",
                "No interview record exists, so success and prohibited states are unknown.",
                [],
                "Run `sei interview` and have an accountable owner accept the intent.",
            )
        )
    elif not any(
        item.get("epistemicStatus") == "accepted-intent" for item in interviews
    ):
        findings.append(
            _finding(
                project,
                "rule:unaccepted-intent",
                "Interview exists but product intent is not accepted",
                "high",
                "All interview records remain belief evidence.",
                [item["interviewId"] for item in interviews[-5:]],
                "Repeat or approve the interview with `--accept-as <owner>`.",
            )
        )
    if not claims:
        findings.append(
            _finding(
                project,
                "rule:missing-claims",
                "No falsifiable product claims exist",
                "high",
                "The project has no claim registry entries.",
                [],
                "Run the built-in interview to create owner-reviewable claims.",
            )
        )
    if not flows:
        findings.append(
            _finding(
                project,
                "rule:missing-user-flows",
                "No critical user flows are mapped",
                "high",
                "The project cannot link code changes to a user outcome.",
                [],
                "Map up to three critical journeys through the built-in interview.",
            )
        )
    if stored_map["projectFingerprint"] != current_project_map["projectFingerprint"]:
        findings.append(
            _finding(
                project,
                "rule:stale-project-map",
                "Stored project and code maps are stale",
                "medium",
                "The current bounded project fingerprint differs from the stored map.",
                ["map:project", "map:code"],
                "Run `sei map` before relying on impact or coverage results.",
            )
        )
    if current_project_map["coverage"]["truncated"]:
        findings.append(
            _finding(
                project,
                "rule:scan-budget-exhausted",
                "Project scan reached its safety budget",
                "medium",
                "The project map is partial because a file or byte budget was exhausted.",
                ["map:project"],
                "Narrow the boundary or add a reviewed scanner profile.",
            )
        )
    if signals["silent_catch"] > 0:
        top_paths = _top_signal_paths(current_code_map, "silent_catch")
        findings.append(
            _finding(
                project,
                "rule:silent-exception-handler",
                "Silent exception handlers were detected",
                "critical",
                (
                    f"Static analysis found {signals['silent_catch']} broad empty "
                    f"catch/pass pattern(s). Top paths: {top_paths}."
                ),
                ["map:code"],
                "Inspect each path and define a failure observable before changing code.",
            )
        )
    if signals["fallback"] > 0:
        top_paths = _top_signal_paths(current_code_map, "fallback")
        findings.append(
            _finding(
                project,
                "rule:fallback-observability-gap",
                "Fallback paths require explicit observability",
                "medium",
                (
                    f"Static analysis found {signals['fallback']} fallback reference(s). "
                    f"Top paths: {top_paths}."
                ),
                ["map:code"],
                "Classify each fallback as allowed, degraded, or prohibited and add a path oracle.",
            )
        )
    if signals["todo"] > max(5, len(code_files) // 3):
        findings.append(
            _finding(
                project,
                "rule:temporary-decision-density",
                "Temporary-decision markers are concentrated",
                "medium",
                f"Static analysis found {signals['todo']} TODO/FIXME/HACK/temporary marker(s).",
                ["map:code"],
                "Link consequential markers to an owner, expiry, and accepted decision.",
            )
        )
    if code_files and not test_files:
        findings.append(
            _finding(
                project,
                "rule:no-test-surface",
                "No test surface was identified",
                "high",
                f"The bounded map contains {len(code_files)} code file(s) and zero test file(s).",
                ["map:project", "map:code"],
                "Define at least one oracle that fails against a seeded defect.",
            )
        )
    for claim in claims:
        if claim.get("state") == "supported" and not claim.get("supportingEvidence"):
            findings.append(
                _finding(
                    project,
                    "rule:unsupported-supported-claim",
                    "A supported claim lacks evidence",
                    "critical",
                    f"{claim['claimId']} is supported with no supporting evidence.",
                    [claim["claimId"]],
                    "Downgrade the claim to unknown or attach adequate independent evidence.",
                )
            )
    if claims and not any(
        item.get("kind") in {"runtime", "outcome"} for item in store.records("evidence")
    ):
        findings.append(
            _finding(
                project,
                "rule:runtime-outcome-unknown",
                "Runtime and user outcome remain unobserved",
                "medium",
                "Claims exist, but the evidence ledger has no runtime or outcome evidence.",
                [item["claimId"] for item in claims[:10]],
                "Keep claims unknown until a bounded runtime and outcome adapter is approved.",
            )
        )
    return findings


def run_inspection(
    project_root: Path,
    llm_provider: LLMProvider | None = None,
) -> dict[str, Any]:
    store = ProjectStore(project_root)
    store.require_initialized()
    current_project_map, current_code_map = scan_project(project_root)
    previous_findings = _latest(store.records("findings"), "findingId")
    findings = deterministic_findings(store, current_project_map, current_code_map)
    for finding in findings:
        store.append_finding(finding)
    current_ids = {finding["findingId"] for finding in findings}
    cleared_findings: list[dict[str, Any]] = []
    for previous in previous_findings:
        if previous.get("findingId") not in current_ids and previous.get(
            "state"
        ) not in {"refuted", "closed"}:
            cleared = {
                **previous,
                "state": "refuted",
                "verdict": "not-reproduced",
                "observation": (
                    f"{previous.get('ruleId', 'rule')} did not reproduce in the "
                    "current bounded inspection."
                ),
                "nextAction": (
                    "Retain the prior observation as history and reopen only if "
                    "new evidence reproduces it."
                ),
                "createdAt": utc_now(),
            }
            store.append_finding(cleared)
            cleared_findings.append(cleared)
    llm_result = None
    if llm_provider is not None:
        claims = _latest(store.records("claims"), "claimId")
        payload = {
            "schemaVersion": "sei.llm-investigation-input.v1",
            "projectSummary": {
                "project": project_root.name,
                "languages": current_code_map["languages"],
                "riskSignals": current_code_map["riskSignals"],
                "fileCount": current_project_map["coverage"]["files"],
                "scanTruncated": current_project_map["coverage"]["truncated"],
            },
            "claims": [
                {
                    "claimId": item["claimId"],
                    "layer": item["layer"],
                    "state": item["state"],
                    "observable": item["observable"],
                }
                for item in claims[:20]
            ],
            "findingCandidates": [
                {
                    "findingId": item["findingId"],
                    "ruleId": item["ruleId"],
                    "severity": item["severity"],
                    "observation": item["observation"],
                }
                for item in findings[:20]
            ],
            "privacyBoundary": {
                "sourceCodeIncluded": False,
                "filePathsIncluded": False,
                "interviewAnswersIncluded": False,
            },
        }
        llm_result = llm_provider.investigate(payload)
    status = build_status(store)
    store.write_status(status)
    return {
        "schemaVersion": "sei.inspection-result.v1",
        "project": project_root.name,
        "generatedAt": utc_now(),
        "findings": findings,
        "clearedFindings": cleared_findings,
        "llmInvestigation": llm_result,
        "status": status,
    }


def build_status(store: ProjectStore) -> dict[str, Any]:
    def safe_count(kind: str, key: str) -> int:
        try:
            return len(_latest(store.records(kind), key))
        except ConfigurationError:
            return 0

    all_finding_records = _latest(store.records("findings"), "findingId")
    finding_records = [
        item
        for item in all_finding_records
        if item.get("state") not in {"refuted", "closed"}
    ]
    severity = Counter(item.get("severity", "unknown") for item in finding_records)
    interview_records = store.records("interviews")
    try:
        project_map = store.read_map("project")
        fingerprint = project_map.get("projectFingerprint")
        generated_at = project_map.get("generatedAt")
    except ConfigurationError:
        fingerprint = None
        generated_at = None
    return {
        "schemaVersion": "sei.status.v1",
        "project": store.project_root.name,
        "toolVersion": __version__,
        "mode": "read-only",
        "counts": {
            "interviews": len(interview_records),
            "acceptedInterviews": sum(
                1
                for item in interview_records
                if item.get("epistemicStatus") == "accepted-intent"
            ),
            "claims": safe_count("claims", "claimId"),
            "flows": safe_count("flows", "flowId"),
            "evidence": safe_count("evidence", "evidenceId"),
            "findings": len(finding_records),
        },
        "findingSeverity": dict(sorted(severity.items())),
        "projectFingerprint": fingerprint,
        "mapGeneratedAt": generated_at,
        "updatedAt": utc_now(),
    }


def project_status(project_root: Path) -> dict[str, Any]:
    store = ProjectStore(project_root)
    store.require_initialized()
    status = build_status(store)
    store.write_status(status)
    return status


def validate_project_state(project_root: Path) -> dict[str, Any]:
    store = ProjectStore(project_root)
    store.require_initialized()
    errors: list[str] = []
    warnings: list[str] = []
    required_json = [
        (store.config_path, CONFIG_VERSION),
        (store.boundary_path, BOUNDARY_VERSION),
        (store.project_map_path, "sei.project-map.v1"),
        (store.code_map_path, "sei.code-map.v1"),
    ]
    for path, version in required_json:
        try:
            value = read_json(path)
        except ConfigurationError as exc:
            errors.append(str(exc))
            continue
        if value.get("schemaVersion") != version:
            errors.append(
                f"Unexpected schemaVersion in {path}: {value.get('schemaVersion')}"
            )
    for kind, key, time_key in (
        ("interviews", "interviewId", "createdAt"),
        ("claims", "claimId", "validFrom"),
        ("flows", "flowId", "createdAt"),
        ("evidence", "evidenceId", "collectedAt"),
        ("findings", "findingId", "createdAt"),
    ):
        try:
            records = store.records(kind)
        except ConfigurationError as exc:
            errors.append(str(exc))
            continue
        identifiers = [item.get(key) for item in records if item.get(key)]
        if not all(isinstance(item, str) and ":" in item for item in identifiers):
            errors.append(f"Invalid identifiers in {kind}")
        if any(not isinstance(item.get(time_key), str) for item in records):
            errors.append(f"Missing {time_key} in {kind}")
    config = store.config()
    if config.get("mode") != "read-only":
        errors.append("Default mode must remain read-only.")
    if config.get("llm", {}).get("transmitSourceCode") is not False:
        errors.append("Source-code transmission must be disabled.")
    if config.get("llm", {}).get("transmitInterviewAnswers") is not False:
        errors.append("Interview-answer transmission must be disabled.")
    return {
        "schemaVersion": "sei.validation-result.v1",
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "checkedAt": utc_now(),
    }


def write_inspection_report(
    store: ProjectStore,
    result: dict[str, Any],
) -> Path:
    report_path = store.reports_dir / "latest-inspection.md"
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings = sorted(
        result["findings"],
        key=lambda item: (
            severity_order.get(item["severity"], 9),
            item["ruleId"],
        ),
    )
    lines = [
        f"# SEI Inspection: {result['project']}",
        "",
        f"Generated: {result['generatedAt']}",
        "",
        "This report contains candidates, not confirmed defects.",
        "",
        "## Findings",
        "",
    ]
    if not findings:
        lines.append("No deterministic candidates were generated in the bounded scan.")
    for finding in findings:
        lines.extend(
            [
                f"### [{finding['severity'].upper()}] {finding['title']}",
                "",
                finding["observation"],
                "",
                f"Next action: {finding['nextAction']}",
                "",
            ]
        )
    if result.get("llmInvestigation"):
        lines.extend(["## LLM hypotheses", ""])
        for hypothesis in result["llmInvestigation"]["hypotheses"]:
            lines.extend(
                [
                    f"- Hypothesis: {hypothesis['statement']}",
                    f"  Discriminator: {hypothesis['discriminator']}",
                    f"  Expected: {hypothesis['expected_observation']}",
                ]
            )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return report_path
