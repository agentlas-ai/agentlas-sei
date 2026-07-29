from __future__ import annotations

import os
import re
import shlex
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigurationError
from .store import ProjectStore
from .util import digest_bytes, read_json, unique_id, utc_now

REVIEW_QUESTIONS = (
    (
        "intent-alignment",
        "이 변경은 어떤 사용자 목적 또는 승인된 Claim을 개선합니까?",
    ),
    (
        "outcome-oracle",
        "변경이 맞았음을 사용자 결과나 상태 변화로 어떻게 확인합니까?",
    ),
    (
        "assumption-change",
        "새로 추가되거나 더 이상 유효하지 않은 가정은 무엇입니까?",
    ),
    (
        "cognitive-handoff",
        "다른 사람이 이 변경의 이유·제약·실패 조건을 재구성할 수 있는 증거는 무엇입니까?",
    ),
    (
        "debt-introduced",
        "의도적으로 남기는 기술·인지·의도부채와 제거 기한은 무엇입니까? 없으면 '없음'이라고 적습니다.",
    ),
    (
        "fallback-impact",
        "fallback·retry·부분 성공·조용한 실패의 의미가 바뀝니까? 어떻게 관측합니까?",
    ),
    (
        "rollback",
        "예상과 다를 때 되돌리거나 격리하는 방법은 무엇입니까?",
    ),
    (
        "decision",
        "최종 판단을 proceed, proceed-with-debt, stop 중 하나로 입력하세요.",
    ),
)

HIGH_RISK_PATH_RE = re.compile(
    r"(?:auth|permission|billing|payment|migration|schema|database|security|release|deploy)",
    re.IGNORECASE,
)
HIGH_RISK_DIFF_RE = re.compile(
    r"(?:fallback|retry|except|catch|permission|authorization|migration|rollback)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ChangeSet:
    scope: str
    diff_digest: str
    changed_paths: tuple[str, ...]
    additions: int
    deletions: int
    risk_codes: tuple[str, ...]
    risk_level: str
    pushed_refs: tuple[str, ...] = ()
    no_changes: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "diffDigest": self.diff_digest,
            "changedPaths": list(self.changed_paths),
            "additions": self.additions,
            "deletions": self.deletions,
            "riskCodes": list(self.risk_codes),
            "riskLevel": self.risk_level,
            "pushedRefs": list(self.pushed_refs),
            "noChanges": self.no_changes,
        }


@dataclass(frozen=True)
class PushUpdate:
    local_ref: str
    local_oid: str
    remote_ref: str
    remote_oid: str

    @property
    def label(self) -> str:
        return f"{self.local_ref}->{self.remote_ref}"


ZERO_OID_RE = re.compile(r"^0{40,64}$")
OID_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")


def _git(
    root: Path,
    arguments: list[str],
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=check,
            capture_output=True,
            text=True,
            input=input_text,
            timeout=15,
        )
    except FileNotFoundError as exc:
        raise ConfigurationError("Git is not installed.") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or "unknown git error"
        raise ConfigurationError(f"Git command failed: {detail}") from exc


def _require_git(root: Path) -> None:
    result = _git(root, ["rev-parse", "--is-inside-work-tree"], check=False)
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise ConfigurationError("Git change review requires a local Git repository.")


def _pre_push_range(root: Path) -> list[str]:
    upstream = _git(
        root,
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        check=False,
    )
    if upstream.returncode == 0 and upstream.stdout.strip():
        return [f"{upstream.stdout.strip()}..HEAD"]
    parent = _git(root, ["rev-parse", "HEAD^"], check=False)
    if parent.returncode == 0:
        return ["HEAD^..HEAD"]
    head = _git(root, ["rev-parse", "HEAD"], check=False)
    if head.returncode == 0:
        return [f"{head.stdout.strip()}^!"]
    return ["--cached"]


