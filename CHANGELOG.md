# Changelog

## 0.3.0 - 2026-07-30

- Rebuilt the local dashboard as a minimal split-view diagnostic workspace
  focused on detected issues, user-flow hierarchy, and code-link coverage.
- Removed the large hero, local path, operator/reviewer status, repeated metric
  cards, limitations section, and footer from the reader-facing dashboard.
- Added automatic locale detection and live Korean, English, Japanese, and
  Simplified Chinese switching.
- Added `--lang auto|ko|en|ja|zh` to both `sei run` and `sei dashboard`.
- Added safe VS Code links only for relative source paths that resolve inside
  the selected project; the server still exposes no project files.

## 0.2.0 - 2026-07-29

- Added a one-command guided flow: `sei <project>` now attaches an unconfigured
  project, starts the built-in interview when needed, refreshes maps, inspects,
  and opens the result.
- Added a dependency-free, plain-Korean local dashboard served only on
  `127.0.0.1` behind a per-process random URL token.
- Kept deterministic candidates visibly separate from confirmed bugs and
  exposed macro, meso, and micro user flows in non-technical language.
- Extended the built-in interview with per-journey follow-ups for intermediate
  processes and small observable steps.
- Added explicit entry/exit code-link slots to every small flow; unmapped links
  remain visible instead of being guessed.

## 0.1.2 - 2026-07-29

- Limited critical Python silent-catch detection to bare, `Exception`, and
  `BaseException` handlers so narrow compatibility catches are not overstated.
- Added ranked file paths and per-file counts to fallback and silent-catch
  observations.

## 0.1.1 - 2026-07-29

- Made Git repositories scan tracked files plus non-ignored untracked files
  instead of traversing ignored build and tool state.
- Excluded common generated directories and credential/key material even when
  they are accidentally tracked.
- Added regression coverage for ignored build-directory false positives found
  during the first external-project installation.

## 0.1.0 - 2026-07-29

- Added standalone local-first `sei` CLI.
- Added bounded project and code maps.
- Added built-in intent interviewer and claim/flow candidates.
- Added append-only local evidence and finding store.
- Added deterministic contradiction and debt candidates.
- Added opt-in OpenAI-compatible LLM hypothesis generation.
- Added exact-diff cognitive and technical debt reviews.
- Added managed pre-commit and exact Git-ref/OID pre-push gates, including
  new-branch, multi-ref, non-fast-forward, and deletion handling.
- Separated test-fixture risk signals from the product-risk aggregate while
  preserving per-file evidence.
- Added project validation, doctor, reports, and self-debt audit.
