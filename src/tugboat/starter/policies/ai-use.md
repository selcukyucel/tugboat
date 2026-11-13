---
title: How we use AI tools
status: example
---

This page says how the team uses AI coding tools. The agent reads it at the start of every session. People read it once, and again when it changes.

## Data that must never enter a prompt

None of these goes into a prompt, a chat, a file the agent is asked to read, or a tool call:

- Passwords, API keys, tokens, private keys, and signing certificates.
- Personal data of real users: names, email addresses, phone numbers, home addresses, payment details, health data, and location history.
- Production data: database exports, logs and crash reports that hold user data, and support tickets with a user's details.
- Material under a contract that forbids sharing it, such as a partner's unreleased plans or licensed data.

Use made-up data instead. An agent that meets such data in a file or in a tool result does not copy it, repeat it, or send it on. It tells the developer where the data is.

## Accounts

Only company accounts are used with AI tools. Nobody uses a personal account or a personal subscription for the team's code.
Only the tools, models, and MCP servers listed in `permissions/ai-tools.json` are used.

## Accountability

The developer who opens a pull request is accountable for every line in it, whoever wrote the line.
That developer reviews agent-written code before the pull request is opened, as carefully as their own. "The agent wrote it" does not excuse a defect.
An agent never approves and never merges a pull request.