def parse_push_updates(raw: str) -> tuple[PushUpdate, ...]:
    if len(raw.encode("utf-8")) > 1024 * 1024:
        raise ConfigurationError(
            "Pre-push update input exceeds the 1 MiB safety limit."
        )
    updates: list[PushUpdate] = []
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 4:
            raise ConfigurationError(
                f"Invalid pre-push update on line {line_number}: expected four fields."
            )
        local_ref, local_oid, remote_ref, remote_oid = fields
        if not OID_RE.fullmatch(local_oid) or not OID_RE.fullmatch(remote_oid):
            raise ConfigurationError(
                f"Invalid pre-push object ID on line {line_number}."
            )
        local_ref_valid = local_ref.startswith("refs/") or (
            local_ref == "(delete)" and ZERO_OID_RE.fullmatch(local_oid)
        )
        if not local_ref_valid or not remote_ref.startswith("refs/"):
            raise ConfigurationError(f"Invalid pre-push ref on line {line_number}.")
        updates.append(PushUpdate(local_ref, local_oid, remote_ref, remote_oid))
    return tuple(updates)


def _push_diff(
    root: Path,
    updates: tuple[PushUpdate, ...],
) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    diff_parts: list[str] = []
    changed_paths: set[str] = set()
    pushed_refs: list[str] = []
    risk_codes: list[str] = []
    empty_tree: str | None = None
    for update in updates:
        pushed_refs.append(update.label)
        marker = (
            f"push-update {update.local_ref} {update.local_oid} "
            f"{update.remote_ref} {update.remote_oid}"
        )
        if ZERO_OID_RE.fullmatch(update.local_oid):
            risk_codes.append("remote-ref-deletion")
            diff_parts.append(marker + "\n")
            continue
        if ZERO_OID_RE.fullmatch(update.remote_oid):
            risk_codes.append("new-remote-ref")
            if empty_tree is None:
                empty_tree = _git(
                    root,
                    ["hash-object", "-t", "tree", "--stdin"],
                    input_text="",
                ).stdout.strip()
            range_arg = f"{empty_tree}..{update.local_oid}"
        else:
            ancestry = _git(
                root,
                ["merge-base", "--is-ancestor", update.remote_oid, update.local_oid],
                check=False,
            )
            if ancestry.returncode != 0:
                risk_codes.append("non-fast-forward-push")
            range_arg = f"{update.remote_oid}..{update.local_oid}"
        diff = _git(
            root,
            ["diff", range_arg, "--no-ext-diff", "--no-color", "--unified=0"],
        ).stdout
        names = _git(
            root,
            ["diff", range_arg, "--name-only", "--no-ext-diff"],
        ).stdout
        changed_paths.update(
            line.strip() for line in names.splitlines() if line.strip()
        )
        diff_parts.append(marker + "\n" + diff)
    return (
        "\n".join(diff_parts),
        tuple(sorted(changed_paths)),
        tuple(pushed_refs),
        tuple(risk_codes),
    )


