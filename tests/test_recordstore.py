import json
import os
import stat
import time
import unittest
from pathlib import Path
from unittest import mock

from tugboat import paths, recordstore, records, sync
from tugboat.errors import DataError, TugError

from test_formats import changed
from test_init import InitCase
from test_records import ACCEPTANCE_REPORT, CLAIM, EVENT, HANDOVER, SUMMARY

JIRA = {"tracker": "jira", "key": "SHOP-123"}


def at(record, stamp, suffix):
    """The same record at another time, with an ID of its own."""
    compact = stamp.replace("-", "").replace(":", "").split(".")[0].rstrip("Z") + "Z"
    return changed(record, at=stamp, id=f"{compact}-{suffix * 12}")


class StoreCase(InitCase):
    """A machine that has synced the team repo. The store needs the clone and no app repo."""

    def setUp(self):
        super().setUp()
        self.assertTrue(sync.sync_team_repo(self.address).ok)
        self.repo = self.address  # every call names the team repo
        self.clone = paths.team_repo_clone(self.address)
        self.store = paths.records_worktree(self.address)

    def files(self):
        return sorted(path.relative_to(self.store).as_posix() for path in self.store.rglob("*") if path.is_file() and path.name != ".git")


class PathTests(unittest.TestCase):
    def test_every_record_of_a_story_shares_one_folder_whatever_the_tracker(self):
        self.assertEqual(recordstore.task_path(CLAIM["task"]), "github-projects/acme/shop-ios/311")
        self.assertEqual(recordstore.task_path(JIRA), "jira/SHOP-123")
        self.assertEqual(recordstore.record_path(CLAIM), "github-projects/acme/shop-ios/311/20261115T101530Z-3f9a1c2b7d4e.json")
        android = changed(EVENT, repo="acme/shop-android", platform="android", task=JIRA)
        ios = changed(CLAIM, task=JIRA)
        self.assertEqual(os.path.dirname(recordstore.record_path(android)), os.path.dirname(recordstore.record_path(ios)))


