#!/usr/bin/env bash
set -euo pipefail

source_ref="${1:-}"
if [[ -z "$source_ref" ]]; then
  source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  marketplace_ref="$source_root"
else
  source_root="$source_ref"
  marketplace_ref="$source_ref"
fi

if [[ "$source_root" == http://* || "$source_root" == https://* || "$source_root" == git@* || "$source_root" == file://* ]]; then
  checkout_root="$(mktemp -d)"
  trap 'rm -rf "$checkout_root"' EXIT
  git clone --depth 1 "$source_root" "$checkout_root/source"
  source_root="$checkout_root/source"
  if [[ "$marketplace_ref" == file://* ]]; then
    marketplace_ref="${marketplace_ref#file://}"
  fi
fi

if [[ ! -f "$source_root/pyproject.toml" || ! -f "$source_root/.agents/plugins/marketplace.json" ]]; then
  echo "SEI installer: source is not an Agentlas SEI package: $source_root" >&2
  exit 2
fi

if command -v uv >/dev/null 2>&1; then
  uv tool install --force --reinstall "$source_root"
elif command -v pipx >/dev/null 2>&1; then
  pipx install --force "$source_root"
else
  python3 -m pip install --user "$source_root"
fi

mkdir -p "$HOME/.claude/commands"
cp "$source_root/.claude/commands/sei.md" "$HOME/.claude/commands/sei.md"
mkdir -p "$HOME/.gemini/commands"
cp "$source_root/gemini/extension/commands/sei.toml" "$HOME/.gemini/commands/sei.toml"
mkdir -p "$HOME/.gemini/antigravity/global_workflows"
cp "$source_root/antigravity/workflows/sei.md" "$HOME/.gemini/antigravity/global_workflows/sei.md"

if command -v codex >/dev/null 2>&1; then
  codex_home="${CODEX_HOME:-$HOME/.codex}"
  mkdir -p "$codex_home"
  if ! codex plugin marketplace add "$marketplace_ref"; then
    echo "SEI installer: Codex marketplace installation failed." >&2
    exit 3
  fi
  if ! codex plugin add agentlas-sei@agentlas-sei; then
    echo "SEI installer: Codex plugin installation failed." >&2
    exit 3
  fi
fi

echo "SEI terminal installed: sei <project-folder>"
echo "Claude Code: /sei <project-folder>"
echo "Gemini CLI: /sei <project-folder>"
echo "Antigravity: /sei <project-folder>"
echo "Codex invocation after plugin install: \$sei <project-folder>"
