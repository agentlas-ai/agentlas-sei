from __future__ import annotations

import ast
import io
import os
import re
import subprocess
import tokenize
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .util import digest_bytes, digest_value, utc_now

DEFAULT_EXCLUDES = {
    ".git",
    ".agentlas",
    ".bug-hunter",
    ".hg",
    ".lazyweb",
    ".next",
    ".nox",
    ".nuxt",
    ".playwright-mcp",
    ".svelte-kit",
    ".svn",
    ".sei",
    ".tox",
    ".venv",
    "venv",
    "node_modules",
    "target",
    "dist",
    "build",
    "coverage",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

SECRET_NAMES = {
    ".env",
    ".env.local",
    "credentials.json",
    "service-account.json",
    "serviceaccountkey.json",
}

SECRET_SUFFIXES = {".jks", ".key", ".p12", ".pem", ".pfx"}
SECRET_DIRECTORIES = {".aws", ".gcp", ".ssh", "credentials", "signing"}

TEXT_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".jsx",
    ".json",
    ".kt",
    ".kts",
    ".md",
    ".mjs",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}

CODE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".kts",
    ".mjs",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".swift",
    ".ts",
    ".tsx",
    ".vue",
}

MAX_FILE_BYTES = 1_000_000
MAX_SCAN_BYTES = 32_000_000
MAX_FILES = 20_000

IMPORT_RE = re.compile(
    r"""(?:from\s+["']([^"']+)["']|import\s+(?:[^"']+\s+from\s+)?["']([^"']+)["']|require\(["']([^"']+)["']\))"""
)
FUNCTION_RE = re.compile(
    r"""(?:function|class|def|async\s+function)\s+([A-Za-z_$][A-Za-z0-9_$]*)"""
)


@dataclass
class ScanBudget:
    files: int = 0
    bytes_read: int = 0
    truncated: bool = False
    listing_source: str = "filesystem"


def language_for(path: Path) -> str:
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".mjs": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".kt": "kotlin",
        ".swift": "swift",
        ".rb": "ruby",
        ".php": "php",
        ".cs": "csharp",
        ".c": "c",
        ".cc": "cpp",
        ".cpp": "cpp",
        ".sh": "shell",
        ".sql": "sql",
    }
    return mapping.get(path.suffix.lower(), "other")


