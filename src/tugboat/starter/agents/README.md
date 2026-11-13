# Agent definitions

The agents this project has approved. One file per agent, named after it, such as `reviewer.md`.
Tugboat installs them into each developer's agent tool. An agent definition found in an app repo that is not listed here is reported by the setup check.

```
---
name: reviewer
description: Reads a finished change and reports findings. It edits no file.
tools: ["Read", "Grep", "Glob"]
rules: ["COM-001", "IOS-001"]
---

The instructions the agent follows.
```

| Field | Meaning |
|---|---|
| `description` | The agent's purpose, in a sentence or two |
| `tools` | The tools it may use. Leave out edit and write tools for an agent that only reads. |
| `rules` | The rule IDs it applies. Each must exist in `policies/`. |
