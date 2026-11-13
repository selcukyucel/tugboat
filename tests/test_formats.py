import copy
import unittest

from tugboat import formats
from tugboat.errors import DataError

PROFILE = {
    "schema": "tug.profile/1",
    "project": "shop",
    "app_repos": [
        {"github": "acme/shop-ios", "default_branch": "main", "platform": "ios"},
        {"github": "acme/shop-android", "default_branch": "release/1.x", "platform": "android"},
    ],
    "tracker": {"kind": "jira", "criteria_source": {"kind": "field", "name": "Acceptance Criteria"}},
    "ci_features": ["secret-scan"],
    "surfaces": ["claude-code", "copilot-cli"],
    "approval_mode": "teammate",
    "minimum_tool_version": "0.2.0",
    "frameworks": [],
}
ESTATE = {
    "schema": "tug.estate/1",
    "apps": [
        {
            "id": "shop-ios",
            "name": "Shop for iOS",
            "platform": "ios",
            "repo": "acme/shop-ios",
            "owner": "ios-team",
            "environments": ["dev", "production"],
            "modules": [{"id": "checkout", "owner": "@pat", "paths": ["Sources/Checkout/**"]}],
        }
    ],
}
RULE = {"id": "IOS-001", "title": "New unit tests use Swift Testing", "scope": "ios", "mode": "advisory", "status": "example"}
NOTE = {"title": "Token refresh", "owner": "@pat", "repo": "acme/shop-ios", "paths": ["Sources/Auth/**"], "last_verified": "2026-11-13"}
DECISION = {"id": "DEC-0001", "title": "Use Swift Testing", "date": "2026-11-13", "status": "accepted", "paths": []}
DECISION_BODY = "## Context\nc\n\n## Options\no\n\n## Choice\nx\n\n## Reason\nr\n"
AGENT = {"name": "reviewer", "description": "Reads and reports.", "tools": ["Read", "Grep"], "rules": ["IOS-001"]}


def changed(base, **changes):
    """A deep copy of base with top-level fields replaced. A value of ... removes the field."""
    result = copy.deepcopy(base)
    for key, value in changes.items():
        if value is ...:
            result.pop(key)
        else:
            result[key] = value
    return result


class Cases(unittest.TestCase):
    def refuse(self, validate, cases):
        for name, (data, message) in cases.items():
            with self.subTest(name=name), self.assertRaisesRegex(DataError, message):
                validate(*data) if isinstance(data, tuple) else validate(data)


