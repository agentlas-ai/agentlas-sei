---
description: Run SEI against an existing local software project
---

# /sei

1. Resolve the requested project folder, defaulting to the current folder.
2. Run `sei "<project-folder>"` for the guided flow. For dashboard-only use,
   run `sei dashboard "<project-folder>" --lang auto`; supported overrides are
   `ko`, `en`, `ja`, and `zh`.
3. Complete the built-in interview before inspection.
4. Return the local dashboard URL, flow coverage, candidates, confirmed bug
   count, evidence limits, and next check.
5. Do not mutate application code or deploy without a separate user request.
