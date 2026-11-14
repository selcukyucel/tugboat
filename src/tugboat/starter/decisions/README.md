# Decision records

One file per technology or architecture choice, named after its ID, such as `DEC-0001-use-swift-testing.md`.

```
---
id: DEC-0001
title: Use Swift Testing for new unit tests
date: 2026-11-13
status: accepted
paths: ["Tests/**"]
---

## Context

What made a choice necessary.

## Options

What was considered.

## Choice

What was chosen.

## Reason

Why.
```

| Field | Values |
|---|---|
| `status` | `proposed`, `accepted`, or `superseded`. Only an accepted record is in force. |
| `paths` | The code the decision affects. `[]` when it affects no one place. |
| `repo` | Optional: `repo: your-org/your-app-ios`. The one app repo the record and its paths are about. Without it, the record holds for the whole project. |

All four sections are needed. A record is never deleted. A later record supersedes it.
A record that holds what looks like a secret fails the check.

## How a record gets here

When a task makes a technology or architecture choice, the agent drafts a record in the app repo with the skill `tug-decision-record`.
The developer reviews the draft, and `tug decision propose` opens a pull request here. Merging it is the team's agreement with the choice.

## How the agent uses a record

In an app repo the agent gets a list of the accepted records that hold there: the ID, the title, the affected paths, and the file.
It reads a record before it works on the code the record affects, and it follows the choice.