class JsonFormatTests(Cases):
    def test_marker(self):
        self.assertEqual(formats.validate_marker({"schema": "tug.team-repo/1"}), {"schema": "tug.team-repo/1"})
        self.refuse(
            formats.validate_marker,
            {
                "newer schema": ({"schema": "tug.team-repo/2"}, 'must have "schema": "tug.team-repo/1"'),
                "extra field": ({"schema": "tug.team-repo/1", "x": 1}, "unknown fields: x"),
                "a list": ([], "must be a JSON object"),
            },
        )

    def test_profile_is_accepted_and_returned_unchanged(self):
        self.assertEqual(formats.validate_profile(copy.deepcopy(PROFILE)), PROFILE)

    def test_profile_accepts_no_repos_yet_and_no_criteria_source_yet(self):
        profile = changed(PROFILE, app_repos=[], tracker={"kind": "github-projects", "criteria_source": None}, approval_mode="owner")
        self.assertEqual(formats.validate_profile(profile)["tracker"]["criteria_source"], None)

    def test_profile_refusals(self):
        repo = PROFILE["app_repos"][0]
        self.refuse(
            formats.validate_profile,
            {
                "missing field": (changed(PROFILE, surfaces=...), "profile is missing fields: surfaces"),
                "unknown field": (changed(PROFILE, colour="blue"), "profile has unknown fields: colour"),
                "wrong schema": (changed(PROFILE, schema="tug.profile/9"), 'must have "schema": "tug.profile/1"'),
                "bad tracker": (changed(PROFILE, tracker={"kind": "trello", "criteria_source": None}), "tracker kind must be one of"),
                "no surface": (changed(PROFILE, surfaces=[]), "surfaces must have between 1 and 3 items"),
                "unknown surface": (changed(PROFILE, surfaces=["xcode"]), "surfaces item must be one of"),
                "surface twice": (changed(PROFILE, surfaces=["claude-code", "claude-code"]), "lists 'claude-code' more than once"),
                "unknown ci feature": (changed(PROFILE, ci_features=["ai-tagging"]), "ci_features item must be one of"),
                "bad approval": (changed(PROFILE, approval_mode="nobody"), "approval_mode must be one of"),
                "bad version": (changed(PROFILE, minimum_tool_version="0.2"), "minimum_tool_version must look like 1.2.3"),
                "bad framework": (changed(PROFILE, frameworks=["eu-ai-act"]), "frameworks item must be one of"),
                "repo as url": (changed(PROFILE, app_repos=[changed(repo, github="https://github.com/acme/shop")]), "written as owner/name"),
                "repo with .git": (changed(PROFILE, app_repos=[changed(repo, github="acme/shop.git")]), "written as owner/name"),
                "backend platform": (changed(PROFILE, app_repos=[changed(repo, platform="backend")]), "platform must be one of"),
                "branch with ..": (changed(PROFILE, app_repos=[changed(repo, default_branch="a..b")]), "not a valid branch name"),
                "repo twice": (changed(PROFILE, app_repos=[repo, changed(repo, github="ACME/shop-ios")]), "more than once"),
                "project not a slug": (changed(PROFILE, project="My Shop"), "project must be lowercase words"),
            },
        )

    def test_protected_paths(self):
        good = {"schema": "tug.protected-paths/1", "paths": [{"pattern": "**/*.p12", "platform": "ios", "reason": "Signing."}]}
        self.assertEqual(formats.validate_protected_paths(copy.deepcopy(good)), good)
        entry = good["paths"][0]
        self.refuse(
            formats.validate_protected_paths,
            {
                "absolute": (changed(good, paths=[changed(entry, pattern="/etc/**")]), "relative pattern"),
                "leaves the repo": (changed(good, paths=[changed(entry, pattern="../other/**")]), "relative pattern"),
                "bad double star": (changed(good, paths=[changed(entry, pattern="src/**.p12")]), r"'\*\*' only as a whole path part"),
                "no reason": (changed(good, paths=[changed(entry, reason=" ")]), "reason cannot be empty"),
                "bad platform": (changed(good, paths=[changed(entry, platform="web")]), "platform must be one of"),
                "same entry twice": (changed(good, paths=[entry, entry]), "more than once"),
            },
        )

    def test_ai_tools(self):
        good = {
            "schema": "tug.ai-tools/1",
            "agent_tools": [{"id": "claude-code", "name": "Claude Code"}],
            "models": [],
            "mcp_servers": [{"id": "github", "name": "GitHub MCP server", "note": "PRs."}],
        }
        self.assertEqual(formats.validate_ai_tools(copy.deepcopy(good)), good)
        self.refuse(
            formats.validate_ai_tools,
            {
                "missing group": (changed(good, models=...), "missing fields: models"),
                "id twice": (changed(good, agent_tools=good["agent_tools"] * 2), "more than once"),
                "unknown entry field": (changed(good, models=[{"id": "m", "name": "M", "url": "x"}]), "unknown fields: url"),
            },
        )

    def test_estate(self):
        self.assertEqual(formats.validate_estate(copy.deepcopy(ESTATE)), ESTATE)
        self.assertEqual(formats.validate_estate({"schema": "tug.estate/1", "apps": []})["apps"], [])
        app = ESTATE["apps"][0]
        module = app["modules"][0]
        self.refuse(
            formats.validate_estate,
            {
                "backend app": (changed(ESTATE, apps=[changed(app, platform="backend")]), "platform must be one of"),
                "module without paths": (changed(ESTATE, apps=[changed(app, modules=[changed(module, paths=[])])]), "paths must have between 1 and 50"),
                "module twice": (changed(ESTATE, apps=[changed(app, modules=[module, module])]), "more than once"),
                "app twice": (changed(ESTATE, apps=[app, app]), "more than once"),
                "no owner": (changed(ESTATE, apps=[changed(app, owner=...)]), "missing fields: owner"),
            },
        )


