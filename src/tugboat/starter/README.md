# Team repo

This repo is the shared ground for one project that builds with AI coding agents.
It holds the project's rules, permissions, knowledge, and decisions. [Tugboat](https://github.com/selcukyucel/tugboat) syncs it to every developer's machine at the start of each agent session.

Nothing here is product code.

## What lives where

| Path | Holds | Format |
|---|---|---|
| `tugboat.json` | The mark that this is a Tugboat team repo | JSON |
| `profile.json` | The project's choices: app repos, tracker, surfaces, approval mode. Written by `tug init`. | JSON |
| `policies/` | Engineering rules, one file per rule | Markdown with a header |
| `permissions/` | Protected paths, and the approved AI tools and MCP servers | JSON |
| `knowledge/` | Notes about the code | Markdown with a header |
| `decisions/` | Technology and architecture choices, with their reasons | Markdown with a header |
| `estate/` | Apps, modules, owners, and environments | JSON |
| `agents/` | Approved agent definitions | Markdown with a header |
| `skills/` | Recipes for common work | One folder per skill, with a `SKILL.md` |
| `signers` | The team's public signing keys, each beside its GitHub account. Task records are checked against it. | One line per key, in Git's format for SSH signatures |

Each folder has a README that gives its format.

## How it changes

- Every change to `main` goes through a pull request.
- A person approves it. An agent may propose a change. It never approves one.
- An approved change reaches each developer at their next session start.

## Check it

```sh
tug team check .
```

The same check runs on every pull request, from `.github/workflows/team-repo-check.yml`.

## The header of a Markdown file

Rules, notes, decision records, and agent definitions start with a header between two `---` lines:

```
---
title: How login tokens are refreshed
paths: ["Sources/Auth/**", "Tests/AuthTests/**"]
---
```

- Each line is `key: value`.
- A value is plain text, or a list written as `["a", "b"]`.
- Put text in double quotes when it contains `: ` or ` #`, or starts with a symbol.

## What must never be here

Secrets, credentials, raw chat transcripts, and product source code beyond file paths and short excerpts.