class WriteTests(StoreCase):
    def test_a_record_is_one_checked_json_file_on_the_records_branch_of_the_team_repo(self):
        stored = recordstore.write(self.repo, changed(CLAIM, at="2026-11-15T12:15:30+02:00"))
        self.assertEqual(stored.path, "github-projects/acme/shop-ios/311/20261115T101530Z-3f9a1c2b7d4e.json")
        file = self.store / stored.path
        self.assertEqual(json.loads(file.read_text(encoding="utf-8")), CLAIM)  # the checked form: the time is in UTC
        self.assertEqual(stored.record, CLAIM)
        self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o444)
        self.assertEqual(self.files(), [stored.path])
        # The folder is the team repo's `records` branch, which shares nothing with `main`.
        self.assertEqual(self.git(self.store, "symbolic-ref", "--short", "HEAD"), "records")
        self.assertEqual(self.git(self.store, "rev-parse", "--path-format=absolute", "--git-common-dir"), os.fspath(self.clone / ".git"))
        self.assertEqual(self.git(self.store, "ls-files"), "")
        self.assertEqual(self.git(self.store, "status", "--porcelain", "--untracked-files=all"), f"?? {stored.path}")
        # The clone itself, the app repo, and `main` are as they were.
        self.assertEqual(self.git(self.clone, "status", "--porcelain"), "")
        self.assertEqual(self.git(self.clone, "symbolic-ref", "--short", "HEAD"), "main")
        self.assertTrue(self.untouched(self.ios))

    def test_a_record_is_written_once_and_never_replaced(self):
        recordstore.write(self.repo, CLAIM)
        before = (self.store / recordstore.record_path(CLAIM)).read_bytes()
        for second in (CLAIM, changed(CLAIM, machine="another-machine")):
            with self.subTest(machine=second["machine"]), self.assertRaisesRegex(TugError, "is already in the store. A record is written once and never edited."):
                recordstore.write(self.repo, second)
        self.assertEqual((self.store / recordstore.record_path(CLAIM)).read_bytes(), before)
        self.assertEqual(len(self.files()), 1)

    def test_a_record_that_fails_its_schema_is_not_stored(self):
        for name, record in (("a missing field", changed(CLAIM, machine=...)), ("an extra field", changed(CLAIM, transcript="x")), ("a newer schema", changed(CLAIM, schema="tug.claim/2"))):
            with self.subTest(name=name), self.assertRaises(DataError):
                recordstore.write(self.repo, record)
        self.assertEqual(self.files() if self.store.exists() else [], [])

    def test_a_crash_during_a_write_leaves_no_half_written_record(self):
        real_link = os.link
        for name, crash_in in (("while the text is written", "os.fsync"), ("just before the record gets its name", "os.link")):
            with self.subTest(name=name):
                with mock.patch(crash_in, side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
                    recordstore.write(self.repo, CLAIM)
                self.assertEqual(self.files(), [])  # no record, and no temporary file either
                self.assertEqual(recordstore.read_all(self.repo), recordstore.Listing((), ()))

        # A crash so hard that nothing could be cleaned up: the process is gone after part of the text is on disk.
        folder = self.store / "github-projects/acme/shop-ios/311"
        folder.mkdir(parents=True, exist_ok=True)
        half = folder / (recordstore.TEMPORARY_PREFIX + "abc123.tmp")
        half.write_text('{"schema": "tug.claim/1", "id": "2026', encoding="utf-8")
        listing = recordstore.read_all(self.repo)
        self.assertEqual((listing.records, listing.unreadable), ((), ()))  # a reader does not look at it
        self.assertEqual(recordstore.unpublished(self.repo), ())  # Git does not see it either, so it can never be published
        self.assertEqual(self.git(self.store, "status", "--porcelain", "--untracked-files=all"), "")
        self.assertIs(os.link, real_link)
        stored = recordstore.write(self.repo, CLAIM)  # the same record can be written after the crash
        self.assertEqual(recordstore.read_all(self.repo).records, (stored,))
        self.assertTrue(half.exists())  # young: it could be a write that is still going on

        old = time.time() - recordstore.STALE_AFTER - 60
        os.utime(half, (old, old))
        recordstore.write(self.repo, at(EVENT, "2026-11-15T10:15:31Z", "b"))
        self.assertFalse(half.exists())  # old: the next write to the folder clears it
        self.assertEqual(len(self.files()), 2)

    def test_the_record_gets_its_name_only_when_it_is_whole(self):
        seen = []
        real_link = os.link

        def watching_link(source, target):
            seen.append((os.path.exists(target), json.loads(Path(source).read_text(encoding="utf-8"))))
            return real_link(source, target)

        with mock.patch("os.link", side_effect=watching_link):
            recordstore.write(self.repo, CLAIM)
        self.assertEqual(seen, [(False, CLAIM)])  # complete in the temporary file before the name exists

    def test_a_record_that_is_too_large_is_refused(self):
        with mock.patch.object(recordstore, "MAX_RECORD_BYTES", 200), self.assertRaisesRegex(DataError, "the record is larger than"):
            recordstore.write(self.repo, SUMMARY)
        self.assertEqual(self.files(), [])


class ReadTests(StoreCase):
    def test_records_come_back_oldest_first_by_their_own_time(self):
        late = recordstore.write(self.repo, at(EVENT, "2026-11-15T10:15:31Z", "c"))
        early = recordstore.write(self.repo, at(CLAIM, "2026-11-15T10:15:30Z", "f"))
        # As text, "30.5Z" would sort before "30Z". As a time it comes after.
        middle = recordstore.write(self.repo, at(HANDOVER, "2026-11-15T10:15:30.500000Z", "a"))
        listing = recordstore.read_all(self.repo)
        self.assertEqual([stored.path for stored in listing.records], [early.path, middle.path, late.path])
        self.assertEqual(listing.unreadable, ())

    def test_the_records_of_one_story_can_be_read_alone(self):
        mine = recordstore.write(self.repo, CLAIM)
        other = recordstore.write(self.repo, changed(SUMMARY, task=JIRA))
        self.assertEqual(recordstore.read_all(self.repo, CLAIM["task"]).records, (mine,))
        self.assertEqual(recordstore.read_all(self.repo, JIRA).records, (other,))
        self.assertEqual(recordstore.read_all(self.repo, {"tracker": "jira", "key": "SHOP-999"}), recordstore.Listing((), ()))
        self.assertEqual(len(recordstore.read_all(self.repo).records), 2)

    def test_a_reader_reports_what_it_cannot_read_and_never_guesses(self):
        good = recordstore.write(self.repo, CLAIM)
        folder = self.store / "github-projects/acme/shop-ios/311"

        def put(name, data):
            (folder / name).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")

        put("20261115T101531Z-aaaaaaaaaaaa.json", '{"schema": "tug.claim/1", "id": ')
        put("20261115T101532Z-bbbbbbbbbbbb.json", changed(EVENT, id="20261115T101532Z-bbbbbbbbbbbb", schema="tug.task-event/2", shape="unknown to this tool"))
        put("20261115T101533Z-cccccccccccc.json", changed(CLAIM, id="20261115T101533Z-cccccccccccc", machine=...))
        put("20261115T101534Z-dddddddddddd.json", changed(CLAIM, id="20261115T101534Z-eeeeeeeeeeee"))  # renamed after it was written
        put("notes.txt", "mine")
        (self.store / "jira/SHOP-123").mkdir(parents=True)
        (self.store / "jira/SHOP-123/20261115T101535Z-ffffffffffff.json").write_text(json.dumps(changed(CLAIM, id="20261115T101535Z-ffffffffffff")), encoding="utf-8")

        listing = recordstore.read_all(self.repo)
        self.assertEqual(listing.records, (good,))
        reasons = {os.path.basename(item.path): item for item in listing.unreadable}
        self.assertEqual(len(reasons), 6)
        self.assertIn("invalid JSON", reasons["20261115T101531Z-aaaaaaaaaaaa.json"].reason)
        self.assertTrue(reasons["20261115T101532Z-bbbbbbbbbbbb.json"].newer)
        self.assertIn("A newer tug wrote it", reasons["20261115T101532Z-bbbbbbbbbbbb.json"].reason)
        self.assertIn("missing fields: machine", reasons["20261115T101533Z-cccccccccccc.json"].reason)
        self.assertIn("it is not at the path its own content gives", reasons["20261115T101534Z-dddddddddddd.json"].reason)
        self.assertEqual(reasons["notes.txt"].reason, "it is not a record file")
        self.assertIn("it is not at the path its own content gives (github-projects/acme/shop-ios/311/", reasons["20261115T101535Z-ffffffffffff.json"].reason)
        self.assertEqual([item.newer for item in listing.unreadable].count(True), 1)
        for item in listing.unreadable:
            self.assertNotIn(os.fspath(self.tmp), item.reason)  # a reason names the file inside the store, not the machine's folders

    def test_a_link_is_not_followed(self):
        outside = self.tmp / "outside.json"
        outside.write_text(json.dumps(CLAIM), encoding="utf-8")
        folder = recordstore.ensure(self.repo) / "github-projects/acme/shop-ios/311"
        folder.mkdir(parents=True)
        (folder / "20261115T101530Z-3f9a1c2b7d4e.json").symlink_to(outside)
        listing = recordstore.read_all(self.repo)
        self.assertEqual(listing.records, ())
        self.assertEqual(listing.unreadable[0].reason, "it is not a plain file")

    def test_a_store_that_is_not_there_reads_as_empty_and_is_not_made_by_reading(self):
        self.assertEqual(recordstore.read_all(self.repo), recordstore.Listing((), ()))
        self.assertEqual(recordstore.unpublished(self.repo), ())
        self.assertFalse(self.store.exists())

    def test_a_very_large_store_is_read_up_to_a_limit_and_says_so(self):
        for index, suffix in enumerate("abc"):
            recordstore.write(self.repo, at(CLAIM, f"2026-11-15T10:15:3{index}Z", suffix))
        with mock.patch.object(recordstore, "MAX_FILES", 2):
            listing = recordstore.read_all(self.repo)
        self.assertEqual((len(listing.records), listing.cut_short), (2, True))
        self.assertFalse(recordstore.read_all(self.repo).cut_short)

    def test_every_kind_of_record_goes_through_the_store(self):
        made = [recordstore.write(self.repo, at(record, f"2026-11-15T10:15:3{index}Z", "abcde"[index])) for index, record in enumerate((CLAIM, EVENT, ACCEPTANCE_REPORT, SUMMARY, HANDOVER))]
        listing = recordstore.read_all(self.repo)
        self.assertEqual(listing.records, tuple(made))
        self.assertEqual([records.kind_of(stored.record) for stored in listing.records], list(records.KINDS))
        self.assertEqual(recordstore.unpublished(self.repo), tuple(sorted(stored.path for stored in made)))


class WorktreeTests(StoreCase):
    def test_a_machine_that_never_synced_is_told_to_sync(self):
        self.use_home("fresh")
        with self.assertRaisesRegex(TugError, "has no copy of the team repo .* yet. Run `tug sync` first."):
            recordstore.write(self.address, CLAIM)
        self.assertFalse(paths.records_worktree(self.address).exists())

    def test_published_and_unpublished_records_are_one_folder_and_git_tells_them_apart(self):
        published = recordstore.write(self.repo, CLAIM)
        self.git(self.store, "add", "--", published.path)
        self.git(self.store, "commit", "--quiet", "-m", "records")  # what publishing will do
        waiting = recordstore.write(self.repo, at(EVENT, "2026-11-15T10:15:31Z", "b"))
        self.assertEqual(recordstore.read_all(self.repo).records, (published, waiting))
        self.assertEqual(recordstore.unpublished(self.repo), (waiting.path,))

    def test_the_branch_is_taken_from_github_when_it_is_there_already(self):
        # Another machine published a record. This machine fetched the branch, and has no checkout of it yet.
        seed = self.tmp / "seed"
        self.git(self.tmp, "clone", "--quiet", os.fspath(self.remote), os.fspath(seed))
        self.git(seed, "checkout", "--quiet", "--orphan", "records")
        self.git(seed, "rm", "-rf", "--quiet", ".")
        path = recordstore.record_path(CLAIM)
        (seed / path).parent.mkdir(parents=True)
        (seed / path).write_text(json.dumps(CLAIM), encoding="utf-8")
        self.git(seed, "add", "-A")
        self.git(seed, "commit", "--quiet", "-m", "a record from another machine")
        self.git(seed, "push", "--quiet", "origin", "records")
        self.git(self.clone, "fetch", "--quiet", "origin", "records:refs/remotes/origin/records")

        listing = recordstore.read_all(self.repo)
        self.assertEqual(listing.records, ())  # reading makes no checkout
        mine = recordstore.write(self.repo, at(EVENT, "2026-11-15T10:15:31Z", "b"))
        listing = recordstore.read_all(self.repo)
        self.assertEqual([stored.record["id"] for stored in listing.records], [CLAIM["id"], mine.record["id"]])
        self.assertEqual(recordstore.unpublished(self.repo), (mine.path,))
        self.assertEqual(self.git(self.store, "rev-parse", "HEAD"), self.git(self.remote, "rev-parse", "records"))

    def test_a_deleted_checkout_is_made_again_and_keeps_what_was_committed(self):
        published = recordstore.write(self.repo, CLAIM)
        self.git(self.store, "add", "--", published.path)
        self.git(self.store, "commit", "--quiet", "-m", "records")
        for current, _folders, _files in os.walk(self.store):
            os.chmod(current, 0o700)
        import shutil

        shutil.rmtree(self.store)
        again = recordstore.write(self.repo, at(EVENT, "2026-11-15T10:15:31Z", "b"))
        self.assertEqual(recordstore.read_all(self.repo).records, (published, again))

    def test_an_older_git_gets_the_same_empty_branch(self):
        recordstore._add_empty_branch_old_git(self.clone, self.store)
        self.assertEqual(self.git(self.store, "symbolic-ref", "--short", "HEAD"), "records")
        self.assertEqual(self.files(), [])
        stored = recordstore.write(self.repo, CLAIM)
        self.assertEqual(recordstore.unpublished(self.repo), (stored.path,))
        self.assertEqual(self.git(self.clone, "status", "--porcelain"), "")

    def test_a_folder_of_something_else_in_the_way_is_left_alone(self):
        self.store.mkdir(parents=True)
        (self.store / "mine.txt").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(TugError, "exists and is not the task records of acme/team. It was left as it is."):
            recordstore.write(self.repo, CLAIM)
        self.assertEqual((self.store / "mine.txt").read_text(encoding="utf-8"), "x")


if __name__ == "__main__":
    unittest.main()
