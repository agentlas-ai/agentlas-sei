---
name: sei
description: "Use when an existing software project needs a product-intent interview, macro/meso/micro user-flow map, technical and cognitive risk inspection, local dashboard, or risky-change gate."
---

# SEI

## Procedure

1. Read the project `AGENTS.md` and preserve its existing rules.
2. Resolve the requested local project folder.
3. Run `sei <project-folder>` for the full guided flow. When the user asks only
   to reopen the result, run `sei dashboard <project-folder> --lang <locale>`.
   Supported locales are `auto`, `ko`, `en`, `ja`, and `zh`.
4. If the project has no interview, let the built-in interviewer finish before
   inspection.
5. Report the tokenized local dashboard URL.
6. Call every static result a candidate, not a confirmed bug.
7. If the user asks to change code, obtain separate authority and use
   `sei review` for the exact staged diff when required.

## Privacy

Do not send source code, file paths, interview answers, `.sei/` state,
credentials, or raw logs to an LLM, cloud service, or Agentlas Hub. An optional
LLM is allowed only when the user explicitly configures it; it receives bounded
counts and summaries only.

## Output

Return dashboard URL, inspection report, flow coverage, candidate counts,
confirmed bug count, evidence limits, and the next falsifiable check.
