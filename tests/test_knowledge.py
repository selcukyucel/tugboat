import unittest

from tugboat import formats, frontmatter, globmatch, knowledge, secretscan
from tugboat.errors import DataError

from test_formats import NOTE
from test_rules import RuleCase

# Seeded secrets are put together here, so no file in this repo holds one whole.
GITHUB_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
PRIVATE_KEY = "-----BEGIN RSA " + "PRIVATE KEY-----"
SLACK_TOKEN = "xoxb-" + "1234567890-abcdefghij"
GOOGLE_KEY = "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"
STRIPE_KEY = "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc"
AI_KEY = "sk-ant-" + "api03-abcdefghijklmnopqrstuv"
JWT = "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"


def note(path, title, paths, *, repo="acme/shop-ios", owner="@pat", body=None, verified="2026-11-13"):
    header = {"title": title, "owner": owner, "repo": repo, "paths": paths, "last_verified": verified}
    return f"knowledge/{path}", frontmatter.render(header, body or f"What we know about {title}.")


class SecretTests(unittest.TestCase):
    def test_each_known_shape_is_found_and_named_with_its_line(self):
        for kind, text in (
            ("GitHub token", f"The CI token is {GITHUB_TOKEN}."),
            ("GitHub token", "github_pat_" + "11ABCDEFG0abcdefghijkl_mnopqrstuv"),
            ("AWS access key", f"key id {AWS_KEY}"),
            ("private key", PRIVATE_KEY),
            ("private key", "-----BEGIN " + "PRIVATE KEY-----"),
            ("private key", "-----BEGIN OPENSSH " + "PRIVATE KEY-----"),
            ("Slack token", SLACK_TOKEN),
            ("Google API key", f"Maps: {GOOGLE_KEY}"),
            ("Stripe key", STRIPE_KEY),
            ("AI provider key", AI_KEY),
            ("signed token (JWT)", f"Authorization: Bearer {JWT}"),
            ("password or key", "password = hunter2hunter2"),
            ("password or key", 'let apiKey = "9f8e7d6c5b4a39281706"'),
            ("password or key", "CLIENT_SECRET: Zx9Yw8Vu7Ts6Rq5P"),
            ("password or key", '"api_key": "abc123def456ghi789"'),
            ("password in a URL", "postgres://admin:s3cretpw@db.example.com/shop"),
        ):
            with self.subTest(text=text[:24]):
                self.assertEqual(secretscan.find(f"First line.\n{text}\nLast line."), [secretscan.Finding(kind, 2)])

    def test_ordinary_writing_about_secrets_is_not_a_finding(self):
        for text in (
            "The password: validation happens in LoginViewModel.",
            "The token is refreshed when the server answers 401.",
            "apiKey = BuildConfig.MAPS_API_KEY",
            "let token = keychain.read(account)",
            "let tokenStore = TokenStoreV2Factory()",
            "let token = sessionTokenV2",
            "val apiKey = Keys2024.mapsKey12",
            "secret = secrets[index12345]",
            "password = <your password>",
            "API_KEY=${MAPS_API_KEY}",
            "api_key: your-api-key-here",
            "secret: xxxxxxxxxxxxxxxx",
            "https://user:password@example.com is the old form. Do not use it.",
            "See https://example.com/docs/auth#tokens and Sources/Auth/TokenStore.swift:42.",
            "The access token lives for 15 minutes and the refresh token for 30 days.",
            "commit 9e2ae27c0ffee1234567890abcdef1234567890a fixed it",
        ):
            with self.subTest(text=text):
                self.assertEqual(secretscan.find(text), [])

    def test_a_finding_never_repeats_the_secret(self):
        findings = secretscan.find(f"one\n{GITHUB_TOKEN}\n{AWS_KEY}\n{STRIPE_KEY}\n{SLACK_TOKEN}")
        words = secretscan.describe(findings)
        self.assertEqual(words, "GitHub token on line 2, AWS access key on line 3, Stripe key on line 4, 1 more")
        for secret in (GITHUB_TOKEN, AWS_KEY, STRIPE_KEY, SLACK_TOKEN):
            self.assertNotIn(secret, words)

    def test_a_transcript_is_several_turn_lines_from_two_speakers(self):
        chat = "User: why does login fail?\nAssistant: The token expired.\nUser: fix it\nAssistant: Done."
        self.assertTrue(secretscan.looks_like_transcript(chat))
        self.assertTrue(secretscan.looks_like_transcript("**Human:** a\n\n**Claude:** b\n\n**Human:** c\n\n**Claude:** d"))
        self.assertTrue(secretscan.looks_like_transcript("> user: a\n> copilot: b\n> user: c\n> copilot: d"))
        saved = '{"role": "user", "content": "a"}\n{"role": "assistant", "content": "b"}\n' * 2
        self.assertTrue(secretscan.looks_like_transcript(saved))
        for plain in (
            "User: the person who signs in.\nSystem: iOS 17 and later.",  # two definitions, not a chat
            "User: a\nUser: b\nUser: c\nUser: d",  # one speaker: a list
            "The user: taps Sign in. The assistant: is not involved.",  # not at the start of a line
            "Users sign in with a code. The system sends it by email.",
        ):
            with self.subTest(plain=plain):
                self.assertFalse(secretscan.looks_like_transcript(plain))


