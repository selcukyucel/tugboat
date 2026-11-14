# What the checks cannot do

Before an agent runs a tool call, Tugboat checks it: a secret in a write, a change to a protected path, a force push to a shared branch.
These checks are a guard against mistakes. They are not a security boundary.
This page lists what they do not cover, so that nobody relies on them for more than they give.

Each line says how it is known: **seen** in a real session, **documented** by the maker of the agent tool, or **by design** of Tugboat.

## When no check runs at all

- Copilot CLI loads no hook from a folder it does not trust. It prints one warning line and goes on. No hook runs, so nothing is reported in the session. `tug doctor` reports the folder. (Seen.)
- VS Code runs no hook in a workspace it does not trust. Nothing reports it in the session. The skill `tug-setup-check` shows it. (Documented. Seen once as a session where nothing happened.)
- Hooks in VS Code are a preview feature there. They can change or go away. (Documented.)
- In a Git worktree of an app repo nothing is checked, because Tugboat's folder is not in a worktree. (By design.)
- In a repo where `tug init` has not run, nothing is checked. (By design.)
- An agent tool other than Claude Code, Copilot CLI, and Copilot in VS Code does not call the checks. (By design.)

## When a check runs and the action goes through anyway

- A Copilot hook that passes its time limit lets the action through, and no setting changes that. Tugboat's limit is 10 seconds, and a check takes about 50 milliseconds. (Seen.)
- Claude Code older than 2.1.295 lets the action through when `tug` is missing or crashes, because it does not know the setting that blocks on failure. (Documented.)
- A shell command is checked only for the writes that can be recognised: redirects, and a fixed list of commands such as `rm`, `mv`, `cp`, `tee`, and `sed -i`. A script, a build tool, or a program that writes a file by another route is not seen. (By design.)
- A secret is found by its shape. A secret with an unusual shape is not found. (By design.)
- Only the `git` command is checked for a force push or a hard reset. The same action through another program is not seen. (By design.)
- A force push to a branch that is not shared is allowed. A developer's own branch is theirs to rewrite. (By design.)
- A tool is checked when its name says that it writes or runs commands. A tool with another name passes. (By design.)

## What can be switched off

- The hook is an entry in `.claude/settings.local.json`, a file on the developer's machine. Whoever can edit that file can remove the entry. Tugboat keeps the agent's edit tools away from the file, and the next sync puts the entry back. (By design.)
- Claude Code reads a changed settings file at once, in the middle of a session. So a removed entry is gone at once. (Seen.)
- The developer can always do by hand what the agent was denied. That is intended: the checks guard the agent, not the developer.

## When a check stops something it should not

- A made-up token in a test can match a secret pattern. The write is denied, and the developer makes that change. (By design.)
- On Copilot a change to a protected path is denied, not asked about. The developer makes that change. (By design.)
- In a Claude Code run with nobody at the keyboard, a protected path holds the run for about two minutes and then refuses. (Seen.)

## What does give a hard boundary

The checks run on the developer's machine, where they can be removed. A boundary has to be somewhere the developer's machine cannot change:

- **Branch protection in GitHub** for the shared branches: no force push, no deletion, changes only by pull request.
- **A secret scan in CI or at push time**, such as GitHub's push protection, or Tugboat's optional CI secret scan. Without one, secret blocking rests on the local check alone.
- **Review of every pull request**, with required reviewers for the protected paths.

Use these. The local checks make mistakes rarer. They do not make them impossible.
