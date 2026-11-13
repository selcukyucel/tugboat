# Permissions

Two files. Tugboat turns them into the settings and checks each agent tool obeys. A change here needs a pull request.

## `protected-paths.json`

Files the agent must not edit without asking. Each entry has:

| Field | Meaning |
|---|---|
| `pattern` | A path pattern inside an app repo. `*` matches within one folder name, `**` matches any number of folders. |
| `platform` | `ios`, `android`, or `any` |
| `reason` | Why the path is protected. The developer sees this text. |

Tugboat always protects its own generated files as well. They are not listed here.

## `ai-tools.json`

The approved AI tools, models, and MCP servers. The setup check reports anything configured on a machine that is not on this list.

Each entry has an `id`, a `name`, and an optional `note`.
