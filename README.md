# Tugboat

Tugboat is a governance layer for teams that build software with AI coding agents.
It gives every developer's agent the same rules and team knowledge from one shared team repo, and keeps task history that any machine can resume.
A tugboat guides a large ship into harbour and does not steer it. Tugboat guides the agent and leaves the decisions to the developer.

It runs beside Claude Code, GitHub Copilot CLI, and GitHub Copilot in VS Code. It adds no hosted service.

## Status

Early. This version can start and check a team repo, set up an app repo with `tug init`, save the project's choices in the team repo, keep a read-only copy of the team content inside the app repo, and generate the files the agent tools load. It syncs at the start of each agent session. It checks each write and each shell command before the agent makes it.
A command that is not written yet is listed and answers "not built yet". Nothing here is ready for daily use.

## Requirements

- macOS
- Git
- [uv](https://docs.astral.sh/uv/). uv fetches a suitable Python when the machine has none. Tugboat needs Python 3.11 or newer.

Tugboat uses only the Python standard library. It has no third-party package at run time.

## Install

```sh
uv tool install git+https://github.com/selcukyucel/tugboat@v0.24.1
tug version
```

Always install from this full address. Tugboat is not published to the Python package index. A package there with the same name is not this tool.

To remove the command: `uv tool uninstall tugboat`.

## Update

```sh
tug update           # install the newest release
tug update --check   # only say whether a newer release exists
```

Tugboat never updates itself. It updates when you run `tug update`.

- It reads the release tags of this repo, takes the newest one, and runs the same uv install as above at that tag.
- It installs only a tagged release from this repo. The address is fixed inside the tool. No file, option, or environment variable can point it elsewhere.
- A tag counts as a release only when it is exactly `v` and three numbers, such as `v1.2.3`.
- It refuses to touch a `tug` that uv did not install as a tool, such as one run from a source checkout. It prints the uv command instead.
- After an update, the next sync in each repo regenerates the agent tool files.

**You are told when you are behind.** Two lines can appear in the sync report, and so at the start of each agent session:

- `This project needs tug 1.4.0 or newer ...` is the first line when your tool is older than the project's minimum tool version. The minimum is in the approved project profile and changes only through a pull request in the team repo. An older tool still syncs. It opens no pull request in the team repo until it is updated.
- `A newer Tugboat is released: ...` is the last line when a newer release exists. Sync asks this repo for its release tags at most once a day, and only when the team repo was just reached. A lookup that fails shows nothing, changes nothing, and is tried again an hour later.

One machine can serve several projects. Each project's minimum is checked in that project's repos, and the newest release satisfies all of them.

## Commands

| Command | Does | State |
|---|---|---|
| `tug version` | Prints the installed version | Works |
| `tug team new <folder>` | Writes the starter layout of a team repo into an empty folder | Works |
| `tug team check [folder]` | Checks every file Tugboat reads in a team repo | Works |
| `tug team propose` | Proposes a change to the team repo as a pull request | Works |
| `tug init` | Sets up the app repo it is run in | Works. Of the optional CI checks, the secret scan is built. |
| `tug profile show` | Prints the project profile this machine works from | Works |
| `tug profile set-criteria` | Saves where the project's stories keep their acceptance criteria. Run by the agent in the first session. | Works. Not yet tried with a real tracker. |
| `tug sync` | Brings the team repo clone, an app repo's copy of it, and the agent tool files up to date | Works |
| `tug owner <path>` | Names the owner of a file or folder in an app repo, from the estate map | Works |
| `tug notes [path...]` | Names the knowledge notes that cover a file or folder in an app repo | Works |
| `tug capture status`, `accept`, `drop`, `propose` | Keeps the notes an agent drafted, records the developer's decision on each, and proposes the accepted ones in one pull request | Works. Not yet run in an agent session. |
| `tug decision status`, `propose` | Lists the decision records an agent drafted, and proposes them as a pull request | Works. Not yet run in an agent session. |
| `tug protected [path...]` | Says whether a file or folder in an app repo is a protected path, and why | Works |
| `tug status` | Prints the session brief | Not built yet |
| `tug doctor` | Checks that this machine and repo are ready | Not built yet |
| `tug update` | Updates `tug` to the newest release | Works |
| `tug expire` | Removes records older than six months, after confirmation | Not built yet |
| `tug remove` | Takes Tugboat out of a repo, or out of every repo on the machine | Not built yet |
| `tug hook session-start` | Called by the agent tool at the start of a session. Syncs and reports to the agent. | Works |
| `tug hook pre-tool-use` | Called by the agent tool before each tool call. Allows, asks, or denies. | Works |

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

## Rules

Engineering rules live in the team repo under `policies/`, one file per rule, in the folder of their scope: `company`, `ios`, or `android`. There are no backend rules.

**Each rule has two switches.**

| Field | Value | What it means |
|---|---|---|
| `status` | `example` | The rule shows the format. It is not applied, and a break of it is not reported. |
| `status` | `adopted` | The team uses the rule. The agent gets it at the start of each session. |
| `mode` | `advisory` | The agent follows the rule and reports a break. The work is not blocked. |
| `mode` | `required` | A break is a blocker. The agent stops and tells the developer. |

**The starter layout has twelve rules, four for each scope. All of them are advisory examples.** So a new team repo applies no rule at all until the team adopts one. That is on purpose:

- The starter rules are illustrations, such as "new unit tests use Swift Testing". They are not your team's rules until your team says so.
- An adopted rule starts as advisory. The team sees how often it is broken, and whether it is right, while nobody is blocked by it.
- A rule becomes required when the team decides it has earned that.

Adopting a rule and making it required are each a pull request in the team repo that a person approves. The agent can do neither: it reads a copy of the approved revision and cannot change it.

**What the agent gets.** Sync writes the rules that apply into the instruction file, `.claude/rules/tugboat.md`, which every agent tool loads at the start of a session:

- an iOS app repo gets the adopted company rules and the adopted iOS rules. It does not get the Android rules. An Android app repo gets company and Android.
- each rule is listed with its ID, its mode, what it says, and the path of its file
- the team repo revision the rules come from is named, and the agent is told to name it in every report that mentions a rule
- the platform comes from the project profile. A repo that is not in the profile gets the company rules only, and the file says why.
- a rule file that cannot be read is not applied. The sync report and the instruction file both name it.
- when two rules conflict, or the work cannot follow a rule, the agent is told to show that to the developer. It does not choose, and it cannot grant an exception.

Today a required rule blocks through the agent's instructions only. The hooks that enforce a block are not built yet.

## AI use policy

One page in the team repo, `policies/ai-use.md`, says how the team uses AI tools. It has three sections, and `tug team check` refuses a page that lacks one:

| Section | Says |
|---|---|
| `## Data that must never enter a prompt` | What is kept out of prompts, chats, and tool calls: secrets, personal data of real users, production data |
| `## Accounts` | That only company accounts are used, and only the approved tools |
| `## Accountability` | That the developer who opens a pull request is accountable for agent-written code in it |

The page has a `status`, like a rule:

- `example`: the page shows the format. It is not put in front of the agent. The starter layout ships it this way, because a policy is the team's own statement.
- `adopted`: the page is in force. Sync writes its full text into the instruction file, `.claude/rules/tugboat.md`, which every agent tool loads at the start of every session, in every app repo of the project.

Adopting it is a pull request in the team repo that sets `status: adopted`. Until then, each sync report says `Setup is not finished: the team has not adopted its AI use policy yet`.

- The page is at most 8,000 characters: one page.
- A page that cannot be read is not loaded. The sync report and the instruction file name it.
- The agent is told to say so when a request would break the policy, and that it cannot make an exception itself.
- A change to the page appears in the sync report as `AI use policy changed`.

## AI tool register

`permissions/ai-tools.json` in the team repo is the team's list of approved AI tools. It has three groups: `agent_tools`, `models`, and `mcp_servers`.

- **One rule for all three.** A group with entries is the whole list, and anything else in that group is not approved. An empty group means the team has not made that list yet, so nothing in it is ruled out.
- **It changes only through a pull request** in the team repo that a person approves.
- **The agent gets the list** at the start of each session, in the instruction file, and is told not to add, set up, or suggest anything that is not on it.

An MCP server entry says how the server is recognised on a machine:

| Field | Meaning |
|---|---|
| `hosts` | For a remote server: the host names of its address, such as `api.githubcopilot.com`. Only the exact host counts. |
| `commands` | For a server that runs on the machine: the program, or the package it runs, such as `figma-developer-mcp`. A version after the name is ignored. |

The name a developer gives a server in their own settings is not used. Anyone can call any server "github".

The setup check that compares the servers configured on a machine with this list is not built yet. The register and the way a server is matched are ready for it.

## Estate map

The estate map is one file in the team repo, `estate/estate.json`. It is the project's inventory: which apps there are, which repo each lives in, which modules each app has, who owns each, and which environments an app runs in. It answers one question: who owns this file?

```sh
tug owner Sources/Checkout/Payments/Card.swift
# Sources/Checkout/Payments/Card.swift: @sam (module payments of the app shop-ios, which covers Sources/Checkout/Payments/**)
```

- A module names the paths it covers. When several modules cover a path, the narrowest path wins: `Sources/Checkout/Payments/**` beats `Sources/Checkout/**`, which beats `**/*.swift`.
- A path that no module covers belongs to the owner of the app.
- A path may be given from the folder you are in, or as a full path. The file need not exist yet. A path outside the repo is refused.
- A repo that is not in the map gets no answer, and the command exits with `1`. Tugboat does not guess an owner.
- The agent gets the apps and modules of its own repo in the instruction file, and is told to run `tug owner` and not to guess.
- `tug team check` refuses a map that is not valid. It warns when an app names a repo that is not in the project profile, or a platform that differs from the profile.
- The backend is not in the map.

**Path patterns.** The same small pattern language is used wherever the team repo names paths:

| Sign | Matches |
|---|---|
| `*` | Any run of characters inside one folder or file name |
| `?` | One character inside one name |
| `**` | Any number of folders, also none. It must stand alone between slashes. |

Everything else matches itself, and letter case counts. A pattern covers what it matches and everything beneath it, so `Sources/Auth` and `Sources/Auth/**` cover the same files.

## Knowledge base

A knowledge note is one Markdown file under `knowledge/` in the team repo. It says what the code does not say for itself: how a module works, a trap and its fix, a term the team uses.

```
---
title: How login tokens are refreshed
owner: a-github-handle
repo: your-org/your-app-ios
paths: ["Sources/Auth/**", "Tests/AuthTests/**"]
last_verified: 2026-11-13
---

The note, in plain sentences.
```

Every note names an owner, the app repo and the code paths it covers, and the day someone last checked it against the code.

**The agent reads only the notes for its task.** It does not load the whole base.

```sh
tug notes Sources/Auth/Login/LoginView.swift
# - How login tokens are refreshed: .tugboat/team/knowledge/auth/token-refresh.md (covers Sources/Auth/Login/LoginView.swift; owner @pat; last verified 2026-11-13)
```

- Sync writes an index into the instruction file: for each note about this repo, its paths, its title, and its file. The text of a note is not in the index. The index names 30 notes and counts the rest.
- The agent is told to run `tug notes <path>...` with the files or folders of its task, and to read the notes it names and no others.
- A file gets the notes whose paths cover it. A folder also gets the notes about anything inside it.
- Several paths give one list, each note once. With no path, every note about this repo is listed.
- A note about another repo is never offered. When no note covers a path, the command says so and exits with `0`.
- When a note and the code disagree, the agent is told that the code is right, and to tell the developer that the note is out of date.

**No secrets and no chat transcripts.** `tug team check` fails a note that:

- holds what looks like a secret. The patterns cover private keys, AWS, GitHub, Slack, Google, Stripe, and AI provider keys, signed tokens (JWT), a password inside a URL, and a name such as `password` or `api_key` followed by a value that mixes letters with two digits or more. A sentence about passwords is not a finding, and neither is a call or a property in a line of code.
- looks like a raw chat transcript: four or more lines that open a turn, such as `User:` and `Assistant:`, from two speakers or more.

The message names the kind of secret and the line. It never repeats the secret. A note that fails is also left out on a developer's machine, so no agent gets it, and sync says which note was left out.

The check is a pattern check. It can miss a secret with an unusual shape, and it does not replace a review of the pull request. The same patterns will be used for the hook that checks a write, and for task records.

`tug team check` also warns when a note names a repo that is not in the project profile, because no agent would load it.

## Knowledge capture

During a task an agent finds things out that the code does not say. Capture turns those into knowledge notes, with the developer deciding on each one.

It starts when the developer asks, for example "capture what you learned". The agent then follows the skill `tug-capture-notes`, which ships with the tool. The agent is told not to stop the work to write a note.

1. The agent writes each proposed note as a draft file in the app repo, under `.tugboat/capture/knowledge/`. Git does not see that folder.
2. `tug capture status` lists the drafts and where each stands. A draft that is not a valid note is named with the reason: a missing field, another repo, a date after today, a secret pattern, a chat transcript.
3. The agent shows each draft to the developer, who accepts, edits, or drops it.
4. `tug capture propose` opens one pull request in the team repo with every accepted draft. A person merges it. The proposed drafts are removed from the app repo.

```sh
tug capture status
# Drafts in .tugboat/capture/knowledge/:
# - auth/token-refresh.md: new. Not accepted and not dropped yet. "How login tokens are refreshed" (covers Sources/Auth/**)
tug capture accept auth/token-refresh.md
tug capture drop checkout/tax.md
tug capture propose --task SHOP-123
```

- **Accepted means accepted as it is.** A draft that is edited after it was accepted must be accepted again. It does not go into a pull request before that.
- **A dropped note does not come back.** Dropping removes the draft and remembers what it said: its title, its paths, and its text. A new draft with the same content is marked "dropped before" and is not offered again. Another file name, owner, date, or line breaks do not make it new. Other content does.
- **The dropped list is shown to the agent**, so it does not write the same note again in other words. The tool cannot check a rewording. The agent is told not to do it.
- **One pull request.** All accepted drafts go into one pull request. `--task` puts the task's name into it.
- **A draft with the path of a note that is in the team repo** is proposed as a change to that note. A draft with the same content as the team repo's note is not proposed.
- Without `--yes`, `tug capture propose` asks a person in a terminal before it opens the pull request.

The decisions are kept in `.tugboat/capture.json` in the app repo, on that machine. Another machine does not know what was dropped here.

## Decision records

A decision record keeps one technology or architecture choice with its reason. It is one Markdown file under `decisions/` in the team repo, named after its ID: `DEC-0003-use-swift-testing.md`.

```
---
id: DEC-0003
title: Use Swift Testing for new unit tests
date: 2026-11-14
status: accepted
repo: your-org/your-app-ios
paths: ["Tests/**"]
---

## Context
## Options
## Choice
## Reason
```

- All four sections are needed. `paths` names the code the choice affects, and is `[]` when it affects no one place.
- `repo` is optional. With it, the record is about one app repo. Without it, the record holds for the whole project.
- `status` is `proposed`, `accepted`, or `superseded`. Only an accepted record is in force. A record is never deleted. A later record supersedes it.

**The agent follows the records in force.** Sync writes a list into the instruction file: the accepted records that hold in this repo, each with its ID, title, affected paths, and file. A record about another repo is not listed. The agent is told to read a record before it works on the code the record affects, to follow the choice, and to stop and tell the developer when a task needs another choice.

**The agent drafts a record when a task makes such a choice**, with the skill `tug-decision-record`, which ships with the tool. The developer makes the choice. The agent writes it down.

1. The agent writes the draft in the app repo, under `.tugboat/capture/decisions/`. Git does not see that folder.
2. `tug decision status` lists the drafts, names each one that is not valid, and gives the next free ID.
3. The developer reads the draft and has it changed until it says what they decided.
4. `tug decision propose <file name>` opens a pull request in the team repo and removes the draft. Merging the pull request is the team's agreement with the choice.

```sh
tug decision status
# Draft decision records in .tugboat/capture/decisions/:
# - DEC-0003-use-swift-testing.md: ready to propose. "Use Swift Testing for new unit tests" (status accepted; affects Tests/**)
# The next free ID is DEC-0004.
tug decision propose DEC-0003-use-swift-testing.md
```

- A record reaches the team repo only this way. It is not written into the app repo's tracked files, and the copy in `.tugboat/team/` gets it only after a person merges the pull request.
- A draft is refused when a section is missing, when its ID is used by another record in the team repo, when its date is after today, when it names another repo, or when it holds what looks like a secret.
- A draft with the file name of a record that is in the team repo is proposed as a change to that record. This is how an older record is set to `superseded`.
- Without `--yes`, the command asks a person in a terminal before it opens the pull request.

Two pull requests that are open at the same time can both use the same new ID. The check on the team repo's `main` fails after the second merge, and one record is then renumbered by a pull request.

## Skills

A skill is a recipe that guides the agent through common work, such as a new screen. It is one folder in the team repo, `skills/<name>/`, with a `SKILL.md` inside. Sync installs the team's skills under `.claude/skills/` in each app repo.

**A skill names the rule IDs it applies**, anywhere in its `SKILL.md`: `IOS-005`, `AND-005`, `COM-001`.

**A skill is installed where its rules apply.**

| The skill names | It is installed in |
|---|---|
| iOS rules, and no Android rule | iOS repos only |
| Android rules, and no iOS rule | Android repos only |
| Rules of both platforms, only company rules, or no rule | Every app repo |

When a skill changes sides, the next sync removes it from the repos it no longer belongs to. The skills that ship with the tool, whose names start with `tug-`, go to every app repo.

**`tug team check` checks the rule IDs.**

- A skill that names a rule that is not in `policies/` fails the check. This catches a typo.
- It warns when a skill names no rule ID, when it names a rule that is only an example, and when it is for a platform the project has no repo for.

## Agent registry

The team repo holds the agent definitions the project has approved, one file each under `agents/`. A definition states the agent's purpose, the tools it may use, and the rule IDs it applies.

```
---
name: reviewer
description: Reviews a finished change and reports findings. It edits no file.
tools: ["Read", "Grep", "Glob"]
rules: []
---

The instructions the agent follows.
```

- **Installed by init and sync.** Each definition is written to `.claude/agents/<name>.md` in the app repo, which Claude Code, Copilot CLI, and Copilot in VS Code all read. Git does not see the file. The installed file has the three header fields every agent tool knows: `name`, `description`, and `tools`.
- **Listed for the agent.** The instruction file gets a section "Approved agents", with the name, purpose, and tools of each.
- **Changed only by pull request.** A definition lives in the team repo. The installed copy is read-only and is rewritten at the next sync after a merge. A definition that is removed from the team repo is removed from the app repo.
- **Installed where its rules apply**, as a skill is. A definition that names iOS rules and no Android rule goes to iOS repos only. With `rules: []`, or rules of both platforms, it goes to every app repo.
- **Never over a file of yours.** If the app repo already has a file at that path that Tugboat did not write, it is left alone and the sync report says so.
- `tug team check` fails a definition that names a rule that is not in `policies/`. It warns when one names a rule that is only an example, or is for a platform the project has no repo for.

**The reviewer.** The starter layout ships one definition, `reviewer`. It reviews a finished change against the adopted rules, the decision records, and the knowledge notes, and reports findings.

- It may use `Read`, `Grep`, and `Glob`. It has no edit tool, no write tool, and no shell, so it can change no file.
- It cannot fetch a diff itself. The agent that calls it hands it the list of changed files and the diff.
- A review without that is reported as incomplete, never as clean.
- Its `rules` list is empty on purpose: it applies every adopted rule of the repo it runs in.

Whether an agent tool enforces the `tools` list is up to that tool. The automatic run of the reviewer after each change is not built yet. Today a developer or the main agent calls it.

## Protected paths

Some files an agent must not change without asking: signing files, CI workflows, release automation. The team repo holds one list of them, `permissions/protected-paths.json`. Each entry has a path pattern, a platform (`ios`, `android`, or `any`), and a reason. A change to the list is a pull request in the team repo.

**Sync compiles the list for each app repo.** Nothing is committed in the app repo, so a new protected path needs no app repo pull request. It is in force after the next sync.

| What sync writes | For | What it does |
|---|---|---|
| `.tugboat/permissions.json` | Every agent tool | The paths protected in this repo, with the reason for each: the team repo's entries for the repo's platform, and Tugboat's own files |
| "ask" rules in `.claude/settings.local.json` | Claude Code | Claude Code asks the developer before it edits a protected file |
| A section in the instruction file | Every agent tool | The agent is told to ask before it changes a protected file, and not to get around it with a shell command |

```sh
tug protected Signing/dist.p12 Sources/App.swift
# Signing/dist.p12: protected by **/*.p12 (from the team repo). Signing certificates must not be changed by an agent. Ask the developer before changing it.
# Sources/App.swift: not protected.
```

- **Tugboat's own files are always protected**: the copy of the team repo, its records under `.tugboat/`, the files it generated under `.claude/`, and `.claude/settings.local.json`. The draft folder `.tugboat/capture/` is not, because the capture skills write there.
- **The rules in the settings file are Tugboat's own.** It records which rules it added and takes only those out again. A rule or an approval the developer wrote stays where it is.
- **A rule names the full path on the machine.** Claude Code reads a short rule from the folder the session started in, which need not be the top of the repo.
- **A broken list takes no protection away.** When the team repo's file cannot be read, the entries of the last good compile stay in force and the sync report says so.
- **A repo whose platform is not known yet** gets the entries of both platforms.

**What is enforced where, today:**

| Agent tool | Protection |
|---|---|
| Claude Code | The "ask" rule, and the check before each action. Claude Code's documentation says such a rule holds in every permission mode. In a run with nobody to answer, the edit is refused. |
| Copilot CLI and Copilot in VS Code | The check before each action, which denies the change. Copilot takes no allow or deny rule for tools from a file in the repo. |

See "The check before each action" below.

## The check before each action

Before an agent tool runs a tool call, it calls `tug hook pre-tool-use` and waits for the answer: allow, ask the developer, or deny. Sync installs this hook once, in `.claude/settings.local.json`, where Claude Code, Copilot CLI, and Copilot in VS Code all read it.

Only tools that write files or run shell commands are checked. A read passes at once. One check takes about 50 milliseconds.

| What the agent is about to do | Claude Code | Copilot |
|---|---|---|
| Write text that holds a secret pattern, or run a shell command that holds one | Denied | Denied |
| Change a protected path with an edit tool | Asks the developer | Denied |
| Change a protected path with a shell command that the check recognises | Asks the developer | Denied |
| Force-push to a shared branch, delete one, or move one by force | Denied | Denied |
| Run `git reset --hard` while on a shared branch | Denied | Denied |
| Anything else | Allowed | Allowed |

- **A secret** is found with the same patterns the team repo check uses for notes. Only the text that is about to be written is read, not the file as it is. The answer names the kind of secret and the line, never the secret.
- **A protected path** comes from the compiled list, `.tugboat/permissions.json`. So a path added in the team repo is checked after the next sync.
- **A shared branch** is the repo's default branch from the project profile, `main`, `master`, `develop`, and any `release/...` branch. A force push to a developer's own branch is allowed.
- **Shell commands that are recognised as writes**: a redirect (`>`, `>>`), `tee`, `rm`, `rmdir`, `mv`, `cp` and `ln` (the destination), `touch`, `mkdir`, `chmod`, `chown`, `truncate`, `dd of=`, `sed -i`, `perl -i`, and `git rm`, `git mv`, `git clean`, `git restore`, `git checkout`, `git apply` with paths. Reading a protected file, or running a tool such as `fastlane`, is not a change.
- **Every answer says why**, and what the developer can do.

**When the check itself fails, the action is denied.** A check that failed has not passed.

| What goes wrong | Claude Code | Copilot CLI |
|---|---|---|
| `tug` meets an error, such as a broken compiled list | Denied, with the reason | Denied, with the reason |
| The `tug` command is missing | Denied. The hook entry says `"onFailure": "block"`, which Claude Code needs for this. | Denied |
| The check runs past its time limit of 10 seconds | Denied, for the same reason | **Allowed.** Copilot lets the action through after a timeout, and no setting changes that. |

With a broken compiled list every write and every shell command is denied, apart from `tug sync`, which rebuilds the list.

The limits of these checks are in the next section.

## Limits of the checks

Before an agent runs a tool call, Tugboat checks it: a secret in a write, a change to a protected path, a force push to a shared branch.
These checks are a guard against mistakes. They are not a security boundary.
This page lists what they do not cover, so that nobody relies on them for more than they give.

Each line says how it is known: **seen** in a real session, **documented** by the maker of the agent tool, or **by design** of Tugboat.

### When no check runs at all

- Copilot CLI loads no hook from a folder it does not trust. It prints one warning line and goes on. Tugboat reports nothing. (Seen.)
- VS Code runs no hook in a workspace it does not trust. Nothing reports it. (Documented. Seen once as a session where nothing happened.)
- Hooks in VS Code are a preview feature there. They can change or go away. (Documented.)
- In a Git worktree of an app repo nothing is checked, because Tugboat's folder is not in a worktree. (By design.)
- In a repo where `tug init` has not run, nothing is checked. (By design.)
- An agent tool other than Claude Code, Copilot CLI, and Copilot in VS Code does not call the checks. (By design.)

### When a check runs and the action goes through anyway

- A Copilot hook that passes its time limit lets the action through, and no setting changes that. Tugboat's limit is 10 seconds, and a check takes about 50 milliseconds. (Seen.)
- Claude Code older than 2.1.295 lets the action through when `tug` is missing or crashes, because it does not know the setting that blocks on failure. (Documented.)
- A shell command is checked only for the writes that can be recognised: redirects, and a fixed list of commands such as `rm`, `mv`, `cp`, `tee`, and `sed -i`. A script, a build tool, or a program that writes a file by another route is not seen. (By design.)
- A secret is found by its shape. A secret with an unusual shape is not found. (By design.)
- Only the `git` command is checked for a force push or a hard reset. The same action through another program is not seen. (By design.)
- A force push to a branch that is not shared is allowed. A developer's own branch is theirs to rewrite. (By design.)
- A tool is checked when its name says that it writes or runs commands. A tool with another name passes. (By design.)

### What can be switched off

- The hook is an entry in `.claude/settings.local.json`, a file on the developer's machine. Whoever can edit that file can remove the entry. Tugboat keeps the agent's edit tools away from the file, and the next sync puts the entry back. (By design.)
- Claude Code reads a changed settings file at once, in the middle of a session. So a removed entry is gone at once. (Seen.)
- The developer can always do by hand what the agent was denied. That is intended: the checks guard the agent, not the developer.

### When a check stops something it should not

- A made-up token in a test can match a secret pattern. The write is denied, and the developer makes that change. (By design.)
- On Copilot a change to a protected path is denied, not asked about. The developer makes that change. (By design.)
- In a Claude Code run with nobody at the keyboard, a protected path holds the run for about two minutes and then refuses. (Seen.)

### What does give a hard boundary

The checks run on the developer's machine, where they can be removed. A boundary has to be somewhere the developer's machine cannot change:

- **Branch protection in GitHub** for the shared branches: no force push, no deletion, changes only by pull request.
- **A secret scan in CI or at push time**, such as GitHub's push protection, or Tugboat's optional CI secret scan. Without one, secret blocking rests on the local check alone.
- **Review of every pull request**, with required reviewers for the protected paths.

Use these. The local checks make mistakes rarer. They do not make them impossible.

The same page is in every team repo, as `permissions/hook-limits.md`.

## CI secret scan (optional)

The checks on a developer's machine can be removed there. A scan in CI cannot. A project can turn on one CI check: every pull request in an app repo is scanned for keys and tokens, and a finding fails the check.

```sh
tug init --ci secret-scan     # turn it on for the project, from any app repo
tug init --ci none            # turn it off again
```

It has two parts.

| Part | Where | What it is |
|---|---|---|
| The scan | The team repo, `.github/workflows/secret-scan.yml` | A reusable workflow. It runs [gitleaks](https://github.com/gitleaks/gitleaks), an open-source scanner, on the commits of the pull request. It ships with the starter layout. |
| The caller | Each app repo, `.github/workflows/tugboat-secret-scan.yml` | A few lines that call the scan for every pull request. `tug init` writes it. |

- **The scanner is pinned.** The workflow names one gitleaks version and the SHA-256 of its archive, and checks the download against it before it unpacks it.
- **A finding is kept out of the log.** The log names the rule, the file, and the commit.
- **A secret that a later commit takes out again is still found**, because the scan reads every commit of the pull request, not only its last state.
- **The caller is the one file Tugboat writes that Git is meant to see**, because a workflow runs only when it is committed. Tugboat writes it and never commits it. Commit it through the app repo's own review, with whoever owns its CI.
- **Turning the scan off removes the caller**, and only while it is exactly what Tugboat wrote. A file somebody changed is left alone, and init says so.
- **Other app repos of the project** get the file at their next `tug init`. Until then their sync report says that the file is missing. Sync itself changes no file Git sees.
- **With no CI check selected, Tugboat adds no workflow file.**

Two things a person must do in GitHub. Tugboat cannot.

1. **In a team repo made before this release**, add `.github/workflows/secret-scan.yml`. `tug team propose` does not touch workflow files. Copy it from the starter layout.
2. **In a private team repo**, allow the app repos to call its workflows: Settings, Actions, General, Access. GitHub's documentation notes that this lets people with access to the app repos see the team repo's workflows.

A made-up secret in a test can fail the scan. gitleaks reads a `.gitleaks.toml` in the app repo, where a path or a value can be allowed.

The other two optional CI checks, the guardrail pack and dependency verification, are saved as a choice and have no workflow yet.

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

When the copy moves to a new revision, sync lists what changed since this repo's last sync, with the file to read for each:

```
Changed in the team repo since this repo's last sync (bb959ef to 9e2ae27):
- Rule added: IOS-002 "New unit tests use Swift Testing" (.tugboat/team/policies/ios/IOS-002.md)
- Note removed: auth/tokens.md
- Profile changed (.tugboat/team/profile.json)
```

- It lists rules, permissions, notes, skills, decision records, agents, the estate map, and the profile. A change to anything else in the team repo, such as a CI workflow, is not listed.
- A change inside a skill's folder counts as a change of that skill.
- The list holds at most 20 lines. The rest are counted.
- The first copy in a repo lists nothing, and so does a refresh that only a new tool version caused.
- The list is shown once, by the sync that applies the change. The session-start hook hands it to the agent, so the agent need not search for what is new.

**It generates the files the agent tools load**, once, under `.claude/`. Claude Code, GitHub Copilot CLI, and GitHub Copilot in VS Code all read them, so nothing is written twice.

| File | What it is |
|---|---|
| `.claude/rules/tugboat.md` | The instruction file. It tells the agent where the team content is and how to use it, and lists the rules that apply in this repo. |
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