class NoteFormatTests(unittest.TestCase):
    def refuse(self, body, message, **fields):
        with self.assertRaises(DataError) as caught:
            formats.validate_note({**NOTE, **fields}, body)
        self.assertIn(message, str(caught.exception))
        return str(caught.exception)

    def test_a_note_with_a_secret_pattern_fails_and_the_message_does_not_repeat_it(self):
        message = self.refuse(f"Tokens refresh on 401.\n\nUse {GITHUB_TOKEN} to try it.", "note holds what looks like a secret: GitHub token on line 3 of its text.")
        self.assertNotIn(GITHUB_TOKEN, message)
        self.assertIn("If it is a real secret, replace it where it is used too", message)
        self.refuse("Fine text.", "note header holds what looks like a secret (AWS access key).", title=f"Key {AWS_KEY}")

    def test_a_note_that_is_a_raw_chat_transcript_fails(self):
        chat = "User: why does login fail?\nAssistant: The token expired.\nUser: fix it\nAssistant: Done."
        self.refuse(chat, "note looks like a raw chat transcript")

    def test_a_note_about_tokens_in_plain_sentences_passes(self):
        text = "The access token lives for 15 minutes. `TokenStore` refreshes it when the server answers 401.\nThe password: field is checked in `LoginViewModel`."
        self.assertEqual(formats.validate_note(dict(NOTE), text)["text"], text)


class FolderMatchTests(unittest.TestCase):
    def test_a_folder_is_reached_by_a_pattern_that_covers_it_or_lies_inside_it(self):
        for pattern, folder, expected in (
            ("Sources/Auth/**", "Sources/Auth", True),
            ("Sources/Auth/Tokens/**", "Sources/Auth", True),  # inside the folder
            ("Sources/Auth/Tokens/Store.swift", "Sources", True),
            ("Sources/**", "Sources/Auth/Tokens", True),  # covers the folder
            ("Sources", "Sources/Auth", True),
            ("Sources/Checkout/**", "Sources/Auth", False),
            ("Sources/Checkout", "Sources/Auth", False),
            ("Tests/**", "Sources", False),
            ("**/*.xcstrings", "Sources/Home", True),  # such a file can be anywhere
            ("Sources/*/Views/**", "Sources/Auth", True),
            ("Sources/*/Views/**", "Sources/Auth/Models", False),
        ):
            with self.subTest(pattern=pattern, folder=folder):
                self.assertEqual(globmatch.reaches(pattern, folder), expected)


NOTES = (
    note("auth/token-refresh.md", "How login tokens are refreshed", ["Sources/Auth/**", "Tests/AuthTests/**"]),
    note("auth/keychain.md", "Keychain access groups", ["Sources/Auth/Keychain/**"], owner="@sam"),
    note("checkout/tax.md", "Tax is rounded per line.", ["Sources/Checkout/**"], verified="2026-06-01"),
    note("android/login.md", "Login on Android", ["app/src/main/java/com/acme/login/**"], repo="acme/shop-android"),
)


