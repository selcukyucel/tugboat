# Tugboat

Tugboat is a governance layer for teams that build software with AI coding agents.
It gives every developer's agent the same rules and team knowledge from one shared team repo, and keeps task history that any machine can resume.
A tugboat guides a large ship into harbour and does not steer it. Tugboat guides the agent and leaves the decisions to the developer.

It runs beside Claude Code, GitHub Copilot CLI, and GitHub Copilot in VS Code. It adds no hosted service.

## Status

Early. This version is the skeleton: the `tug` command exists and `tug version` works.
Every other command is listed and answers "not built yet". Nothing here is ready for daily use.

## Requirements

- macOS
- Git
- [uv](https://docs.astral.sh/uv/). uv fetches a suitable Python when the machine has none. Tugboat needs Python 3.11 or newer.

Tugboat uses only the Python standard library. It has no third-party package at run time.

## Install

```sh
uv tool install git+https://github.com/selcukyucel/tugboat@v0.1.0
tug version
```

Always install from this full address. Tugboat is not published to the Python package index. A package there with the same name is not this tool.

To remove the command: `uv tool uninstall tugboat`.

## Commands

| Command | Does | State |
|---|---|---|
| `tug version` | Prints the installed version | Works |
| `tug init` | Sets up the app repo it is run in | Not built yet |
| `tug sync` | Pulls the team repo and refreshes the local files | Not built yet |
| `tug status` | Prints the session brief | Not built yet |
| `tug doctor` | Checks that this machine and repo are ready | Not built yet |
| `tug update` | Updates `tug` to the newest release | Not built yet |
| `tug expire` | Removes records older than six months, after confirmation | Not built yet |
| `tug remove` | Takes Tugboat out of a repo, or out of every repo on the machine | Not built yet |
| `tug hook` | Called by the agent tool's hooks | Not built yet |

Exit codes: `0` success, `1` error, `2` usage error (agent tools read `2` from a hook as "deny"), `3` command not built yet.

## Development

Run the tests. uv picks a suitable Python and sets up the project in `.venv` the first time:

```sh
uv run python -m unittest discover -s tests
```

Run the command from the source tree:

```sh
uv run tug version
```

### Release

1. Raise `__version__` in `src/tugboat/__init__.py`. A version is three numbers, such as `0.2.0`.
2. Commit on `main`.
3. Run `uv run python tools/release.py`. It checks the tree, runs the tests, and creates the tag `v` plus the version. It does not push.
4. Push the branch and the tag with the command it prints.

## License

MIT. See [LICENSE](LICENSE).
