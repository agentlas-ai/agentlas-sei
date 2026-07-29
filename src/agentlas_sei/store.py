from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ProjectStateError
from .util import append_jsonl, atomic_write_json, read_json, read_jsonl


@dataclass(frozen=True)
class ProjectStore:
    project_root: Path

    @property
    def root(self) -> Path:
        return self.project_root / ".sei"

    @property
    def config_path(self) -> Path:
        return self.root / "config.json"

    @property
    def boundary_path(self) -> Path:
        return self.root / "boundary.json"

    @property
    def status_path(self) -> Path:
        return self.root / "status.json"

    @property
    def project_map_path(self) -> Path:
        return self.root / "maps" / "project-map.json"

    @property
    def code_map_path(self) -> Path:
        return self.root / "maps" / "code-map.json"

    @property
    def interviews_path(self) -> Path:
        return self.root / "memory" / "interviews.jsonl"

    @property
    def claims_path(self) -> Path:
        return self.root / "registry" / "claims.jsonl"

    @property
    def flows_path(self) -> Path:
        return self.root / "registry" / "flows.jsonl"

    @property
    def evidence_path(self) -> Path:
        return self.root / "evidence" / "evidence.jsonl"

    @property
    def findings_path(self) -> Path:
        return self.root / "findings" / "findings.jsonl"

    @property
    def change_reviews_path(self) -> Path:
        return self.root / "decisions" / "change-reviews.jsonl"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    def initialize(self, config: dict[str, Any], boundary: dict[str, Any]) -> None:
        for path in (
            self.root / "maps",
            self.root / "memory",
            self.root / "registry",
            self.root / "evidence",
            self.root / "findings",
            self.root / "decisions",
            self.root / "repair-packets",
            self.reports_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.config_path, config)
        atomic_write_json(self.boundary_path, boundary)

    def require_initialized(self) -> None:
        if not self.config_path.exists() or not self.boundary_path.exists():
            raise ProjectStateError(
                f"{self.project_root} is not attached. Run `sei init` first."
            )

    def config(self) -> dict[str, Any]:
        self.require_initialized()
        return read_json(self.config_path)

    def boundary(self) -> dict[str, Any]:
        self.require_initialized()
        return read_json(self.boundary_path)

    def write_map(self, kind: str, value: dict[str, Any]) -> None:
        if kind == "project":
            atomic_write_json(self.project_map_path, value)
        elif kind == "code":
            atomic_write_json(self.code_map_path, value)
        else:
            raise ValueError(f"Unsupported map kind: {kind}")

    def read_map(self, kind: str) -> dict[str, Any]:
        if kind == "project":
            return read_json(self.project_map_path)
        if kind == "code":
            return read_json(self.code_map_path)
        raise ValueError(f"Unsupported map kind: {kind}")

    def append_interview(self, value: dict[str, Any]) -> None:
        append_jsonl(self.interviews_path, value)

    def append_claim(self, value: dict[str, Any]) -> None:
        append_jsonl(self.claims_path, value)

    def append_flow(self, value: dict[str, Any]) -> None:
        append_jsonl(self.flows_path, value)

    def append_evidence(self, value: dict[str, Any]) -> None:
        append_jsonl(self.evidence_path, value)

    def append_finding(self, value: dict[str, Any]) -> None:
        append_jsonl(self.findings_path, value)

    def append_change_review(self, value: dict[str, Any]) -> None:
        append_jsonl(self.change_reviews_path, value)

    def records(self, kind: str) -> list[dict[str, Any]]:
        mapping = {
            "interviews": self.interviews_path,
            "claims": self.claims_path,
            "flows": self.flows_path,
            "evidence": self.evidence_path,
            "findings": self.findings_path,
            "change-reviews": self.change_reviews_path,
        }
        if kind not in mapping:
            raise ValueError(f"Unsupported record kind: {kind}")
        return read_jsonl(mapping[kind])

    def write_status(self, value: dict[str, Any]) -> None:
        atomic_write_json(self.status_path, value)
