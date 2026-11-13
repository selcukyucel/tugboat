# Tugboat

Tugboat is a governance layer for teams that build software with AI coding agents.
It gives every developer's agent the same rules and team knowledge from one shared team repo, and keeps task history that any machine can resume.
A tugboat guides a large ship into harbour and does not steer it. Tugboat guides the agent and leaves the decisions to the developer.

It runs beside Claude Code, GitHub Copilot CLI, and GitHub Copilot in VS Code. It adds no hosted service.

## Status

Early. This version can start and check a team repo, keep a local clone of it up to date, keep a read-only copy of its content inside an app repo, and generate the files the agent tools load. It syncs at the start of each agent session. It does not yet check or block anything the agent does.
A command that is not written yet is listed and answers "not built yet". Nothing here is ready for daily use.

## Requirements

- macOS
- Git
- [uv](https://docs.astral.sh/uv/). uv fetches a suitable Python when the machine has none. Tugboat needs Python 3.11 or newer.

Tugboat uses only the Python standard library. It has no third-party package at run time.

## Install

```sh
uv tool install git+https://github.com/selcukyucel/tugboat@v0.7.0
tug version
```

Always install from this full address. Tugboat is not published to the Python package index. A package there with the same name is not this tool.

To remove the command: `uv tool uninstall tugboat`.

## Commands

| Command | Does | State |
|---|---|---|
| `tug version` | Prints the installed version | Works |
| `tug team new <folder>` | Writes the starter layout of a team repo into an empty folder | Works |
| `tug team check [folder]` | Checks every file Tugboat reads in a team repo | Works |
| `tug team propose` | Proposes a change to the team repo as a pull request | Works |
| `tug init` | Sets up the app repo it is run in | Partly. It detects the repo, asks you to confirm, and stops. It changes nothing yet. |
| `tug sync` | Brings the team repo clone, an app repo's copy of it, and the agent tool files up to date | Works. The first run needs `--team-repo` and `--app-repo`. |
| `tug status` | Prints the session brief | Not built yet |
| `tug doctor` | Checks that this machine and repo are ready | Not built yet |
| `tug update` | Updates `tug` to the newest release | Not built yet |
| `tug expire` | Removes records older than six months, after confirmation | Not built yet |
| `tug remove` | Takes Tugboat out of a repo, or out of every repo on the machine | Not built yet |
| `tug hook session-start` | Called by the agent tool at the start of a session. Syncs and reports to the agent. | Works |

Exit codes: `0` success, `1` error, `2` usage error (agent tools read `2` from a hook as "deny"), `3` command not built yet.

## The team repo

Each project keeps its rules, permissions, knowledge, and decisions in one team repo of its own. Tugboat ships with none.

To start one:

1. Create an empty repo in GitHub and clone it.
2. Run `tug team new <the clone>`. It writes the starter layout. It commits nothing, pushes nothing, and creates nothing in GitHub.
3. Review the files, commit, and push.
4. In GitHub, protect `main` so that it changes only through a pull request.

The starter layout explains itself: its `README.md` gives the layout, and each folder's README gives that folder's file format.
Structured files are JSON. Rules, notes, decision records, and agent definitions are Markdown files with a small header.

`tug team check` checks all of them and prints one line per problem. It exits with `1` when a file is not valid. The starter layout runs the same check on every pull request.

## Proposing a change to the team repo

Team content changes only through a pull request that a person merges. `tug team propose` makes one:

```sh
tug team propose --title "Add a note on token refresh" \
  --body "Found while fixing the login bug." \
  --file knowledge/auth/tokens.md=./tokens.md
```

It pushes a branch named `tug/...` with the files, and opens the pull request with the GitHub command-line tool `gh` when that is installed. Otherwise it prints the link to open it.

- It never merges, and it never pushes to `main`.
- It asks you to confirm first. `--yes` skips the question.
- It works in a temporary copy. The clone that sync keeps stays on `main`, and no app repo is written to.
- It only changes team content: the profile, rules, permissions, knowledge, decisions, the estate map, agents, and skills. It refuses to touch CI workflows.
- A change that would fail `tug team check` is refused before anything is pushed. A problem that was already on `main` does not block a fix.
- `--remove PATH` removes a file. `--file` and `--remove` can be given more than once.
- Git must know your name and email. Tugboat does not let Git make one up from the machine's name.

## Init

Run `tug init` inside an existing app repo. It asks nothing about the repo. It reads:

- the GitHub address, from the remote called `origin`
- the default branch, from the clone's own record of the remote, or from GitHub when the clone has none
- the platform: iOS when the repo has an Xcode project or workspace, Android when it has Gradle files of an Android project

It shows what it found and asks you to confirm. `--yes` skips the question.

It stops with the reason, and changes nothing, when:

- the folder is not inside a Git repo, or is a team repo
- the repo has no remote, or the remote is not a GitHub address
- no iOS or Android project is found. Gradle files alone do not count: a backend can use Gradle too.
- the repo holds both an iOS and an Android project. Then run it with `--platform ios` or `--platform android`.

Projects under `Pods/`, `Carthage/`, `node_modules/`, and similar folders are not counted. A token inside a remote URL is never shown or saved.

The steps after detection are not built yet: choosing the team repo and the project's settings.

## Sync

```sh
tug sync --team-repo git@github.com:owner/name.git --app-repo path/to/app   # the first time
tug sync                                                                     # after that, inside the app repo
```

Sync does three things.

**It keeps a clone of the team repo** at `~/.tugboat/team-repos/<host>/<owner>/<name>`.

- A sync that fails does not stop work. It says why, and gives the time of the last sync that worked.
- The clone only moves forward to what GitHub has. Nothing is merged, rebased, or reset.
- Local edits in the clone are never discarded. They are reported.
- Git is never allowed to wait for a password or a prompt, because sync will run where nobody can answer.

**It keeps a read-only copy of the team content inside the app repo**, at `.tugboat/team/`. The agent reads team content from there, so no agent tool has to read outside its workspace.

- The copy holds the last approved revision that sync fetched. A local edit or a local commit in the clone never reaches it.
- It is rewritten only when the team repo revision or the tool version changed. Otherwise no file is touched.
- The files are read-only. To change a rule or a note, open a pull request in the team repo.
- Git ignores `.tugboat/`. Tugboat adds it to the repo's local exclude file and touches no tracked file.
- Links in the team repo are not copied.

**It generates the files the agent tools load**, once, under `.claude/`. Claude Code, GitHub Copilot CLI, and GitHub Copilot in VS Code all read them, so nothing is written twice.

| File | What it is |
|---|---|
| `.claude/rules/tugboat.md` | The instruction file. It tells the agent where the team content is and how to use it. |
| `.claude/skills/<name>/` | Each skill from the team repo |
| `.claude/agents/<name>.md` | Each approved agent definition from the team repo |
| `.claude/settings.local.json` | Tugboat adds one hook here, so that each session starts with a sync |

- A second sync with nothing new writes no file.
- Tugboat never overwrites a file it did not write, and never changes a file the repo tracks. It reports the path.
- `.claude/settings.local.json` is yours. Tugboat changes only its own hook entries, which it recognises by their command, and keeps every other key and hook.
- Every generated file is listed in the repo's local exclude file, so `git status` stays clean.
- The hook calls `tug` by its full path, so it does not depend on the agent tool's `PATH`.

At the start of a session the hook runs a sync and hands its report to the agent. A sync that fails is reported in one line and the session goes on. Outside a repo that Tugboat was set up in, the hook does nothing.

The two options are a stopgap until `tug init` exists.

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