def _git_metadata(root: Path) -> dict[str, Any]:
    if not (root / ".git").exists():
        return {"kind": "content-snapshot", "commit": None, "dirty": None}
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        )
        return {"kind": "git", "commit": commit, "dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"kind": "git-unavailable", "commit": None, "dirty": None}


def _excluded_path(path: Path, relative_parts: tuple[str, ...]) -> bool:
    lowered_parts = tuple(part.lower() for part in relative_parts)
    name = path.name.lower()
    return (
        any(part in DEFAULT_EXCLUDES for part in relative_parts)
        or any(part in SECRET_DIRECTORIES for part in lowered_parts[:-1])
        or name in SECRET_NAMES
        or name.startswith(".env.")
        or path.suffix.lower() in SECRET_SUFFIXES
    )


def _accept_file(
    path: Path,
    relative_parts: tuple[str, ...],
    budget: ScanBudget,
) -> bool:
    if _excluded_path(path, relative_parts) or path.is_symlink():
        return False
    try:
        stat = path.stat()
    except OSError:
        return False
    if not path.is_file() or stat.st_size > MAX_FILE_BYTES:
        return False
    if path.suffix.lower() not in TEXT_EXTENSIONS and path.name not in {
        "Dockerfile",
        "Makefile",
        "README",
        "LICENSE",
    }:
        return False
    if budget.files >= MAX_FILES or budget.bytes_read + stat.st_size > MAX_SCAN_BYTES:
        budget.truncated = True
        return False
    budget.files += 1
    budget.bytes_read += stat.st_size
    return True


def _git_files(root: Path, budget: ScanBudget) -> list[Path] | None:
    try:
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            cwd=root,
            check=False,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    budget.listing_source = "git"
    files: list[Path] = []
    relative_names = sorted(
        os.fsdecode(item) for item in result.stdout.split(b"\0") if item
    )
    for relative_name in relative_names:
        pure = PurePosixPath(relative_name)
        if pure.is_absolute() or ".." in pure.parts:
            continue
        path = root.joinpath(*pure.parts)
        if _accept_file(path, pure.parts, budget):
            files.append(path)
        if budget.truncated:
            break
    return files


def _safe_files(root: Path, budget: ScanBudget) -> list[Path]:
    git_files = _git_files(root, budget)
    if git_files is not None:
        return git_files

    files: list[Path] = []
    for current_root, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(
            name
            for name in directories
            if name not in DEFAULT_EXCLUDES
            and not (Path(current_root) / name).is_symlink()
        )
        for name in sorted(names):
            path = Path(current_root) / name
            relative_parts = path.relative_to(root).parts
            if _accept_file(path, relative_parts, budget):
                files.append(path)
            if budget.truncated:
                return files
    return files


def _read_text(path: Path) -> tuple[str, bytes] | None:
    try:
        raw = path.read_bytes()
        return raw.decode("utf-8"), raw
    except (OSError, UnicodeDecodeError):
        return None


def _python_symbols(text: str) -> tuple[list[str], list[str]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [], []
    symbols: list[str] = []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(node.name)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return sorted(set(symbols))[:500], sorted(set(imports))[:500]


def _generic_symbols(text: str) -> tuple[list[str], list[str]]:
    symbols = sorted(set(FUNCTION_RE.findall(text)))[:500]
    imports = sorted(
        {
            next(group for group in match.groups() if group)
            for match in IMPORT_RE.finditer(text)
        }
    )[:500]
    return symbols, imports


def _risk_signals(text: str) -> dict[str, int]:
    lowered = text.lower()
    return {
        "fallback": len(re.findall(r"\bfallback\b", lowered)),
        "retry": len(re.findall(r"\bretr(?:y|ies|ied|ying)\b", lowered)),
        "todo": len(re.findall(r"\b(?:todo|fixme|hack|temporary)\b", lowered)),
        "silent_catch": len(
            re.findall(
                r"(?:except\s+(?:exception)?\s*:\s*pass|catch\s*(?:\([^)]*\))?\s*\{\s*\})",
                lowered,
            )
        ),
    }


def _broad_exception_type(node: ast.expr | None) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Name):
        return node.id in {"BaseException", "Exception"}
    if isinstance(node, ast.Tuple):
        return any(_broad_exception_type(item) for item in node.elts)
    return False


def _python_risk_signals(text: str) -> dict[str, int]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _risk_signals(text)
    identifiers: list[str] = []
    silent_catch = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.append(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.append(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            identifiers.append(node.name)
        elif (
            isinstance(node, ast.ExceptHandler)
            and len(node.body) == 1
            and isinstance(node.body[0], ast.Pass)
            and _broad_exception_type(node.type)
        ):
            silent_catch += 1
    comments: list[str] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                comments.append(token.string)
    except (tokenize.TokenError, IndentationError):
        return _risk_signals(text)
    identifier_text = "\n".join(identifiers).lower()
    comment_text = "\n".join(comments).lower()
    return {
        "fallback": len(re.findall(r"fallback", identifier_text)),
        "retry": len(re.findall(r"retr(?:y|ies|ied|ying)", identifier_text)),
        "todo": len(re.findall(r"\b(?:todo|fixme|hack|temporary)\b", comment_text)),
        "silent_catch": silent_catch,
    }


def scan_project(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    budget = ScanBudget()
    files = _safe_files(root, budget)
    project_nodes: list[dict[str, Any]] = []
    code_files: list[dict[str, Any]] = []
    languages: dict[str, int] = {}
    aggregate_signals = {"fallback": 0, "retry": 0, "todo": 0, "silent_catch": 0}

    for path in files:
        relative = path.relative_to(root).as_posix()
        read = _read_text(path)
        if read is None:
            continue
        text, raw = read
        digest = digest_bytes(raw)
        kind = "code" if path.suffix.lower() in CODE_EXTENSIONS else "document"
        if (
            path.name.lower().startswith(("test_", "spec."))
            or "/tests/" in f"/{relative}/"
        ):
            kind = "test"
        elif path.name in {"pyproject.toml", "package.json", "Cargo.toml", "go.mod"}:
            kind = "manifest"
        elif path.suffix.lower() in {".yaml", ".yml", ".toml"}:
            kind = "configuration"
        project_nodes.append(
            {
                "path": relative,
                "kind": kind,
                "bytes": len(raw),
                "contentDigest": digest,
            }
        )
        if path.suffix.lower() not in CODE_EXTENSIONS:
            continue
        language = language_for(path)
        languages[language] = languages.get(language, 0) + 1
        if language == "python":
            symbols, imports = _python_symbols(text)
        else:
            symbols, imports = _generic_symbols(text)
        signals = (
            _python_risk_signals(text) if language == "python" else _risk_signals(text)
        )
        if kind != "test":
            for key, value in signals.items():
                aggregate_signals[key] += value
        code_files.append(
            {
                "path": relative,
                "kind": kind,
                "language": language,
                "contentDigest": digest,
                "symbols": symbols,
                "imports": imports,
                "riskSignals": signals,
                "entryPoint": path.name
                in {
                    "main.py",
                    "cli.py",
                    "app.py",
                    "server.py",
                    "index.js",
                    "index.ts",
                    "main.ts",
                    "main.go",
                    "main.rs",
                },
            }
        )

    project_fingerprint = digest_value(
        [
            {"path": node["path"], "digest": node["contentDigest"]}
            for node in project_nodes
        ]
    )
    git = _git_metadata(root)
    generated_at = utc_now()
    project_map = {
        "schemaVersion": "sei.project-map.v1",
        "project": root.name,
        "generatedAt": generated_at,
        "projectFingerprint": project_fingerprint,
        "sourceIdentity": git,
        "coverage": {
            "files": len(project_nodes),
            "bytesRead": budget.bytes_read,
            "truncated": budget.truncated,
            "listingSource": budget.listing_source,
            "maxFiles": MAX_FILES,
            "maxBytes": MAX_SCAN_BYTES,
        },
        "nodes": project_nodes,
    }
    code_map = {
        "schemaVersion": "sei.code-map.v1",
        "project": root.name,
        "generatedAt": generated_at,
        "projectFingerprint": project_fingerprint,
        "languages": languages,
        "riskSignals": aggregate_signals,
        "files": code_files,
    }
    return project_map, code_map
