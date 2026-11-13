import copy
import json
import unittest

from tugboat import aitools, formats, team_repo
from tugboat.errors import DataError

from test_rules import RuleCase

REGISTER = {
    "schema": "tug.ai-tools/1",
    "agent_tools": [{"id": "claude-code", "name": "Claude Code"}],
    "models": [],
    "mcp_servers": [
        {"id": "github", "name": "GitHub MCP server", "note": "PRs.", "hosts": ["api.githubcopilot.com"]},
        {"id": "figma", "name": "Figma MCP server", "hosts": ["mcp.figma.com"], "commands": ["figma-developer-mcp", "@acme/figma-mcp"]},
        {"id": "docs", "name": "Docs server"},
    ],
}


def changed(**servers):
    register = copy.deepcopy(REGISTER)
    register.update(servers)
    return register


class FormatTests(unittest.TestCase):
    def test_a_server_may_say_how_it_is_recognised(self):
        self.assertEqual(formats.validate_ai_tools(copy.deepcopy(REGISTER)), REGISTER)

    def test_what_is_refused(self):
        server = REGISTER["mcp_servers"][0]
        for name, register, message in (
            ("a host on an agent tool", changed(agent_tools=[{"id": "x", "name": "X", "hosts": ["x.example.com"]}]), "unknown fields: hosts"),
            ("a command on a model", changed(models=[{"id": "m", "name": "M", "commands": ["m"]}]), "unknown fields: commands"),
            ("an address instead of a host", changed(mcp_servers=[{**server, "hosts": ["https://api.githubcopilot.com/mcp"]}]), "must be a host name in lowercase"),
            ("a host in capitals", changed(mcp_servers=[{**server, "hosts": ["API.GitHubCopilot.com"]}]), "must be a host name in lowercase"),
            ("a host with no dot", changed(mcp_servers=[{**server, "hosts": ["localhost"]}]), "must be a host name in lowercase"),
            ("a command line", changed(mcp_servers=[{**server, "commands": ["npx -y some-server"]}]), "must be a program or package name with no spaces"),
            ("an empty list", changed(mcp_servers=[{**server, "hosts": []}]), "must have between 1 and 10 items"),
            ("one host for two servers", changed(mcp_servers=[server, {"id": "other", "name": "Other", "hosts": ["api.githubcopilot.com"]}]), "hosts lists 'api.githubcopilot.com' more than once"),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(DataError, message):
                formats.validate_ai_tools(register)

    def test_the_starter_register_says_how_each_server_is_recognised(self):
        register = formats.validate_ai_tools(json.loads(team_repo.starter_layout()["permissions/ai-tools.json"]))
        self.assertEqual({entry["id"]: entry["hosts"] for entry in register["mcp_servers"]}, {"github": ["api.githubcopilot.com"], "atlassian": ["mcp.atlassian.com"]})
        self.assertEqual([entry["id"] for entry in register["agent_tools"]], ["claude-code", "copilot-cli", "copilot-vscode"])
        self.assertEqual(register["models"], [])
        readme = team_repo.starter_layout()["permissions/README.md"]
        self.assertIn("A group with entries is the whole list", readme)


class MatchTests(unittest.TestCase):
    def match(self, **options):
        found = aitools.match_server(REGISTER, **options)
        return found["id"] if found else None

    def test_a_remote_server_is_known_by_the_host_of_its_address_and_nothing_else(self):
        self.assertEqual(self.match(url="https://api.githubcopilot.com/mcp"), "github")
        self.assertEqual(self.match(url="https://API.GitHubCopilot.com:443/mcp/x?y=1"), "github")
        for url in (
            "https://evil.example/api.githubcopilot.com",
            "https://api.githubcopilot.com.evil.example/mcp",
            "https://evil.example/?host=api.githubcopilot.com",
            "https://api.githubcopilot.com@evil.example/mcp",
            "https://githubcopilot.com/mcp",
            "not a url",
            "",
        ):
            with self.subTest(url=url):
                self.assertIsNone(self.match(url=url))

    def test_a_local_server_is_known_by_a_word_of_its_command_with_or_without_a_version(self):
        self.assertEqual(self.match(command=["npx", "-y", "figma-developer-mcp", "--stdio"]), "figma")
        self.assertEqual(self.match(command=["npx", "-y", "figma-developer-mcp@0.6.4", "--stdio"]), "figma")
        self.assertEqual(self.match(command=["npx", "@acme/figma-mcp@2.0.0"]), "figma")
        self.assertIsNone(self.match(command=["npx", "-y", "figma-developer-mcp-evil"]))
        self.assertIsNone(self.match(command=["node", "server.js", "--name=figma-developer-mcp"]))
        self.assertIsNone(self.match(command=[]))

    def test_an_entry_that_says_nothing_about_recognition_matches_nothing(self):
        self.assertIsNone(self.match(url="https://docs.example.com/mcp", command=["docs"]))
        self.assertIsNone(self.match())


class RegisterInTheSessionTests(RuleCase):
    def merge_register(self, register):
        return self.adopt(("permissions/ai-tools.json", json.dumps(register, indent=2) + "\n"))

    def test_the_agent_is_given_the_three_lists_and_told_not_to_go_beyond_them(self):
        text = self.instructions(self.ios)
        section = text[text.index("## Approved AI tools") :]
        for expected in (
            "The team's list of approved AI tools is `.tugboat/team/permissions/ai-tools.json`",
            "- Agent tools: Claude Code; GitHub Copilot CLI; GitHub Copilot in VS Code",
            "- Models: the team has not made this list yet, so nothing is ruled out here.",
            "- MCP servers: GitHub MCP server (api.githubcopilot.com); Atlassian MCP server (mcp.atlassian.com)",
            "Do not add, set up, or suggest an agent tool, a model, or an MCP server that is not on it.",
            "You cannot approve one.",
        ):
            self.assertIn(expected, section)

    def test_a_merged_change_reaches_the_agent_and_a_local_edit_never_does(self):
        self.instructions(self.ios)
        clone = self.tmp / "home/team-repos/github.com/acme/team"
        (clone / "permissions/ai-tools.json").write_text(json.dumps(changed(models=[{"id": "sneaked", "name": "Sneaked in"}])), encoding="utf-8")
        self.assertNotIn("Sneaked in", self.instructions(self.ios))
        self.git(clone, "checkout", "--quiet", "--", "permissions/ai-tools.json")
        self.merge_register(changed(models=[{"id": "model-a", "name": "Model A"}]))
        text = self.instructions(self.ios)
        self.assertIn("- Models: Model A", text)
        self.assertIn("- MCP servers: GitHub MCP server (api.githubcopilot.com); Figma MCP server (mcp.figma.com, figma-developer-mcp, @acme/figma-mcp); Docs server", text)
        self.assertIn("- Permissions changed: approved AI tools (.tugboat/team/permissions/ai-tools.json)", self.sync_out)

    def test_a_register_that_cannot_be_read_is_named_and_not_used(self):
        self.merge_register({"schema": "tug.ai-tools/1", "agent_tools": [], "models": []})
        text = self.instructions(self.ios)
        self.assertIn("No list of approved AI tools is in use. Not loaded: the AI tool register permissions/ai-tools.json was not loaded: AI tool register is missing fields: mcp_servers.", text)
        self.assertIn("Note: the AI tool register permissions/ai-tools.json was not loaded", self.sync_out)

    def test_a_team_repo_without_a_register(self):
        self.git(self.work, "pull", "--quiet", "--ff-only", "origin", "main")
        self.git(self.work, "rm", "--quiet", "permissions/ai-tools.json")
        self.push("README.md", "no register\n")
        self.assertIn("## Approved AI tools\n\nThe team repo has no list of approved AI tools.\n", self.instructions(self.ios))


if __name__ == "__main__":
    unittest.main()
