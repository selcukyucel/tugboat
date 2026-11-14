---
name: tug-setup-check
description: Checks from inside this session that Tugboat really works in this agent tool. It shows whether the hooks run here, and whether the tracker connection and the GitHub connection answer. Use it when the developer asks to check the setup, after `tug init`, or when no Tugboat sync report appeared at the start of a session.
---

# Check the setup from inside this session

`tug doctor` reads files, so it knows what is configured on disk. It cannot see whether this agent tool really runs Tugboat's hooks. An agent tool can skip them and show no error: Copilot CLI does in a folder it does not trust, and VS Code does in a workspace it does not trust.

This skill finds out, from inside the session. You report what you see. You repair nothing.

## Steps

1. **Name the agent tool you are running in.** Use exactly one of these names:

   | You are | Name |
   |---|---|
   | Claude Code | `claude-code` |
   | GitHub Copilot CLI, in a terminal | `copilot-cli` |
   | GitHub Copilot in VS Code | `copilot-vscode` |

   If you cannot tell, ask the developer.

2. **Try the tracker connection.** Run `tug profile show` and read the line "Tracker". Then read one thing through the connection of this agent tool: for Jira, any one issue through the Atlassian connection; for GitHub Projects, any one issue of this repo through the GitHub connection. Read only. Change nothing.

   | What happened | Result |
   |---|---|
   | The connection returned the item | `ok` |
   | The connection exists and gave an error, or asked for a sign-in | `failed` |
   | This session has no such connection, or you did not try | `none` |

3. **Try the GitHub connection** the same way: read one thing about this repo, such as its open pull requests, through the GitHub connection of this agent tool. Not through `git`, and not through the `gh` command: those are not the connection. The result is `ok`, `failed`, or `none`, as above. When the tracker is GitHub Projects, steps 2 and 3 use the same connection. The results can still differ: a sign-in can be allowed to read issues and not pull requests. Report each as you saw it.

4. **Run the check with your shell tool**, as one command of its own:

   ```sh
   tug doctor observe --surface <name> --tracker <result> --github <result>
   ```

   It must go through your shell tool. That is the point: when this agent tool runs Tugboat's hooks, the hook before that tool call leaves a mark, and the command looks for it. Do not ask the developer to type it.

5. **Run `tug doctor`** and read its two parts: "On disk" and "Observed in a session".

6. **Tell the developer** what both commands printed, starting with anything that failed and the repair each line names. Give the lines as they are. Do not change a setting, trust a folder, or install anything yourself.

## What the results mean

- **"Hooks: NOT RUNNING"** means that in this agent tool nothing syncs at the start of a session and no action is checked, although the files are in place. The line names the usual cause and the repair. The developer makes that change, and you run this skill again in a new session.
- **A connection that is `failed` or `none`** does not stop the work. Without the tracker connection you cannot read a story or its acceptance criteria yourself: the developer pastes them.
- A result is kept for this agent tool only. Each agent tool the team uses needs its own run.