def collect_change_set(
    root: Path,
    stage: str,
    push_updates: str | None = None,
) -> ChangeSet:
    _require_git(root)
    if stage == "pre-commit":
        if push_updates is not None:
            raise ConfigurationError(
                "--push-updates is only valid for pre-push review."
            )
        range_args = ["--cached"]
        diff = _git(
            root,
            ["diff", *range_args, "--no-ext-diff", "--no-color", "--unified=0"],
        ).stdout
        names = _git(
            root,
            ["diff", *range_args, "--name-only", "--no-ext-diff"],
        ).stdout
        changed_paths = tuple(
            sorted({line.strip() for line in names.splitlines() if line.strip()})
        )
        pushed_refs: tuple[str, ...] = ()
        initial_risk_codes: tuple[str, ...] = ()
    elif stage == "pre-push":
        if push_updates is not None:
            updates = parse_push_updates(push_updates)
            if not updates:
                return ChangeSet(
                    scope=stage,
                    diff_digest=digest_bytes(b""),
                    changed_paths=(),
                    additions=0,
                    deletions=0,
                    risk_codes=(),
                    risk_level="none",
                    no_changes=True,
                )
            diff, changed_paths, pushed_refs, initial_risk_codes = _push_diff(
                root, updates
            )
        else:
            range_args = _pre_push_range(root)
            diff = _git(
                root,
                ["diff", *range_args, "--no-ext-diff", "--no-color", "--unified=0"],
            ).stdout
            names = _git(
                root,
                ["diff", *range_args, "--name-only", "--no-ext-diff"],
            ).stdout
            changed_paths = tuple(
                sorted({line.strip() for line in names.splitlines() if line.strip()})
            )
            pushed_refs = ()
            initial_risk_codes = ()
    else:
        raise ConfigurationError(f"Unsupported gate stage: {stage}")

    diff_bytes = diff.encode("utf-8")
    if not diff_bytes and not changed_paths and not pushed_refs:
        return ChangeSet(
            scope=stage,
            diff_digest=digest_bytes(b""),
            changed_paths=(),
            additions=0,
            deletions=0,
            risk_codes=(),
            risk_level="none",
            no_changes=True,
        )

    additions = sum(
        1
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    deletions = sum(
        1
        for line in diff.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    risk_codes: list[str] = list(initial_risk_codes)
    if len(changed_paths) >= 12:
        risk_codes.append("broad-change-surface")
    if additions + deletions >= 400:
        risk_codes.append("large-change")
    if any(HIGH_RISK_PATH_RE.search(path) for path in changed_paths):
        risk_codes.append("high-consequence-path")
    if HIGH_RISK_DIFF_RE.search(diff):
        risk_codes.append("control-flow-or-policy-change")
    added_lines = "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    if re.search(r"\b(?:TODO|FIXME|HACK|temporary)\b", added_lines, re.IGNORECASE):
        risk_codes.append("new-temporary-decision")
    source_paths = [
        path
        for path in changed_paths
        if Path(path).suffix.lower()
        in {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".swift"}
    ]
    test_paths = [
        path
        for path in changed_paths
        if "test" in Path(path).name.lower() or "/tests/" in f"/{path}/"
    ]
    if source_paths and not test_paths:
        risk_codes.append("source-change-without-test-change")
    if source_paths and not any(
        path.lower().endswith((".md", ".mdx")) for path in changed_paths
    ):
        risk_codes.append("source-change-without-knowledge-change")

    unique_codes = tuple(sorted(set(risk_codes)))
    critical_codes = {
        "high-consequence-path",
        "control-flow-or-policy-change",
        "large-change",
        "non-fast-forward-push",
        "remote-ref-deletion",
    }
    if critical_codes & set(unique_codes):
        risk_level = "high"
    elif unique_codes:
        risk_level = "medium"
    else:
        risk_level = "low"
    return ChangeSet(
        scope=stage,
        diff_digest=digest_bytes(diff_bytes),
        changed_paths=changed_paths,
        additions=additions,
        deletions=deletions,
        risk_codes=unique_codes,
        risk_level=risk_level,
        pushed_refs=pushed_refs,
    )


def review_template(change_set: ChangeSet) -> dict[str, Any]:
    return {
        "schemaVersion": "sei.change-review-answers.v1",
        "diffDigest": change_set.diff_digest,
        "answers": {question_id: "" for question_id, _ in REVIEW_QUESTIONS},
    }


def load_review_answers(path: Path, expected_digest: str) -> dict[str, str]:
    value = read_json(path)
    if value.get("schemaVersion") != "sei.change-review-answers.v1":
        raise ConfigurationError("Unsupported change-review answer schema.")
    if value.get("diffDigest") != expected_digest:
        raise ConfigurationError(
            "Review answers do not match the current change digest. Regenerate them."
        )
    answers = value.get("answers")
    if not isinstance(answers, dict):
        raise ConfigurationError("Change-review answers must be an object.")
    normalized: dict[str, str] = {}
    for question_id, _ in REVIEW_QUESTIONS:
        answer = answers.get(question_id)
        if not isinstance(answer, str) or not answer.strip():
            raise ConfigurationError(f"Missing change-review answer: {question_id}")
        normalized[question_id] = answer.strip()
    if normalized["decision"] not in {"proceed", "proceed-with-debt", "stop"}:
        raise ConfigurationError(
            "Change-review decision must be proceed, proceed-with-debt, or stop."
        )
    return normalized


def run_review_interactive(
    change_set: ChangeSet,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> dict[str, str]:
    output_fn(
        f"SEI change review: {change_set.risk_level} risk, "
        f"{len(change_set.changed_paths)} path(s), "
        f"+{change_set.additions}/-{change_set.deletions}"
    )
    if change_set.risk_codes:
        output_fn("Risk signals: " + ", ".join(change_set.risk_codes))
    answers: dict[str, str] = {}
    for index, (question_id, prompt) in enumerate(REVIEW_QUESTIONS, 1):
        output_fn(f"\n[{index}/{len(REVIEW_QUESTIONS)}] {prompt}")
        while True:
            answer = input_fn("> ").strip()
            if answer:
                answers[question_id] = answer
                break
            output_fn("필수 질문입니다.")
    if answers["decision"] not in {"proceed", "proceed-with-debt", "stop"}:
        raise ConfigurationError(
            "Final decision must be proceed, proceed-with-debt, or stop."
        )
    return answers


def record_change_review(
    store: ProjectStore,
    change_set: ChangeSet,
    answers: dict[str, str],
    reviewer: str,
) -> dict[str, Any]:
    if not reviewer.strip():
        raise ConfigurationError("A reviewer identity is required.")
    decision = answers["decision"]
    record = {
        "schemaVersion": "sei.change-review.v1",
        "reviewId": unique_id(
            "change-review",
            {
                "diffDigest": change_set.diff_digest,
                "stage": change_set.scope,
                "reviewer": reviewer,
            },
        ),
        "changeSet": change_set.as_dict(),
        "answers": answers,
        "decision": decision,
        "reviewer": reviewer.strip(),
        "createdAt": utc_now(),
        "limitations": [
            "A review receipt records accountable reasoning, not runtime proof.",
            "Any change to the diff invalidates this receipt.",
        ],
    }
    store.append_change_review(record)
    return record


def evaluate_gate(store: ProjectStore, change_set: ChangeSet) -> dict[str, Any]:
    if change_set.no_changes:
        return {
            "schemaVersion": "sei.gate-result.v1",
            "allowed": True,
            "reason": "no-changes",
            "changeSet": change_set.as_dict(),
            "reviewId": None,
        }
    if change_set.risk_level == "low":
        return {
            "schemaVersion": "sei.gate-result.v1",
            "allowed": True,
            "reason": "low-risk-no-review-required",
            "changeSet": change_set.as_dict(),
            "reviewId": None,
        }
    matching = [
        record
        for record in store.records("change-reviews")
        if record.get("changeSet", {}).get("diffDigest") == change_set.diff_digest
        and record.get("changeSet", {}).get("scope") == change_set.scope
    ]
    if not matching:
        return {
            "schemaVersion": "sei.gate-result.v1",
            "allowed": False,
            "reason": "change-review-required",
            "changeSet": change_set.as_dict(),
            "reviewId": None,
            "nextCommand": f"sei review {store.project_root} --stage {change_set.scope} --reviewer <name>",
        }
    review = matching[-1]
    decision = review.get("decision")
    allowed = decision == "proceed" or (
        decision == "proceed-with-debt"
        and change_set.scope == "pre-commit"
        and change_set.risk_level != "high"
    )
    return {
        "schemaVersion": "sei.gate-result.v1",
        "allowed": allowed,
        "reason": f"review-decision:{decision}",
        "changeSet": change_set.as_dict(),
        "reviewId": review.get("reviewId"),
    }


HOOK_MARKER = "# managed-by-agentlas-sei"


def install_hooks(
    root: Path,
    force: bool = False,
    command: Sequence[str] = ("sei",),
) -> dict[str, Any]:
    _require_git(root)
    git_dir_result = _git(root, ["rev-parse", "--git-dir"])
    git_dir = (root / git_dir_result.stdout.strip()).resolve()
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    backups: list[str] = []
    command_text = " ".join(shlex.quote(part) for part in command)
    quoted_root = shlex.quote(str(root))
    for hook_name, stage in (("pre-commit", "pre-commit"), ("pre-push", "pre-push")):
        path = hooks_dir / hook_name
        if path.exists():
            existing = path.read_text(encoding="utf-8", errors="replace")
            if HOOK_MARKER not in existing:
                if not force:
                    raise ConfigurationError(
                        f"Refusing to overwrite existing {hook_name} hook. "
                        "Use --force to create a backup first."
                    )
                backup = hooks_dir / f"{hook_name}.before-sei"
                if backup.exists():
                    raise ConfigurationError(f"Hook backup already exists: {backup}")
                os.replace(path, backup)
                backups.append(str(backup))
        if stage == "pre-push":
            script = (
                "#!/bin/sh\n"
                f"{HOOK_MARKER}\n"
                'push_updates="${TMPDIR:-/tmp}/agentlas-sei-pre-push.$$"\n'
                "umask 077\n"
                "trap 'rm -f \"$push_updates\"' EXIT HUP INT TERM\n"
                'cat > "$push_updates"\n'
                f"if {command_text} gate {quoted_root} --stage pre-push "
                '--push-updates "$push_updates"; then\n'
                "  exit 0\n"
                "fi\n"
                'if [ -n "${CI:-}" ] || [ "${SEI_NONINTERACTIVE:-0}" = "1" ]; then\n'
                "  exit 4\n"
                "fi\n"
                'reviewer="${SEI_REVIEWER:-${USER:-local-maintainer}}"\n'
                f"{command_text} review {quoted_root} --stage pre-push "
                '--push-updates "$push_updates" --reviewer "$reviewer"\n'
                "exit $?\n"
            )
        else:
            script = (
                "#!/bin/sh\n"
                f"{HOOK_MARKER}\n"
                f"if {command_text} gate {quoted_root} --stage pre-commit; then\n"
                "  exit 0\n"
                "fi\n"
                'if [ -n "${CI:-}" ] || [ "${SEI_NONINTERACTIVE:-0}" = "1" ]; then\n'
                "  exit 4\n"
                "fi\n"
                'reviewer="${SEI_REVIEWER:-${USER:-local-maintainer}}"\n'
                f"exec {command_text} review {quoted_root} --stage pre-commit "
                '--reviewer "$reviewer"\n'
            )
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)
        installed.append(str(path))
    return {"installed": installed, "backups": backups}


def uninstall_hooks(root: Path) -> dict[str, Any]:
    _require_git(root)
    git_dir_result = _git(root, ["rev-parse", "--git-dir"])
    hooks_dir = (root / git_dir_result.stdout.strip()).resolve() / "hooks"
    removed: list[str] = []
    restored: list[str] = []
    for hook_name in ("pre-commit", "pre-push"):
        path = hooks_dir / hook_name
        if path.exists():
            existing = path.read_text(encoding="utf-8", errors="replace")
            if HOOK_MARKER in existing:
                path.unlink()
                removed.append(str(path))
        backup = hooks_dir / f"{hook_name}.before-sei"
        if backup.exists() and not path.exists():
            os.replace(backup, path)
            restored.append(str(path))
    return {"removed": removed, "restored": restored}
