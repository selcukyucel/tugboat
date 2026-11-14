# Knowledge

Notes about the code: how a module works, a trap and its fix, a term the team uses.
One note per file. Sub-folders are fine.

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

| Field | Meaning |
|---|---|
| `owner` | The person or group who keeps the note true |
| `repo` | The app repo the note is about, as `owner/name` |
| `paths` | The code the note describes. The agent loads a note only when its task touches these paths. |
| `last_verified` | The day someone last checked the note against the code |

Write what the code does not already say, in plain sentences.

## What the check refuses

`tug team check` fails a note that:

- misses a field, or has a field nobody defined
- holds what looks like a secret: a token, a key, a password. The message names the kind and the line, never the secret.
- looks like a raw chat transcript: several lines that open a turn, such as `User:` and `Assistant:`

A note that fails is not given to any agent. The check warns when a note names a repo that is not in the project profile.

## How the agent finds a note

The agent does not read this whole folder. In an app repo it gets an index: for each note about that repo, its paths, its title, and its file.
Before it works on a file, it runs `tug notes <path>` and reads only the notes that cover the path. A note about another repo is never offered.
