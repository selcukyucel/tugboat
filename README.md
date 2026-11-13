# Tugboat

Tugboat is a governance layer for teams that build software with AI coding agents.
It gives every developer's agent the same rules and team knowledge from one shared team repo, and keeps task history that any machine can resume.
A tugboat guides a large ship into harbour and does not steer it. Tugboat guides the agent and leaves the decisions to the developer.

It runs beside Claude Code, GitHub Copilot CLI, and GitHub Copilot in VS Code. It adds no hosted service.

## Status

Early. This version can start and check a team repo, set up an app repo with `tug init`, save the project's choices in the team repo, keep a read-only copy of the team content inside the app repo, and generate the files the agent tools load. It syncs at the start of each agent session. It does not yet check or block anything the agent does.
A command that is not written yet is listed and answers "not built yet". Nothing here is ready for daily use.

## Requirements

- macOS
- Git
- [uv](https://docs.astral.sh/uv/). uv fetches a suitable Python when the machine has none. Tugboat needs Python 3.11 or newer.

Tugboat uses only the Python standard library. It has no third-party package at run time.

## Install

```sh
uv tool install git+https://github.com/selcukyucel/tugboat@v0.9.0
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
| `tug init` | Sets up the app repo it is run in | Works. The CI workflows are not built yet. |
| `tug profile show` | Prints the project profile this machine works from | Works |
| `tug profile set-criteria` | Saves where the project's stories keep their acceptance criteria. Run by the agent in the first session. | Works. Not yet tried with a real tracker. |
| `tug sync` | Brings the team repo clone, an app repo's copy of it, and the agent tool files up to date | Works |
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
- The push can only create the new branch. It never moves a branch that is already in GitHub.

## Init

Run `tug init` inside an existing app repo. It does five things, in this order. Everything that can stop it happens before anything is written into the repo.

**1. It detects the repo.** It asks nothing about it. It reads:

- the GitHub address, from the remote called `origin`
- the default branch, from the clone's own record of the remote, or from GitHub when the clone has none
- the platform: iOS when the repo has an Xcode project or workspace, Android when it has Gradle files of an Android project

It shows what it found and asks you to confirm.

**2. It asks for the address of the team repo**, and brings its clone up to date. The team repo must exist: init creates nothing in GitHub. When this machine already has exactly one team repo, that one is offered.

**3. It reads the project profile.** The profile is `profile.json` in the team repo. It holds the project's choices and the list of its app repos.

- The first init for a project asks the project questions: the project's name, the tracker (Jira or GitHub Projects), the optional CI checks (any, or none), the agent tools the team uses, and who approves a change to the team repo (a teammate, or the owner of a solo project).
- A later init, in another repo or on another machine, asks none of them. It reads the saved profile.
- `tug init --change` asks the questions again, with the saved answers as the defaults. An option such as `--tracker jira` changes one choice without a question.

**4. It proposes the profile as a pull request in the team repo.** A new profile, a changed choice, and a newly added app repo each go to `main` through a pull request that a person merges. Init shows the change and asks before it opens the pull request.

- Until the pull request is merged, Tugboat works from its own copy of the proposed profile on this machine. The copy is dropped as soon as `main` holds the same profile.
- When `main` gets a different profile in the meantime, the approved profile wins and the local copy is set aside.
- Merge the first profile pull request before the next person runs init. Otherwise that person is asked the project questions too, and two pull requests propose two profiles.
- When the pull request was closed without a merge, `tug init --propose-again` opens a new one.
- When the pull request cannot be made, for example offline, the repo is still set up and init exits with `1`. Run `tug init` again to propose.

**5. It sets up the repo**: the read-only copy of the team content at `.tugboat/team/` and the agent tool files under `.claude/`. See Sync below. A second run with nothing new changes no file and opens no pull request.

Without a terminal, give the answers as options:

```sh
tug init --yes --team-repo git@github.com:owner/name.git \
  --tracker github-projects --approval owner --ci none --surfaces claude-code,copilot-cli
```

| Option | Meaning |
|---|---|
| `--yes` | Answers yes to every confirmation and asks no question. At the first init, `--tracker` and `--approval` must then be given: they have no default. |
| `--team-repo ADDRESS` | The team repo. Not needed in a repo that is already set up. |
| `--platform ios` or `android` | Only for a repo that holds both an iOS and an Android project |
| `--project NAME` | The project's name, at the first init. The default comes from the team repo's name. |
| `--tracker`, `--ci`, `--surfaces`, `--approval` | The project's choices. `--ci` and `--surfaces` take a list separated by commas. `--ci none` selects no CI check. |
| `--change` | Ask the project questions again |
| `--propose-again` | Open a new pull request for a profile change that waits |

Init stops with the reason, and changes nothing in the repo, when:

- the folder is not inside a Git repo, or is a team repo
- the repo has no remote, or the remote is not a GitHub address
- no iOS or Android project is found. Gradle files alone do not count: a backend can use Gradle too.
- the repo holds both an iOS and an Android project. Then run it with `--platform ios` or `--platform android`.
- the team repo is missing or cannot be reached, is not a Tugboat team repo, is on another GitHub host, or is the app repo itself
- the profile in the team repo is not valid
- you decline a confirmation

Projects under `Pods/`, `Carthage/`, `node_modules/`, and similar folders are not counted. A token inside a remote URL is never shown or saved.

A tool that is older than the profile's minimum tool version still sets up the repo. It proposes no profile change until it is updated.

Not built yet: the workflows of the CI checks. The choice is saved.

## The first agent session: where acceptance criteria are kept

One setup step cannot run in a terminal. Tugboat needs to know where the project's stories keep their acceptance criteria, and only the agent tool is signed in to the tracker. `tug` has no tracker sign-in.

So init leaves this step open, and every sync report says `Setup is not finished` until it is done. In an agent session, ask the agent to run the skill `tug-criteria-source`. Tugboat installs that skill with the other agent tool files. The agent then:

1. asks you for one sample story that has acceptance criteria
2. reads it through the tracker connection of the agent tool: the Atlassian connection for Jira, the GitHub connection for GitHub Projects
3. lists every place where the criteria could be, with a short excerpt of each
4. waits for your pick
5. saves it with `tug profile set-criteria`, which proposes the change as a pull request in the team repo

There are three kinds of place:

| Kind | What it is | The name that is saved |
|---|---|---|
| `field` | A field of the tracker, such as a Jira custom field | The field's name, with the ID of a custom field in brackets: `Acceptance Criteria (customfield_10042)` |
| `section` | A headed section in the story's text | The heading's text: `Acceptance criteria` |
| `task-list` | A task list (check boxes) in the story's text | The heading the list is under, or `body` when it is under none |

The step is done once per project. A second developer is not asked. You can also save the pick by hand:

```sh
tug profile set-criteria --kind section --name "Acceptance criteria"
```

- It follows the same rules as every profile change: a pull request that a person merges, and this machine's own copy until then.
- Offline, the pick is kept on this machine and the command exits with `1`. Run it again to propose it.
- Changing the tracker later clears the saved source, because it named a place in the old tracker.
- A team that wants its own steps puts a skill with the same name in the team repo. It replaces the one that ships with Tugboat.

`tug profile show` prints the profile this machine works from, and says whether it is the approved one or a proposal that waits for its merge.

This step was tested with a stand-in for GitHub. It was not yet run in an agent session with a real Jira or GitHub connection.

## Sync

```sh
tug sync
```

Run it inside an app repo that `tug init` set up. It also runs by itself at the start of each agent session.

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
| `.claude/skills/<name>/` | Each skill from the team repo, and the skill `tug-criteria-source` that ships with Tugboat |
| `.claude/agents/<name>.md` | Each approved agent definition from the team repo |
| `.claude/settings.local.json` | Tugboat adds one hook here, so that each session starts with a sync |

- A second sync with nothing new writes no file.
- Tugboat never overwrites a file it did not write, and never changes a file the repo tracks. It reports the path.
- `.claude/settings.local.json` is yours. Tugboat changes only its own hook entries, which it recognises by their command, and keeps every other key and hook.
- Every generated file is listed in the repo's local exclude file, so `git status` stays clean.
- The hook calls `tug` by its full path, so it does not depend on the agent tool's `PATH`.

At the start of a session the hook runs a sync and hands its report to the agent. A sync that fails is reported in one line and the session goes on. The report also says when the project's setup is not finished, and when a profile change that this machine proposed was merged. Outside a repo that Tugboat was set up in, the hook does nothing.

`tug sync --team-repo ADDRESS` brings only the clone of one team repo up to date, from any folder. `--app-repo FOLDER` names an app repo when the command is run from somewhere else.

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
