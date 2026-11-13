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
| `status` | `proposed`, `accepted`, or `superseded` |
| `paths` | The code the decision affects. `[]` when it affects no one place. |

All four sections are needed. A record is never deleted. A later record supersedes it.
