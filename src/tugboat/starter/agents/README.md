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
| `rules` | The rule IDs it applies. Each must exist in `policies/`. `[]` for an agent that applies every adopted rule of the repo it runs in. |

## How a definition is used

Sync installs each definition into `.claude/agents/` in every app repo, which Claude Code, Copilot CLI, and Copilot in VS Code read. It also lists the approved agents in the instruction file.
A definition that names rules of one platform only is installed in that platform's repos only.
A definition changes only through a pull request here.

## The reviewer

`reviewer.md` ships with the starter layout. It reviews a finished change against the adopted rules, the decision records, and the knowledge notes, and reports findings.
It may use `Read`, `Grep`, and `Glob` only. It has no edit tool, no write tool, and no shell, so it can change no file. The agent that calls it must hand it the list of changed files and the diff.
