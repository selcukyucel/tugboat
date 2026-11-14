# Skills

Recipes that guide the agent through common work, such as a new screen or a new analytics event.
One folder per skill, with a `SKILL.md` inside:

```
skills/
  new-swiftui-screen/
    SKILL.md
```

`SKILL.md` starts with a header that has a `name`, which matches the folder name, and a `description` that says when to use the skill. The steps follow.

Each skill names the rule IDs it applies, such as `IOS-005`, anywhere in its `SKILL.md`. `tug team check` fails a skill that names a rule that is not in `policies/`.

A skill is installed where its rules apply. A skill that names iOS rules and no Android rule goes to iOS repos only, and the other way round. A skill that names rules of both platforms, only company rules, or no rule goes to every app repo.