class MarkdownFormatTests(Cases):
    def test_rule(self):
        rule = formats.validate_rule(dict(RULE), "\nUse Swift Testing.\n")
        self.assertEqual((rule["id"], rule["mode"], rule["text"]), ("IOS-001", "advisory", "Use Swift Testing."))
        self.refuse(
            formats.validate_rule,
            {
                "bad id": ((changed(RULE, id="ios-1"), "x"), "must look like COM-001"),
                "id and scope differ": ((changed(RULE, scope="android"), "x"), "does not match scope 'android'"),
                "backend scope": ((changed(RULE, scope="backend"), "x"), "scope must be one of"),
                "bad mode": ((changed(RULE, mode="blocking"), "x"), "mode must be one of"),
                "bad status": ((changed(RULE, status="draft"), "x"), "status must be one of"),
                "no text": ((dict(RULE), "  \n"), "rule has no text under its header"),
                "extra field": ((changed(RULE, severity="high"), "x"), "unknown fields: severity"),
            },
        )

    def test_note(self):
        note = formats.validate_note(dict(NOTE), "Tokens refresh on 401.")
        self.assertEqual(note["paths"], ["Sources/Auth/**"])
        self.refuse(
            formats.validate_note,
            {
                "no owner": ((changed(NOTE, owner=...), "x"), "note is missing fields: owner"),
                "no paths": ((changed(NOTE, paths=[]), "x"), "note paths must have between 1 and 50"),
                "paths as text": ((changed(NOTE, paths="Sources/**"), "x"), "note paths must be a list"),
                "bad date": ((changed(NOTE, last_verified="13/11/2026"), "x"), "must be a date like"),
                "impossible date": ((changed(NOTE, last_verified="2026-02-30"), "x"), "is not a real date"),
                "bad repo": ((changed(NOTE, repo="shop-ios"), "x"), "written as owner/name"),
                "no text": ((dict(NOTE), ""), "note has no text"),
            },
        )

    def test_decision(self):
        decision = formats.validate_decision(dict(DECISION), DECISION_BODY)
        self.assertEqual((decision["id"], decision["status"]), ("DEC-0001", "accepted"))
        self.refuse(
            formats.validate_decision,
            {
                "bad id": ((changed(DECISION, id="DEC-1"), DECISION_BODY), "must look like DEC-0001"),
                "bad status": ((changed(DECISION, status="done"), DECISION_BODY), "status must be one of"),
                "missing sections": ((dict(DECISION), "## Context\nc\n\n## Choice\nx\n"), "missing sections: ## Options, ## Reason"),
            },
        )

    def test_agent(self):
        agent = formats.validate_agent(dict(AGENT), "You review changes.")
        self.assertEqual(agent["tools"], ["Read", "Grep"])
        self.refuse(
            formats.validate_agent,
            {
                "no tools": ((changed(AGENT, tools=[]), "x"), "tools must have between 1 and 50"),
                "bad tool name": ((changed(AGENT, tools=["rm -rf /"]), "x"), "is not a valid tool name"),
                "bad rule id": ((changed(AGENT, rules=["rule-1"]), "x"), "must look like COM-001"),
                "name not a slug": ((changed(AGENT, name="The Reviewer"), "x"), "name must be lowercase words"),
                "no instructions": ((dict(AGENT), "\n"), "agent definition has no text"),
            },
        )

    def test_skill_header_allows_fields_the_agent_tools_add(self):
        skill = formats.validate_skill_header({"name": "new-screen", "description": "Use for a new screen.", "allowed-tools": "Read"})
        self.assertEqual(skill, {"name": "new-screen", "description": "Use for a new screen."})
        with self.assertRaisesRegex(DataError, "skill is missing fields: description"):
            formats.validate_skill_header({"name": "new-screen"})


if __name__ == "__main__":
    unittest.main()
