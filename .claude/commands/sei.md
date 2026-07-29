---
description: Interview, map, inspect, and visualize a local software project
argument-hint: '<project-folder>'
allowed-tools: Bash, Read
---

# /sei

Run `sei "$ARGUMENTS"` when arguments are present, otherwise run `sei .`.
Allow the built-in interview to finish before inspection. Return the local
dashboard URL and explain that all static findings remain candidates until
runtime and outcome evidence confirms them. Do not mutate the target project
except for project-local `.sei/` state or explicitly requested Git hooks.
For a dashboard-only request, run
`sei dashboard "<project-folder>" --lang auto`; supported overrides are
`ko`, `en`, `ja`, and `zh`.
