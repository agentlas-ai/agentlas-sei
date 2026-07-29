---
name: sei
description: "Use for /sei-style requests to interview, map, inspect, or visualize an existing local software project."
---

# SEI

Run the installed terminal entrypoint:

```bash
sei "<project-folder>"
```

The guided command must attach read-only state, run the built-in interview when
no interview exists, refresh maps, inspect deterministic candidates, and open
the tokenized localhost dashboard.

To reopen the dashboard without another inspection, run:

```bash
sei dashboard "<project-folder>" --lang auto
```

The dashboard supports `auto`, `ko`, `en`, `ja`, and `zh`, and can switch
languages while it is running.

Never label a scanner candidate as a confirmed bug. Never transmit source,
paths, interview answers, credentials, or `.sei/` state. LLM use is explicit
and bounded. Code mutation and deployment require a separate user request.

In Codex 0.117 and newer, this skill is invoked as `$sei`. If a user types
`/sei`, treat it as the same semantic request because custom slash prompts were
removed from current Codex.