class NotesForTheTaskTests(RuleCase):
    def notes(self, root, *paths, cwd=None):
        return self.tug(cwd or root, "notes", *paths)

    def ready(self, *extra):
        self.adopt(*NOTES, *extra)
        return self.instructions(self.ios)

    def titles(self, out):
        return [line[2:].split(":")[0] for line in out.splitlines() if line.startswith("- ")]

    def test_a_task_on_one_module_gets_that_modules_notes_and_no_others(self):
        self.ready()
        code, out, err = self.notes(self.ios, "Sources/Auth/Login/LoginView.swift")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out.splitlines(),
            ["- How login tokens are refreshed: .tugboat/team/knowledge/auth/token-refresh.md (covers Sources/Auth/Login/LoginView.swift; owner @pat; last verified 2026-11-13)"],
        )
        code, out, _ = self.notes(self.ios, "Sources/Checkout/Cart.swift", "Sources/Auth/Keychain/Store.swift")
        self.assertEqual(code, 0)
        self.assertEqual(self.titles(out), ["Keychain access groups", "How login tokens are refreshed", "Tax is rounded per line."])
        self.assertIn("(covers Sources/Checkout/Cart.swift; owner @pat; last verified 2026-06-01)", out)
        self.assertNotIn("Login on Android", out)

    def test_a_folder_also_gets_the_notes_about_what_is_inside_it(self):
        self.ready()
        (self.ios / "Sources/Auth/Keychain").mkdir(parents=True)
        code, out, _ = self.notes(self.ios, "Sources/Auth")
        self.assertEqual(code, 0)
        self.assertEqual(self.titles(out), ["Keychain access groups", "How login tokens are refreshed"])
        # The same name as a file that does not exist yet is a file: only a note that covers it counts.
        code, out, _ = self.notes(self.ios, "Sources/Auth/Keychain.swift")
        self.assertEqual(self.titles(out), ["How login tokens are refreshed"])

    def test_no_note_for_a_path_is_said_plainly_and_is_not_an_error(self):
        self.ready()
        code, out, err = self.notes(self.ios, "Sources/Home/HomeView.swift")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.strip(), "No knowledge note covers Sources/Home/HomeView.swift. There is none to read for it.")
        code, out, _ = self.notes(self.ios, "../outside.swift", "Sources/Checkout/Cart.swift")
        self.assertEqual(code, 1)
        self.assertIn("../outside.swift: ../outside.swift is outside this repo", out)
        self.assertEqual(self.titles(out), ["Tax is rounded per line."])

    def test_with_no_path_every_note_about_this_repo_is_listed(self):
        self.ready()
        code, out, _ = self.notes(self.ios)
        self.assertEqual(code, 0)
        self.assertEqual(self.titles(out), ["Keychain access groups", "How login tokens are refreshed", "Tax is rounded per line."])
        self.instructions(self.android)
        code, out, _ = self.notes(self.android)
        self.assertEqual(self.titles(out), ["Login on Android"])
        code, out, _ = self.notes(self.android, "app/src/main/java/com/acme/login/LoginActivity.kt")
        self.assertEqual(self.titles(out), ["Login on Android"])

    def test_the_agent_gets_an_index_of_its_own_repos_notes_and_not_their_text(self):
        text = self.ready()
        section = text[text.index("## Knowledge notes") :]
        for expected in (
            "The team repo has 3 notes about this repo.",
            "- `Sources/Auth/**`, `Tests/AuthTests/**`: How login tokens are refreshed. File: `.tugboat/team/knowledge/auth/token-refresh.md` (last verified 2026-11-13)",
            "- `Sources/Checkout/**`: Tax is rounded per line. File: `.tugboat/team/knowledge/checkout/tax.md` (last verified 2026-06-01)",
            "run `tug notes <path>...` with the files or folders of the task",
            "Do not read the other notes.",
            "the code is right",
        ):
            self.assertIn(expected, section)
        for absent in ("Login on Android", "What we know about", "README"):
            self.assertNotIn(absent, section)
        self.assertIn('- Note added: auth/token-refresh.md "How login tokens are refreshed" (.tugboat/team/knowledge/auth/token-refresh.md)', self.sync_out)
        android = self.instructions(self.android)
        self.assertIn("The team repo has 1 note about this repo.", android)
        self.assertNotIn("token-refresh", android[android.index("## Knowledge notes") :])

    def test_a_repo_with_no_note_is_told_there_is_none(self):
        text = self.instructions(self.ios)
        self.assertIn("The team repo has no knowledge note about this repo, so there is none to read.", text)
        code, out, _ = self.notes(self.ios)
        self.assertEqual((code, out.strip()), (0, "The team repo has no knowledge note about acme/shop-ios."))
        code, out, _ = self.notes(self.ios, "README.md")
        self.assertEqual(code, 0)
        self.assertIn("No knowledge note covers README.md.", out)

    def test_a_long_index_names_thirty_notes_and_counts_the_rest(self):
        many = [note(f"m/n{number:02}.md", f"Note {number:02}", [f"Sources/M{number:02}/**"]) for number in range(33)]
        self.adopt(*many)
        text = self.instructions(self.ios)
        section = text[text.index("## Knowledge notes") :]
        self.assertIn("The team repo has 33 notes about this repo.", section)
        self.assertIn("Note 29.", section)
        self.assertNotIn("Note 30.", section)
        self.assertIn("- and 3 more, not listed here. `tug notes <path>` finds them too.", section)
        code, out, _ = self.notes(self.ios, "Sources/M32/File.swift")
        self.assertEqual(self.titles(out), ["Note 32"])

    def test_a_note_with_a_secret_is_not_given_to_the_agent_and_fails_the_team_repo_check(self):
        text = self.ready(note("auth/leak.md", "The staging login", ["Sources/Auth/**"], body=f"Sign in with this token:\n{GITHUB_TOKEN}"))
        section = text[text.index("## Knowledge notes") :]
        self.assertNotIn("The staging login", section)
        self.assertIn("The team repo has 3 notes about this repo.", section)
        self.assertIn("Note: the note knowledge/auth/leak.md was not loaded: note holds what looks like a secret: GitHub token on line 2 of its text.", self.sync_out)
        self.assertNotIn(GITHUB_TOKEN, self.sync_out)
        _, out, _ = self.notes(self.ios, "Sources/Auth/Login.swift")
        self.assertEqual(self.titles(out), ["How login tokens are refreshed"])
        self.assertIn("Note: the note knowledge/auth/leak.md was not loaded", out)
        self.git(self.work, "pull", "--quiet", "--ff-only", "origin", "main")
        code, out, _ = self.tug(self.work, "team", "check", ".")
        self.assertEqual(code, 1)
        self.assertIn("knowledge/auth/leak.md: note holds what looks like a secret: GitHub token on line 2 of its text.", out)
        self.assertNotIn(GITHUB_TOKEN, out)

    def test_the_check_warns_about_a_note_for_a_repo_that_is_not_in_the_profile(self):
        self.adopt(*NOTES, note("typo.md", "A typo in the repo", ["Sources/**"], repo="acme/shop-iso"))
        self.git(self.work, "pull", "--quiet", "--ff-only", "origin", "main")
        code, out, _ = self.tug(self.work, "team", "check", ".")
        self.assertEqual(code, 0)
        self.assertIn("warning: knowledge/typo.md: is about the repo acme/shop-iso, which is not in the project profile. No agent will load it.", out)
        self.assertEqual(out.count("warning: knowledge/"), 1)

    def test_many_broken_notes_are_counted_not_all_named(self):
        broken = [(f"knowledge/b{number}.md", "no header\n") for number in range(5)]
        self.adopt(*broken)
        self.instructions(self.ios)
        loaded = knowledge.load(self.ios)
        self.assertEqual(len(loaded.problems), 4)
        self.assertIn("2 more notes were not loaded.", loaded.problems[-1])

    def test_outside_a_repo_that_tugboat_set_up(self):
        other = self.app("other", {"Shop.xcodeproj/project.pbxproj": "x\n"}, "acme/other")
        code, _, err = self.notes(other, "README.md")
        self.assertEqual(code, 1)
        self.assertIn("this folder is not set up with Tugboat.", err)


if __name__ == "__main__":
    unittest.main()
