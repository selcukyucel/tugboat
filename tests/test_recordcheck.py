import json
import os
import unittest

from tugboat import recordcheck, recordstore, records, taskstate
from tugboat.errors import TugError

from test_formats import changed
from test_knowledge import AWS_KEY, GITHUB_TOKEN
from test_records import CHECKS_REPORT, CLAIM, EVENT, HANDOVER, QA_PACK, SUMMARY, nested
from test_recordstore import StoreCase, at

# Seeded sign-ins are put together here, so no file in this repo holds one whole.
BEARER = "Authorization: Bearer " + "f3a91c0b77e24d5aa8c6" + "19d2"
COOKIE = "Set-Cookie: session=" + "9f8e7d6c5b4a3928" + "1706abcd; Path=/"


def seeded(record, stamp, suffix, path, text):
    return nested(at(record, stamp, suffix), path, text)


class FindingsTests(unittest.TestCase):
    def test_a_clean_record_of_each_kind_has_no_finding(self):
        for record in (CLAIM, EVENT, CHECKS_REPORT, QA_PACK, SUMMARY, HANDOVER):
            with self.subTest(schema=record["schema"], report=record.get("report")):
                self.assertEqual(recordcheck.findings(records.validate_record(record)), ())

    def test_a_secret_is_found_in_any_field_and_named_by_field_and_kind(self):
        for name, record, path, text, reason in (
            ("a summary", SUMMARY, ("summary",), f"Signed in to staging.\n\nThe token is {GITHUB_TOKEN}.", "its field `summary` holds what looks like a secret (GitHub token on line 3)"),
            ("a next action", EVENT, ("next_action",), f"Run it with {AWS_KEY}.", "its field `next_action` holds what looks like a secret (AWS access key)"),
            ("the output of a failed test", CHECKS_REPORT, ("checks", 1, "summary"), f"401 from the API. Request: {BEARER}", "its field `checks[1].summary` holds what looks like a secret (sign-in in an Authorization header)"),
            ("a cookie in a handover note", HANDOVER, ("next_steps",), f"The server answered with {COOKIE}", "its field `next_steps` holds what looks like a secret (sign-in in a cookie)"),
            ("a password in a list", SUMMARY, ("decisions", 0), "Use password = hunter2hunter2 for the test account.", "its field `decisions[0]` holds what looks like a secret (password or key)"),
            ("a branch name", EVENT, ("branch",), f"feat/{GITHUB_TOKEN}", "its field `branch` holds what looks like a secret (GitHub token)"),
        ):
            with self.subTest(name=name):
                record = records.validate_record(nested(record, path, text))  # the schema passes: a secret is valid text
                found = recordcheck.findings(record)
                self.assertEqual(found, (reason,))
                for secret in (GITHUB_TOKEN, AWS_KEY, BEARER.rsplit(" ", 1)[1], "hunter2hunter2"):
                    self.assertNotIn(secret, found[0])  # a reason never repeats the secret

    def test_a_name_and_its_value_in_two_fields_are_read_as_one(self):
        flag = {"name": "payments_api_key", "state": "9f8e7d6c5b4a39281706"}
        record = records.validate_record(changed(QA_PACK, feature_flags=[QA_PACK["feature_flags"][0], flag]))
        self.assertEqual(recordcheck.findings(record), ("its field `feature_flags[1]` holds what looks like a secret (password or key)",))

    def test_several_fields_give_one_reason_each(self):
        record = records.validate_record(changed(HANDOVER, reason=f"I used {GITHUB_TOKEN}.", next_steps=f"Remove {AWS_KEY} from the config."))
        self.assertEqual([reason.split("`")[1] for reason in recordcheck.findings(record)], ["reason", "next_steps"])


