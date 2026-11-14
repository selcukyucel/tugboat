# Permissions

Two files. Tugboat turns them into the settings and checks each agent tool obeys. A change here needs a pull request.

A third file, `hook-limits.md`, says what those checks cannot do. Read it before you rely on them.

## `protected-paths.json`

Files the agent must not edit without asking. Each entry has:

| Field | Meaning |
|---|---|
| `pattern` | A path pattern inside an app repo. `*` matches within one folder name, `**` matches any number of folders. |
| `platform` | `ios`, `android`, or `any` |
| `reason` | Why the path is protected. The developer sees this text. |

Tugboat always protects its own generated files as well. They are not listed here.

At every sync Tugboat compiles this list for each app repo: the entries for `any` and for the repo's own platform.
Claude Code gets a rule that makes it ask the developer before it edits a protected file. Every agent tool gets the list in its instruction file, and `tug protected <path>` answers for one path.
A new entry is in force after the next sync. Nothing is committed in the app repo.

## `ai-tools.json`

The register of approved AI tools. It has three groups:

| Group | Holds |
|---|---|
| `agent_tools` | The agent tools the team works with, such as Claude Code |
| `models` | The models the team may use |
| `mcp_servers` | The MCP servers an agent tool may be connected to |

**One rule for all three groups.** A group with entries is the whole list: anything else in that group is not approved. An empty group, `[]`, means the team has not made that list yet, so nothing in it is ruled out. The starter leaves `models` empty for that reason.

Each entry has an `id`, a `name`, and an optional `note`.

An MCP server entry also says how the server is recognised on a developer's machine:

| Field | Meaning | Example |
|---|---|---|
| `hosts` | For a remote server: the host names of its address | `["api.githubcopilot.com"]` |
| `commands` | For a server that runs on the machine: the program, or the package it runs. A version after the name is ignored. | `["figma-developer-mcp"]` |

The name a developer gives a server in their own settings does not count. Anyone can call any server "github".

```json
{
  "schema": "tug.ai-tools/1",
  "agent_tools": [{"id": "claude-code", "name": "Claude Code"}],
  "models": [],
  "mcp_servers": [
    {"id": "github", "name": "GitHub MCP server", "hosts": ["api.githubcopilot.com"]}
  ]
}
```

The agent is given this list at the start of each session and is told not to add or suggest anything that is not on it. The setup check, which is not built yet, will report an MCP server that is configured on a machine and is not on the list.

Adding a tool, a model, or a server is a pull request in this repo that a person approves.
