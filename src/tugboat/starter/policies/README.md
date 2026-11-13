# Rules

One file per rule, in the folder of its scope:

| Scope | Folder | Rule IDs |
|---|---|---|
| Whole project | `company/` | `COM-001`, `COM-002`, ... |
| iOS code | `ios/` | `IOS-001`, ... |
| Android code | `android/` | `AND-001`, ... |

The file is named after its ID, such as `ios/IOS-001.md`. An ID is never reused, also after its rule is removed.

```
---
id: IOS-001
title: New unit tests use Swift Testing
scope: ios
mode: advisory
status: example
---

The rule, in a few plain sentences.

## Why

The reason.

## How to check

What a reviewer or an agent looks at.
```

## Two switches: status and mode

Each rule has two fields that decide what it does. They are separate.

| Field | Value | What it means |
|---|---|---|
| `status` | `example` | The rule shows the format. It is not applied, and a break of it is not reported. |
| `status` | `adopted` | The team uses the rule. The agent gets it at the start of each session. |
| `mode` | `advisory` | The agent follows the rule. When the work breaks it, the agent says so in its report. The work is not blocked. |
| `mode` | `required` | A break is a blocker. The agent stops and tells the developer. |

**Every rule here starts as an advisory example.** That is on purpose:

- The rules that came with this repo are examples. They are not your team's rules until your team adopts them. Change them, remove them, and add your own.
- A rule that the team adopts starts as `advisory`. The team first sees how often it is broken, and whether it is right, while nobody is blocked by it.
- A rule becomes `required` when the team decides it has earned that.

Each step is a pull request in this repo that a person approves:

1. Adopt a rule: set `status: adopted`.
2. Make it a blocker: set `mode: required`.

The agent cannot do either. It reads an approved copy of this repo and cannot change it.

## Which rules an agent gets

An agent that works in an iOS app repo gets the adopted `company/` rules and the adopted `ios/` rules. It does not get the `android/` rules. An Android app repo gets `company/` and `android/`.

When two rules conflict, or the work cannot follow a rule, the agent shows that to the developer. It does not choose, and it cannot grant an exception.

## No backend rules

There are no backend rules here. Backend code keeps its own governance. `tug team check` reports a folder under `policies/` that is not `company`, `ios`, or `android`.