class CheckTests(StoreCase):
    def write_raw(self, record, text=None):
        """A file put into the store by hand, as an older tool or a person might leave one."""
        recordstore.ensure(self.repo)
        path = self.store / recordstore.record_path(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record) if text is None else text, encoding="utf-8")
        return recordstore.record_path(record)

    def test_with_no_store_there_is_nothing_to_check(self):
        self.assertEqual(recordcheck.check(self.repo), recordcheck.Checked((), ()))

    def test_clean_records_are_ready_oldest_first(self):
        later = recordstore.write(self.repo, at(EVENT, "2026-11-15T10:15:31.500Z", "b"))
        first = recordstore.write(self.repo, CLAIM)
        checked = recordcheck.check(self.repo)
        self.assertEqual(checked, recordcheck.Checked((first, later), ()))

    def test_a_record_with_a_seeded_secret_stays_local_with_the_reason(self):
        clean = recordstore.write(self.repo, CLAIM)
        bad = recordstore.write(self.repo, seeded(SUMMARY, "2026-11-15T10:15:31Z", "b", ("summary",), f"The CI token is {GITHUB_TOKEN}."))
        checked = recordcheck.check(self.repo)
        self.assertEqual(checked.ready, (clean,))
        self.assertEqual(checked.held, (recordcheck.Held(bad.path, ("its field `summary` holds what looks like a secret (GitHub token)",)),))
        # Nothing was written or removed: the record is where it was, and Git still sees it as new.
        self.assertEqual(recordstore.unpublished(self.repo), tuple(sorted((clean.path, bad.path))))
        self.assertEqual(self.git(self.store, "status", "--porcelain", "--untracked-files=all").count("??"), 2)
        # On its own machine it still counts: the state of the task reads published and unpublished records alike.
        self.assertEqual(len(recordstore.read_all(self.repo).records), 2)
        # The reason is worked out from the file each time.
        self.assertEqual(recordcheck.check(self.repo), checked)

    def test_a_record_that_fails_its_schema_stays_local_with_the_reason(self):
        unknown_field = self.write_raw({**at(CLAIM, "2026-11-15T10:15:31Z", "b"), "password_hint": "none"})
        broken = self.write_raw(at(CLAIM, "2026-11-15T10:15:32Z", "c"), text="{")
        moved = self.write_raw(at(CLAIM, "2026-11-15T10:15:33Z", "d"))
        os.rename(self.store / moved, self.store / moved.replace("101533Z", "101534Z"))
        moved = moved.replace("101533Z", "101534Z")
        checked = recordcheck.check(self.repo)
        self.assertEqual(checked.ready, ())
        reasons = {held.path: held.reasons[0] for held in checked.held}
        self.assertEqual(reasons[unknown_field], "it is not a valid record: claim has unknown fields: password_hint")
        self.assertRegex(reasons[broken], "^it is not a valid record: ")
        self.assertRegex(reasons[moved], "^it is not a valid record: it is not at the path its own content gives")
        for reason in reasons.values():
            self.assertNotIn(os.fspath(self.tmp), reason)  # no folder of this machine in a reason
        self.assertFalse(any(held.newer for held in checked.held))

    def test_a_record_from_a_newer_tool_is_held_and_said_to_be_newer(self):
        newer = self.write_raw({**at(EVENT, "2026-11-15T10:15:31Z", "b"), "schema": "tug.task-event/2", "shape": "unknown to this tool"})
        (held,) = recordcheck.check(self.repo).held
        self.assertEqual((held.path, held.newer), (newer, True))
        self.assertIn("A newer tug wrote it", held.reasons[0])

    def test_a_published_record_is_not_checked_again(self):
        published = recordstore.write(self.repo, seeded(SUMMARY, "2026-11-15T10:15:31Z", "b", ("summary",), f"The CI token is {GITHUB_TOKEN}."))
        self.git(self.store, "add", "--", published.path)
        self.git(self.store, "commit", "--quiet", "-m", "records")  # as an older tool without this check could have done
        self.assertEqual(recordcheck.check(self.repo), recordcheck.Checked((), ()))

    def test_what_is_no_new_record_is_named_and_never_ready(self):
        published = recordstore.write(self.repo, CLAIM)
        gone = recordstore.write(self.repo, at(CLAIM, "2026-11-15T10:15:31Z", "b"))
        self.git(self.store, "add", "-A")
        self.git(self.store, "commit", "--quiet", "-m", "records")
        # A person edits one published record, deletes another, and leaves a file of their own.
        os.chmod(self.store / published.path, 0o644)
        (self.store / published.path).write_text(json.dumps({**CLAIM, "machine": "another-machine"}), encoding="utf-8")
        os.unlink(self.store / gone.path)
        (self.store / "notes.txt").write_text("my notes", encoding="utf-8")
        new = recordstore.write(self.repo, at(EVENT, "2026-11-15T10:15:32Z", "c"))
        checked = recordcheck.check(self.repo)
        self.assertEqual(checked.ready, (new,))
        self.assertEqual(checked.held, ())
        self.assertEqual(checked.other, ("notes.txt",))
        self.assertEqual(checked.changed, tuple(sorted((published.path, gone.path))))

    def test_a_renamed_published_record_counts_as_changed_and_its_old_name_is_no_new_record(self):
        published = recordstore.write(self.repo, CLAIM)
        self.git(self.store, "add", "-A")
        self.git(self.store, "commit", "--quiet", "-m", "records")
        renamed = published.path.replace("3f9a1c2b7d4e", "aaaaaaaaaaaa")
        self.git(self.store, "mv", published.path, renamed)
        found = recordstore.changes(self.repo)
        self.assertEqual((found.new, found.other, found.changed), ((), (), (renamed,)))


