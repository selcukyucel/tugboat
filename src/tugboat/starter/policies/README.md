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

| Field | Values |
|---|---|
| `mode` | `advisory`: the agent reports a break. `required`: a break is a blocker. Every rule starts as `advisory`. |
| `status` | `example`: shows the format and is not applied. `adopted`: the team uses it. |

There are no backend rules here. Backend code keeps its own governance.
