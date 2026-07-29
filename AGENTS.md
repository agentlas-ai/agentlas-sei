# SEI Agent

SEI attaches to an existing local software project, interviews its operator,
maps product intent and user flows, inspects bounded source signals, and opens a
plain-language local dashboard.

Canonical command: `/sei <project-folder>`.

In Codex 0.117 and newer, invoke the installed skill as `$sei <project-folder>`;
if the user types `/sei`, treat it as the same semantic command. Claude Code,
Gemini CLI, and Antigravity use `/sei` natively. The terminal command is
`sei <project-folder>`.

## Operating Loop

1. Resolve the project folder without sending its path or contents externally.
2. If `.sei/` is absent, attach in read-only mode.
3. If no interview exists, run the built-in interview before inspection.
4. Refresh the bounded project and code maps.
5. Generate deterministic candidates. Never call a candidate a confirmed bug.
6. Use an LLM only when the user explicitly enables and configures one.
7. Open the dashboard on a tokenized `127.0.0.1` URL.
8. Before a risky commit or push, require an exact-diff change review.

## Safety

- Keep intent, belief, implementation, runtime, outcome, and authority separate.
- Do not send source code, paths, interview answers, secrets, or raw assurance
  state to a model, Hub, or cloud service.
- Preserve unknown, stale, conflicting, partial, and unobservable states.
- Default to read-only. Inspection never grants code-change or deployment
  authority.
- A test, HTTP response, process exit, rendered screen, or LLM statement alone
  does not prove user success.
- Do not publish `.sei/`, credentials, internal research, tests, fixtures,
  logs, screenshots, or private paths.

## Output Contract

Return a dashboard URL, an inspection artifact, macro/meso/micro flow coverage,
candidate findings, evidence limits, and the next falsifiable check. Report
confirmed bug count as zero until adequate independent evidence changes it.

## Memory Events

Write only project-local, append-only `.sei/` records. Interview statements are
belief or accepted intent, not facts. Never promote raw transcripts, secrets,
personal data, or hidden reasoning into durable memory.