class DiscardTests(StoreCase):
    def test_a_held_record_can_be_discarded_and_a_clean_one_takes_its_place(self):
        bad = recordstore.write(self.repo, seeded(SUMMARY, "2026-11-15T10:15:31Z", "b", ("summary",), f"The CI token is {GITHUB_TOKEN}."))
        held = recordcheck.discard(self.repo, bad.path)
        self.assertEqual(held.reasons, ("its field `summary` holds what looks like a secret (GitHub token)",))
        self.assertFalse((self.store / bad.path).exists())
        again = recordstore.write(self.repo, at(SUMMARY, "2026-11-15T10:15:40Z", "c"))
        self.assertEqual(recordcheck.check(self.repo), recordcheck.Checked((again,), ()))
        loaded = taskstate.load(self.repo, SUMMARY["task"], SUMMARY["repo"])
        self.assertEqual(loaded.state.history, (again,))

    def test_only_a_held_unpublished_record_is_discarded(self):
        clean = recordstore.write(self.repo, CLAIM)
        with self.assertRaisesRegex(TugError, "passes the checks, so it will be published"):
            recordcheck.discard(self.repo, clean.path)
        published = recordstore.write(self.repo, seeded(SUMMARY, "2026-11-15T10:15:31Z", "b", ("summary",), f"The CI token is {GITHUB_TOKEN}."))
        self.git(self.store, "add", "--", published.path)
        self.git(self.store, "commit", "--quiet", "-m", "records")
        (self.store / "notes.txt").write_text("my notes", encoding="utf-8")
        newer = self.store / recordstore.record_path(at(EVENT, "2026-11-15T10:15:32Z", "c"))
        newer.write_text(json.dumps({**at(EVENT, "2026-11-15T10:15:32Z", "c"), "schema": "tug.task-event/2"}), encoding="utf-8")
        for path, message in (
            (published.path, "is not an unpublished record"),
            ("notes.txt", "is not an unpublished record"),
            ("../../../outside.json", "is not an unpublished record"),
            (newer.relative_to(self.store).as_posix(), "was written by a newer tug"),
        ):
            with self.subTest(path=path), self.assertRaisesRegex(TugError, message):
                recordcheck.discard(self.repo, path)
        self.assertEqual(sorted(self.files()), sorted([clean.path, published.path, "notes.txt", newer.relative_to(self.store).as_posix()]))


if __name__ == "__main__":
    unittest.main()
