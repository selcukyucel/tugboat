import json
import os
import stat
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from tugboat import doctor, paths, signing, sync, team_repo, teampr
from tugboat.errors import DataError, TugError

from test_doctor import DoctorCase
from test_init import InitCase
from test_teampr import NOTE_TEXT

# Public keys only, made for these tests. No private key belongs to them.
ED25519 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGtQUDZWhs8k/cbjpwoqhGrHVq3CQCtPVAYwxMNgGnH6"
OTHER = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFZHlzCwSWpvjG1CUn3z9sDoR9Vc1X0XjcDCVmxwE9hs"


def make_key(folder, name="key"):
    """A new SSH key with no passphrase, in a test folder. Returns the path of the private key."""
    path = folder / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "pat@pats-macbook", "-f", os.fspath(path)], check=True, capture_output=True)
    return path


class FormatTests(unittest.TestCase):
    def test_a_public_key_is_read_without_its_comment(self):
        key = signing.parse_public_key(f"{ED25519} pat@pats-macbook\n")
        self.assertEqual((key.key_type, key.text), ("ssh-ed25519", ED25519))

    def test_what_is_not_one_public_key_is_refused(self):
        for name, text, message in (
            ("a private key", "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n", "is a private key"),
            ("nothing", "", "is not one public SSH key"),
            ("two keys", f"{ED25519}\n{OTHER}\n", "is not one public SSH key"),
            ("a type nobody listed", "ssh-dss AAAAB3NzaC1kc3MAAACB", "has the key type 'ssh-dss'"),
            ("not base64", "ssh-ed25519 not*base64", "is not a public SSH key"),
            ("a key of another type", ED25519.replace("ssh-ed25519 ", "ssh-rsa "), "does not match its key type"),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(DataError, message):
                signing.parse_public_key(text)

    def test_the_signers_file_gives_each_key_its_account(self):
        text = signing.STARTER_TEXT + f'\npat-dev namespaces="git" {ED25519}\nsam-dev {OTHER} a comment\n'
        signers = signing.parse_signers(text)
        self.assertEqual([(signer.login, signer.key.text, signer.options) for signer in signers], [("pat-dev", ED25519, ('namespaces="git"',)), ("sam-dev", OTHER, ())])
        self.assertEqual(signing.login_of(signers, signing.parse_public_key(OTHER)), "sam-dev")
        self.assertIsNone(signing.login_of(signers[:1], signing.parse_public_key(OTHER)))
        self.assertEqual(signing.parse_signers(signing.STARTER_TEXT), [])
        retired = signing.parse_signers(f'pat-dev namespaces="git",valid-before="20261231" {ED25519}\n')
        self.assertEqual(retired[0].options, ('namespaces="git"', 'valid-before="20261231"'))

    def test_a_line_that_would_let_a_key_sign_for_others_is_refused(self):
        for name, line, message in (
            ("every account", f"* {ED25519}", "line 1 must start with one GitHub account name"),
            ("a list of accounts", f"pat-dev,sam-dev {ED25519}", "must start with one GitHub account name"),
            ("an email pattern", f"*@example.com {ED25519}", "must start with one GitHub account name"),
            ("a certificate authority", f"pat-dev cert-authority {ED25519}", "has the option 'cert-authority'"),
            ("another namespace", f'pat-dev namespaces="file" {ED25519}', "has the option"),
            ("no key", "pat-dev ssh-ed25519", "must be: account, key type, key"),
            ("a broken key", "pat-dev ssh-ed25519 AAAA", "does not match its key type|is not a public SSH key"),
            ("one key for two accounts", f"pat-dev {ED25519}\nsam-dev {ED25519}", "line 2 lists a key that line above gives to pat-dev"),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(DataError, message):
                signing.parse_signers(line + "\n")
        # One account may have several keys: one for each machine.
        self.assertEqual(len(signing.parse_signers(f"pat-dev {ED25519}\npat-dev {OTHER}\n")), 2)

    def test_a_key_is_added_as_one_line_without_its_comment(self):
        key = signing.parse_public_key(f"{ED25519} pat@pats-macbook")
        first = signing.with_signer("", "pat-dev", key)
        self.assertEqual(first, signing.STARTER_TEXT + f'pat-dev namespaces="git" {ED25519}\n')  # an empty file gets the explanation too
        self.assertNotIn("pats-macbook", first)
        second = signing.with_signer(first, "sam-dev", signing.parse_public_key(OTHER))
        self.assertTrue(second.endswith(f'pat-dev namespaces="git" {ED25519}\nsam-dev namespaces="git" {OTHER}\n'))
        with self.assertRaisesRegex(TugError, "this key is in the team repo's signers already, for the account pat-dev"):
            signing.with_signer(second, "sam-dev", key)
        with self.assertRaisesRegex(TugError, "is not a GitHub account name"):
            signing.with_signer(first, "not a login", signing.parse_public_key(OTHER))

    def test_the_starter_layout_has_the_file_and_the_check_reads_it(self):
        self.assertEqual(team_repo.starter_layout()["signers"], signing.STARTER_TEXT)


class SigningCase(InitCase):
    """A machine that has synced the team repo, and a developer with an SSH key."""

    def setUp(self):
        super().setUp()
        self.assertTrue(sync.sync_team_repo(self.address).ok)
        self.clone = paths.team_repo_clone(self.address)
        self.key = make_key(self.tmp)
        self.public = signing.parse_public_key((self.tmp / "key.pub").read_text(encoding="utf-8"))

    def list_key(self, login="pat-dev", key=None):
        """A person merges the pull request that adds the key."""
        text = (self.work / "signers").read_text(encoding="utf-8")
        (self.work / "signers").write_text(signing.with_signer(text, login, key or self.public), encoding="utf-8")
        self.git(self.work, "commit", "--quiet", "-am", f"Add a signing key of {login}")
        self.git(self.work, "push", "--quiet", "origin", "HEAD:main")
        self.assertTrue(sync.sync_team_repo(self.address).ok)

    def fake_gh(self, *, required=("main", "records"), account_keys=None, login="pat-dev"):
        """A `gh` that answers the three questions the check asks."""
        keys = [{"id": 1, "key": key} for key in (account_keys if account_keys is not None else [self.public.text])]
        nodes = [{"pattern": branch, "requiresCommitSignatures": True} for branch in required if branch != "records"]
        rules = {"records": [{"type": "required_signatures"}] if "records" in required else [], "main": []}
        answers = {
            "graphql": json.dumps({"data": {"repository": {"branchProtectionRules": {"nodes": nodes}}}}),
            "rules-main": json.dumps(rules["main"]),
            "rules-records": json.dumps(rules["records"]),
            "keys": json.dumps(keys),
        }
        folder = self.tmp / "gh-answers"
        folder.mkdir(exist_ok=True)
        for name, text in answers.items():
            (folder / name).write_text(text, encoding="utf-8")
        script = self.tmp / "gh"
        script.write_text(
            "#!/bin/sh\n"
            f'case "$*" in\n'
            '  "pr create"*) echo "https://github.com/acme/team/pull/9" ;;\n'
            f'  *graphql*) cat "{folder}/graphql" ;;\n'
            f'  *rules/branches/main*) cat "{folder}/rules-main" ;;\n'
            f'  *rules/branches/records*) cat "{folder}/rules-records" ;;\n'
            f'  *ssh_signing_keys*) cat "{folder}/keys" ;;\n'
            f'  *"api user"*) echo "{login}" ;;\n'
            '  *) echo "unexpected: $*" >&2; exit 1 ;;\n'
            "esac\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return os.fspath(script)


class MachineTests(SigningCase):
    def test_a_machine_with_no_signing_has_no_key_and_says_why(self):
        setup = signing.machine_setup(self.clone)
        self.assertEqual((setup.key, setup.problem), (None, "Tugboat uses SSH signing keys, and no signing is set there"))
        self.git(self.clone, "config", "gpg.format", "openpgp")
        self.assertEqual(signing.machine_setup(self.clone).problem, "Tugboat uses SSH signing keys, and Git signs with 'openpgp' there")
        self.git(self.clone, "config", "gpg.format", "ssh")
        self.assertEqual(signing.machine_setup(self.clone).problem, "SSH signing is chosen and no key is named")
        self.git(self.clone, "config", "user.signingkey", os.fspath(self.tmp / "missing"))
        self.assertIn("could not be read", signing.machine_setup(self.clone).problem)

    def test_the_key_is_set_inside_the_clone_only_and_only_the_public_key_is_read(self):
        opened = []
        real = type(self.key).read_text

        def watched(path, *args, **kwargs):
            opened.append(path.name)
            return real(path, *args, **kwargs)

        with mock.patch("pathlib.Path.read_text", watched):
            key = signing.use_key(self.clone, os.fspath(self.tmp / "key.pub"))
            again = signing.use_key(self.clone, os.fspath(self.key))  # the private key's path: the .pub beside it is read
            setup = signing.machine_setup(self.clone)
        self.assertEqual(key, self.public)
        self.assertEqual(again, self.public)
        self.assertEqual(setup.key, self.public)
        self.assertEqual(set(opened), {"key.pub"})
        self.assertEqual(self.git(self.clone, "config", "--local", "--get", "gpg.format"), "ssh")
        self.assertEqual(self.git(self.clone, "config", "--local", "--get", "user.signingkey"), os.fspath(self.key))
        self.assertNotIn("signingkey", (self.config).read_text(encoding="utf-8"))  # the machine's own Git settings are as they were
        self.assertNotIn("signingkey", self.git(self.ios, "config", "--list"))  # and an app repo signs as before

    def test_a_key_written_into_the_git_setting_is_read_too(self):
        self.git(self.clone, "config", "gpg.format", "ssh")
        self.git(self.clone, "config", "user.signingkey", f"key::{self.public.text}")
        self.assertEqual(signing.machine_setup(self.clone).key, self.public)

    def test_what_is_no_public_key_file_cannot_be_used(self):
        with self.assertRaisesRegex(TugError, "is not a file. Give the public key"):
            signing.use_key(self.clone, os.fspath(self.tmp / "nothing.pub"))
        lonely = self.tmp / "lonely.pub"
        lonely.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n", encoding="utf-8")
        with self.assertRaisesRegex(TugError, "is a private key"):
            signing.use_key(self.clone, os.fspath(lonely))
        self.assertIsNone(signing.machine_setup(self.clone).key)

    def test_a_test_commit_is_signed_and_the_team_s_file_names_the_account(self):
        signing.use_key(self.clone, os.fspath(self.key))
        worked, sentence = signing.test_signature(self.clone)
        self.assertEqual((worked, sentence), (False, "Git signed a test commit, and the team repo's signers does not have the key that signed it"))
        self.list_key("pat-dev")
        worked, sentence = signing.test_signature(self.clone)
        self.assertEqual((worked, sentence), (True, "Git signed a test commit, and the team repo's signers gives the signature to the account pat-dev"))
        # Nothing of the test is on a branch.
        self.assertEqual(self.git(self.clone, "status", "--porcelain"), "")
        self.assertEqual(self.git(self.clone, "for-each-ref", "--format=%(refname:short)", "refs/heads"), "main")
        # A key that is listed for another account's name signs as that account and as nobody else.
        other = make_key(self.tmp, "other")
        signing.use_key(self.clone, os.fspath(other))
        self.assertFalse(signing.test_signature(self.clone)[0])

    def test_a_key_that_cannot_sign_fails_with_git_s_reason(self):
        signing.use_key(self.clone, os.fspath(self.key))
        os.unlink(self.key)  # only the public key is left, and no agent holds the private one
        with mock.patch.dict(os.environ, {"SSH_AUTH_SOCK": ""}):
            worked, sentence = signing.test_signature(self.clone)
        self.assertFalse(worked)
        self.assertIn("Git could not sign with the key", sentence)


class ProposeTests(SigningCase):
    def test_a_proposal_is_signed_when_the_machine_has_a_key_and_can_change_the_signers_file(self):
        signing.use_key(self.clone, os.fspath(self.key))
        content = signing.with_signer(signing.signers_text(self.clone), "pat-dev", self.public)
        proposal = teampr.propose(self.address, title="Add a signing key of pat-dev", body="", changes={"signers": content.encode("utf-8")})
        self.assertEqual(proposal.files, ("signers",))
        allowed = self.tmp / "allowed"
        allowed.write_text(content, encoding="utf-8")
        shown = self.git(self.remote, "-c", f"gpg.ssh.allowedSignersFile={allowed}", "log", "-1", "--format=%G? %GS", proposal.branch)
        self.assertEqual(shown, "G pat-dev")

    def test_a_proposal_from_a_machine_with_no_key_is_not_signed_and_still_made(self):
        proposal = teampr.propose(self.address, title="Add a note", body="", changes={"knowledge/auth/tokens.md": NOTE_TEXT})
        self.assertEqual(self.git(self.remote, "log", "-1", "--format=%G?", proposal.branch), "N")

    def test_a_key_that_cannot_sign_stops_the_proposal_and_leaves_nothing_behind(self):
        signing.use_key(self.clone, os.fspath(self.key))
        os.unlink(self.key)
        with mock.patch.dict(os.environ, {"SSH_AUTH_SOCK": ""}), self.assertRaisesRegex(TugError, "Git could not sign the commit with the key set for the team repo, so nothing was proposed"):
            teampr.propose(self.address, title="Add a signing key", body="", changes={"signers": signing.with_signer("", "pat-dev", self.public).encode("utf-8")})
        self.assertEqual(self.git(self.remote, "for-each-ref", "--format=%(refname:short)", "refs/heads"), "main")
        self.assertEqual(len(self.git(self.clone, "worktree", "list").splitlines()), 1)

    def test_the_team_repo_check_reads_the_signers_file(self):
        self.assertEqual([problem for problem in team_repo.check_team_repo(self.work).problems if problem.path == "signers"], [])
        (self.work / "signers").write_text(f"* {ED25519}\n", encoding="utf-8")
        problems = [(problem.level, problem.message) for problem in team_repo.check_team_repo(self.work).problems if problem.path == "signers"]
        self.assertEqual(problems, [("error", "signers line 1 must start with one GitHub account name")])
        os.unlink(self.work / "signers")
        problems = [(problem.level, problem.message[:16]) for problem in team_repo.check_team_repo(self.work).problems if problem.path == "signers"]
        self.assertEqual(problems, [("warning", "is not there. Wi")])


class CommandTests(SigningCase):
    def setUp(self):
        super().setUp()
        self.ok(self.ios, "--yes", "--team-repo", "git@github.com:acme/team.git", "--tracker", "github-projects", "--approval", "owner")
        self.merge()

    def signing(self, *argv, answers=None, gh=""):
        with mock.patch.dict(os.environ, {"TUG_GH": gh}):
            return self.tug(self.ios, "signing", *argv, answers=answers)

    def test_use_then_propose_then_check_sets_a_machine_up(self):
        code, out, err = self.signing("use", os.fspath(self.tmp / "key.pub"))
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("Git now signs with this key inside Tugboat's clone of acme/team: ssh-ed25519", out)
        self.assertIn("Your other repos keep their own signing.", out)
        self.assertIn("2. Run `tug signing propose`.", out)
        self.git(self.clone, "config", "user.signingkey", os.fspath(self.key))  # the test has no agent, so Git gets the private key's path

        code, out, err = self.signing("check")
        self.assertEqual(code, 1)
        self.assertIn("Failed: this machine's key is not in the team repo's `signers`. `tug signing propose` opens the pull request that adds it.", out)

        code, out, err = self.signing("propose", "--yes", gh=self.fake_gh())
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("with 1 file: signers.", out)
        self.assertIn("Pull request: https://github.com/acme/team/pull/9", out)
        self.merge(next(branch for branch in self.proposals() if branch.startswith("tug/add-a-signing-key-of-pat-dev")))

        code, out, err = self.signing("check", gh=self.fake_gh())
        self.assertEqual((code, err), (0, ""), out)
        for line in (
            "Passed: the team repo's `signers` gives this machine's key to the GitHub account pat-dev.",
            "Passed: Git signed a test commit, and the team repo's signers gives the signature to the account pat-dev.",
            "Passed: GitHub requires signed commits on the branch 'main' of acme/team.",
            "Passed: GitHub requires signed commits on the branch 'records' of acme/team.",
            "Passed: the key is a signing key of the GitHub account pat-dev.",
        ):
            self.assertIn(line + "\n", out)
        seen = signing.read_check(self.address)
        self.assertEqual((seen["login"], seen["signature"], seen["branches"], seen["account_key"]), ("pat-dev", True, {"main": True, "records": True}, True))

        code, out, err = self.signing("propose", "--yes", gh=self.fake_gh())
        self.assertEqual(code, 1)
        self.assertIn("this key is in the team repo's signers already, for the account pat-dev", err)

    def test_check_says_what_github_does_not_require_and_what_the_account_lacks(self):
        signing.use_key(self.clone, os.fspath(self.key))
        self.list_key("pat-dev")
        code, out, _ = self.signing("check", gh=self.fake_gh(required=("main",), account_keys=[OTHER]))
        self.assertEqual(code, 1)
        self.assertIn("Passed: GitHub requires signed commits on the branch 'main' of acme/team.\n", out)
        self.assertIn("Failed: GitHub does not require signed commits on the branch 'records' of acme/team.\n", out)
        self.assertIn("Failed: the key is not a signing key of the GitHub account pat-dev.\n", out)
        code, out, _ = self.signing("check")  # no gh on the machine
        self.assertEqual(code, 0)
        self.assertIn("Untested: the GitHub command-line tool `gh` is not installed, so GitHub was not asked.", out)
        self.assertIsNone(signing.read_check(self.address)["branches"])

    def test_propose_needs_a_key_an_account_and_a_person(self):
        code, _, err = self.signing("propose", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("this machine has no signing key for the team repo", err)
        self.assertIn("`tug signing use ~/.ssh/<key>.pub`", err)
        signing.use_key(self.clone, os.fspath(self.key))
        before = self.proposals()
        code, _, err = self.signing("propose", "--yes")
        self.assertIn("which GitHub account is this key for? Pass --login ACCOUNT.", err)
        code, out, err = self.signing("propose", "--login", "pat-dev")  # no terminal and no --yes
        self.assertEqual(code, 1)
        self.assertIn("a pull request needs a person to confirm it", err)
        code, out, err = self.signing("propose", "--login", "pat-dev", answers=["n"])
        self.assertIn("stopped. Nothing was proposed.", err)
        self.assertEqual(self.proposals(), before)
        code, out, err = self.signing("propose", "--login", "pat-dev", answers=["y"])
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("This opens a pull request in acme/team: Add a signing key of pat-dev", out)
        self.assertEqual(len(self.proposals()), len(before) + 1)


class DoctorTests(DoctorCase):
    def signing_checks(self):
        return [(check.result, check.text) for check in doctor.check_signing(self.address)]

    def list_key(self, login, key):
        text = (self.work / "signers").read_text(encoding="utf-8")
        (self.work / "signers").write_text(signing.with_signer(text, login, key), encoding="utf-8")
        self.git(self.work, "commit", "--quiet", "-am", "Add a signing key")
        self.git(self.work, "push", "--quiet", "origin", "HEAD:main")
        self.assertTrue(sync.sync_team_repo(self.address).ok)

    def test_a_team_that_has_not_started_gets_one_advisory_line(self):
        self.assertEqual(self.signing_checks(), [(doctor.ADVISORY, "The team repo lists no key in its `signers` file yet, so no task record can be published or verified.")])

    def test_a_machine_with_no_signing_key_fails_clearly_once_the_team_has_started(self):
        self.list_key("sam-dev", signing.parse_public_key(OTHER))
        (check,) = doctor.check_signing(self.address)
        self.assertEqual(check.result, doctor.FAILED)
        self.assertEqual(check.text, "This machine has no signing key for the team repo: Tugboat uses SSH signing keys, and no signing is set there.")
        self.assertEqual(check.fix, "Pick an SSH key you have and run `tug signing use ~/.ssh/<key>.pub`. Then run `tug signing propose`.")
        code, out, _ = self.tug(self.ios, "doctor")
        self.assertEqual(code, 1)
        self.assertIn("This machine has no signing key for the team repo", out)

    def test_a_key_that_the_team_repo_does_not_list_fails_and_a_listed_one_passes(self):
        clone = paths.team_repo_clone(self.address)
        key = make_key(self.tmp)
        public = signing.use_key(clone, os.fspath(key))
        self.list_key("sam-dev", signing.parse_public_key(OTHER))
        self.assertEqual(self.signing_checks(), [(doctor.FAILED, "This machine's signing key (ssh-ed25519) is not in the team repo's `signers`, so a record from this machine would not be verified.")])
        self.list_key("pat-dev", public)
        passed = (doctor.PASSED, "This machine signs for the team repo with an SSH key (ssh-ed25519) that the team repo's `signers` gives to the GitHub account pat-dev.")
        untested = "Whether GitHub requires signed commits on the team repo, and whether this key is a signing key of the account, was not checked on this machine."
        self.assertEqual(self.signing_checks(), [passed, (doctor.UNTESTED, untested)])

        signing.save_check(self.address, public, {"login": "pat-dev", "signature": True, "branches": {"main": True, "records": False}, "account_key": False})
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.assertEqual(
            self.signing_checks(),
            [
                passed,
                (doctor.FAILED, f"On {day} GitHub did not require signed commits on the team repo's branch 'records'."),
                (doctor.FAILED, f"On {day} this key was not a signing key of the GitHub account pat-dev, so GitHub would not show its commits as verified."),
            ],
        )
        signing.save_check(self.address, public, {"login": "pat-dev", "signature": True, "branches": {"main": True, "records": True}, "account_key": True})
        self.assertEqual([result for result, _ in self.signing_checks()], [doctor.PASSED, doctor.PASSED, doctor.PASSED])
        # What was seen for another key, or long ago, says nothing about now.
        old = json.loads(signing.check_file(self.address).read_text(encoding="utf-8"))
        old["at"] = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
        signing.check_file(self.address).write_text(json.dumps(old), encoding="utf-8")
        self.assertEqual(self.signing_checks()[1], (doctor.UNTESTED, "Whether GitHub requires signed commits on the team repo was last checked more than 30 days ago."))
        signing.save_check(self.address, signing.parse_public_key(OTHER), {"login": "sam-dev", "signature": True, "branches": {"main": True, "records": True}, "account_key": True})
        self.assertEqual(self.signing_checks()[1][0], doctor.UNTESTED)

    def test_a_signers_file_that_cannot_be_used_fails(self):
        (self.work / "signers").write_text(f"* {ED25519}\n", encoding="utf-8")
        self.git(self.work, "commit", "--quiet", "-am", "A bad line")
        self.git(self.work, "push", "--quiet", "origin", "HEAD:main")
        self.assertTrue(sync.sync_team_repo(self.address).ok)
        self.assertEqual(self.signing_checks(), [(doctor.FAILED, "The team repo's `signers` file cannot be used: signers line 1 must start with one GitHub account name.")])


if __name__ == "__main__":
    unittest.main()
