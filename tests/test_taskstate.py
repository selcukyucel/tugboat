import json
import unittest
from datetime import datetime, timezone

from tugboat import paths, recordstore, records, sync, taskstate
from tugboat.errors import TugError
from tugboat.recordstore import Stored

from test_formats import changed
from test_init import InitCase
from test_records import ACCEPTANCE_REPORT, ANALYSIS_REPORT, CHECKS_REPORT, CLAIM, COMMIT, EVENT, HANDOVER, STORY, SUMMARY, TEAM_REVISION

TASK = CLAIM["task"]
REPO = "acme/shop-ios"
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
OTHER_COMMIT = "3c" * 20
OTHER_REVISION = "4d" * 20


def stamp(minute, day="2026-11-15"):
    return f"{day}T10:{minute:02d}:00Z"


def made(record, minute, day="2026-11-15", **changes):
    """One of the sample records at a time of its own, checked, as the store would return it."""
    at = stamp(minute, day)
    record_id = f"{day.replace('-', '')}T10{minute:02d}00Z-{minute:012x}"
    checked = records.validate_record(changed(record, id=record_id, at=at, **changes))
    return Stored(recordstore.record_path(checked), checked)


def result(name, status="passed", report=None):
    return {"name": name, "status": status, "summary": None, "report": report.record["id"] if report else None}


def event(minute, step="checks-run", results=(), day="2026-11-15", **changes):
    return made(EVENT, minute, day, step=step, results=list(results), **changes)


def started(minute, day="2026-11-15"):
    return event(minute, "started", day=day, story=STORY, agent={"produced": False, "files": []})


def state_of(stored, **current):
    kept, _ = taskstate.within_window(stored, NOW)
    return taskstate.compute(kept, TASK, REPO, **current)


def by_name(state):
    return {item.name: item for item in state.results}


class ComputeTests(unittest.TestCase):
    def test_the_state_is_worked_out_from_the_events_and_nothing_else(self):
        checks = made(CHECKS_REPORT, 20)
        history = [
            made(CLAIM, 1),
            started(2),
            event(10, "plan-saved", next_action="Write the fee row."),
            checks,
            event(21, results=[result("build"), result("unit-tests", "failed", checks)], next_action="Fix the two failing tests.", branch="feat/fee", commit=OTHER_COMMIT),
        ]
        state = state_of(reversed(history))  # the order the files are read in does not matter
        self.assertEqual((state.task, state.repo, state.platform), (TASK, REPO, "ios"))
        self.assertEqual(state.story, STORY)
        self.assertEqual((state.step, state.next_action), ("checks-run", "Fix the two failing tests."))
        self.assertEqual((state.branch, state.commit, state.team_repo_revision), ("feat/fee", OTHER_COMMIT, TEAM_REVISION))
        self.assertEqual([(item.name, item.status, item.kind, item.commit) for item in state.results], [("build", "passed", None, OTHER_COMMIT), ("unit-tests", "failed", "checks", OTHER_COMMIT)])
        self.assertEqual([claim.author for claim in state.claims], ["pat-dev"])
        self.assertEqual((state.pending_choice, state.handover), (None, None))
        self.assertEqual(state.last_at, stamp(21))
        self.assertEqual([item.record["id"] for item in state.history], [item.record["id"] for item in history])

    def test_the_newest_result_of_a_check_counts_and_the_earlier_one_stays_as_history(self):
        first = event(10, results=[result("unit-tests", "failed"), result("build")])
        second = event(30, results=[result("unit-tests")], commit=OTHER_COMMIT)
        state = state_of([first, second])
        self.assertEqual(by_name(state)["unit-tests"].status, "passed")
        self.assertEqual(by_name(state)["unit-tests"].commit, OTHER_COMMIT)
        self.assertEqual(by_name(state)["build"].commit, COMMIT)  # not run again, so it is still the earlier one
        self.assertEqual(state.history, (first, second))

    def test_the_four_results_stay_distinct(self):
        state = state_of([event(10, results=[result("a", "passed"), result("b", "failed"), result("c", "blocked"), result("d", "untested")])])
        self.assertEqual([item.status for item in state.results], ["passed", "failed", "blocked", "untested"])

    def test_another_app_repo_of_the_same_story_has_a_state_of_its_own(self):
        android = made(EVENT, 30, repo="acme/shop-android", platform="android", step="analysis-done", results=[result("lint", "failed")])
        records_of_both = [started(2), event(10, results=[result("build")]), android]
        ios = state_of(records_of_both)
        self.assertEqual((ios.step, [item.name for item in ios.results]), ("checks-run", ["build"]))
        other = taskstate.compute(records_of_both, TASK, "acme/shop-android")
        self.assertEqual((other.platform, other.step, [item.name for item in other.results], other.story), ("android", "analysis-done", ["lint"], None))
        self.assertIsNone(taskstate.compute(records_of_both, {"tracker": "jira", "key": "SHOP-1"}, REPO))

    def test_a_task_that_was_only_claimed_has_no_step_and_no_checkpoint_to_save(self):
        state = state_of([made(CLAIM, 1)])
        self.assertEqual((state.step, state.commit, state.next_action, state.results), (None, None, None, ()))
        self.assertEqual(len(state.claims), 1)
        with self.assertRaisesRegex(TugError, "has no event yet"):
            taskstate.checkpoint_state(state)


class StaleTests(unittest.TestCase):
    def setUp(self):
        self.checks = made(CHECKS_REPORT, 20)
        self.analysis = made(ANALYSIS_REPORT, 22)
        self.acceptance = made(ACCEPTANCE_REPORT, 24)
        self.criteria = [item["text"] for item in ACCEPTANCE_REPORT["criteria"]]
        self.records = [
            self.checks, self.analysis, self.acceptance,
            event(21, results=[result("unit-tests", "passed", self.checks)]),
            event(23, "analysis-done", results=[result("analysis", "failed", self.analysis)]),
            event(25, "acceptance-reported", results=[result("acceptance", "blocked", self.acceptance)]),
        ]  # fmt: skip

    def stale(self, **current):
        return {name: item.stale for name, item in by_name(state_of(self.records, **current)).items()}

    def test_a_changed_commit_marks_every_earlier_result_stale(self):
        self.assertEqual(self.stale(commit=COMMIT), {"unit-tests": (), "analysis": (), "acceptance": ()})
        self.assertEqual(set(self.stale(commit=OTHER_COMMIT).values()), {("the commit changed",)})
        state = state_of(self.records, commit=OTHER_COMMIT)
        self.assertFalse(any(item.current for item in state.results))
        self.assertEqual([item.status for item in state.results], ["passed", "failed", "blocked"])  # what it said is kept
        self.assertEqual(len(state.history), 6)  # and nothing leaves the history

    def test_only_what_is_given_is_judged(self):
        self.assertEqual(set(self.stale().values()), {()})

    def test_a_result_made_after_the_change_is_current_again(self):
        again = event(40, results=[result("unit-tests")], commit=OTHER_COMMIT)
        self.records.append(again)
        self.assertEqual(self.stale(commit=OTHER_COMMIT), {"unit-tests": (), "analysis": ("the commit changed",), "acceptance": ("the commit changed",)})

    def test_changed_rules_mark_the_analysis_stale_and_nothing_else(self):
        asked = []

        def answer(value):
            def rules_changed(old, new):
                asked.append((old, new))
                return value

            return rules_changed

        self.assertEqual(self.stale(team_repo_revision=OTHER_REVISION, rules_changed=answer(True)), {"unit-tests": (), "analysis": ("the team's rules changed",), "acceptance": ()})
        self.assertEqual(asked, [(TEAM_REVISION, OTHER_REVISION)])
        self.assertEqual(self.stale(team_repo_revision=OTHER_REVISION, rules_changed=answer(False))["analysis"], ())
        # What cannot be worked out is not assumed to be fine.
        unknown = "the team repo changed, and whether its rules changed could not be worked out"
        self.assertEqual(self.stale(team_repo_revision=OTHER_REVISION, rules_changed=answer(None))["analysis"], (unknown,))
        self.assertEqual(self.stale(team_repo_revision=OTHER_REVISION)["analysis"], (unknown,))
        asked.clear()
        self.assertEqual(self.stale(team_repo_revision=TEAM_REVISION, rules_changed=answer(True))["analysis"], ())
        self.assertEqual(asked, [])  # the same revision needs no question

    def test_a_changed_criterion_marks_the_acceptance_result_stale_and_nothing_else(self):
        self.assertEqual(self.stale(criteria=self.criteria), {"unit-tests": (), "analysis": (), "acceptance": ()})
        reordered = [f"  {text}  " for text in reversed(self.criteria)]
        self.assertEqual(self.stale(criteria=reordered)["acceptance"], ())  # the same criteria in another order
        for name, now in (
            ("reworded", [self.criteria[0], "It works offline, also on a slow network."]),
            ("one added", [*self.criteria, "It is logged."]),
            ("one removed", self.criteria[:1]),
        ):
            with self.subTest(name=name):
                self.assertEqual(self.stale(criteria=now), {"unit-tests": (), "analysis": (), "acceptance": ("an acceptance criterion changed",)})

    def test_an_acceptance_result_whose_report_is_gone_cannot_be_compared(self):
        self.records.remove(self.acceptance)
        state = state_of(self.records, criteria=self.criteria)
        self.assertEqual(by_name(state)["acceptance"].kind, None)  # with no report, nothing says it is an acceptance result
        saved = taskstate.checkpoint_state(state_of([*self.records, self.acceptance]))
        only_checkpoint = [event(50, "checkpoint", state=saved)]
        again = by_name(state_of(only_checkpoint, criteria=self.criteria))["acceptance"]
        self.assertEqual((again.kind, again.stale), ("acceptance", ("its report is no longer among the records, so its criteria could not be compared",)))

    def test_several_reasons_are_all_named(self):
        reasons = self.stale(commit=OTHER_COMMIT, team_repo_revision=OTHER_REVISION, rules_changed=lambda old, new: True, criteria=["Something else."])
        self.assertEqual(reasons["analysis"], ("the commit changed", "the team's rules changed"))
        self.assertEqual(reasons["acceptance"], ("the commit changed", "an acceptance criterion changed"))
        self.assertEqual(reasons["unit-tests"], ("the commit changed",))


class WindowTests(unittest.TestCase):
    def test_six_months_are_counted_on_the_calendar(self):
        for now, expected in (
            (datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc), "2026-05-15T12:00:00+00:00"),
            (datetime(2026, 3, 10, 8, 30, tzinfo=timezone.utc), "2025-09-10T08:30:00+00:00"),
            (datetime(2026, 8, 31, 0, 0, tzinfo=timezone.utc), "2026-02-28T00:00:00+00:00"),  # February has no 31st
            (datetime(2024, 8, 30, 0, 0, tzinfo=timezone.utc), "2024-02-29T00:00:00+00:00"),  # a leap year
        ):
            with self.subTest(now=now):
                self.assertEqual(taskstate.cutoff(now).isoformat(), expected)

    def test_a_record_from_seven_months_ago_appears_nowhere(self):
        old_report = made(CHECKS_REPORT, 5, "2026-04-14")
        old = [
            made(CLAIM, 1, "2026-04-14", author="someone-else", machine="an-old-machine"),
            started(2, "2026-04-14"),
            old_report,
            event(6, results=[result("old-check", "failed", old_report)], day="2026-04-14", next_action="An old next action."),
            made(SUMMARY, 7, "2026-04-14", summary="An old summary."),
        ]
        recent = [made(CLAIM, 1), event(10, results=[result("build")])]
        kept, expired = taskstate.within_window([*old, *recent], NOW)
        self.assertEqual((len(kept), expired), (2, 5))
        state = taskstate.compute(kept, TASK, REPO)
        self.assertEqual([item.name for item in state.results], ["build"])
        self.assertIsNone(state.story)  # the story was named only by a record that has expired
        self.assertEqual([claim.author for claim in state.claims], ["pat-dev"])
        everything = json.dumps([state._asdict() | {"history": [item.record for item in state.history]}], default=list)
        for gone in ("2026-04-14", "old-check", "An old next action.", "An old summary.", "someone-else", "an-old-machine"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, everything)

    def test_the_record_s_own_time_decides_and_the_edge_is_kept(self):
        edge = made(CLAIM, 1, "2026-05-15")
        on_the_edge = Stored(edge.path, {**edge.record, "at": "2026-05-15T12:00:00Z"})
        just_before = Stored(edge.path, {**edge.record, "at": "2026-05-15T11:59:59Z", "id": "20260515T115959Z-aaaaaaaaaaaa"})
        kept, expired = taskstate.within_window([on_the_edge, just_before], NOW)
        self.assertEqual(([item.record["at"] for item in kept], expired), (["2026-05-15T12:00:00Z"], 1))

    def test_only_expired_records_give_no_state_at_all(self):
        kept, expired = taskstate.within_window([started(2, "2026-01-10"), event(3, day="2026-01-10")], NOW)
        self.assertEqual((kept, expired), ([], 2))
        self.assertIsNone(taskstate.compute(kept, TASK, REPO))


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.report = made(CHECKS_REPORT, 5, "2026-04-14")
        self.old = [
            made(CLAIM, 1, "2026-04-14"),
            started(2, "2026-04-14"),
            self.report,
            event(6, results=[result("unit-tests", "failed", self.report), result("build")], day="2026-04-14", next_action="Fix the tests."),
        ]

    def checkpoint(self, minute=1, day="2026-10-01", pending="Fix the findings, or leave the code as it is?"):
        """A checkpoint saved while the early records were still in the window."""
        before = taskstate.compute(self.old, TASK, REPO)
        saved = {**taskstate.checkpoint_state(before), "pending_choice": pending}
        return event(minute, "checkpoint", day=day, state=saved, next_action="Wait for the developer's choice.")

    def test_a_task_with_an_expired_first_event_and_a_recent_checkpoint_still_resumes(self):
        checkpoint = self.checkpoint()
        kept, expired = taskstate.within_window([*self.old, checkpoint], NOW)
        self.assertEqual((kept, expired), ([checkpoint], 4))
        state = taskstate.compute(kept, TASK, REPO, commit=COMMIT)
        self.assertEqual(state.story, STORY)
        self.assertEqual((state.step, state.next_action), ("checks-run", "Wait for the developer's choice."))
        self.assertEqual(state.pending_choice, "Fix the findings, or leave the code as it is?")
        self.assertEqual([(item.name, item.status, item.kind, item.at, item.stale) for item in state.results], [("build", "passed", None, stamp(6, "2026-04-14"), ()), ("unit-tests", "failed", "checks", stamp(6, "2026-04-14"), ())])
        self.assertEqual([(claim.author, claim.machine) for claim in state.claims], [("pat-dev", "pats-macbook")])
        self.assertEqual(state.history, (checkpoint,))
        # The saved results still know what they were made for.
        self.assertEqual({item.stale for item in taskstate.compute(kept, TASK, REPO, commit=OTHER_COMMIT).results}, {("the commit changed",)})

    def test_what_a_checkpoint_saves_is_a_valid_event_and_reads_back_the_same(self):
        before = taskstate.compute(self.old, TASK, REPO)
        checkpoint = event(1, "checkpoint", day="2026-10-01", state=taskstate.checkpoint_state(before), next_action=before.next_action)
        after = taskstate.compute([checkpoint], TASK, REPO)
        for field in ("story", "step", "results", "pending_choice", "next_action", "claims"):
            with self.subTest(field=field):
                self.assertEqual(getattr(after, field), getattr(before, field))

    def test_events_after_the_checkpoint_are_applied_on_top_of_it(self):
        checkpoint = self.checkpoint()
        later = [
            event(5, "choice-made", day="2026-10-02", choice={"selected": "fix-findings", "detail": None}, next_action="Fix the tests."),
            event(9, results=[result("unit-tests")], day="2026-10-02", commit=OTHER_COMMIT, next_action="Run the analysis."),
        ]
        state = state_of([*self.old, checkpoint, *later], commit=OTHER_COMMIT)
        self.assertEqual((state.step, state.next_action, state.pending_choice), ("checks-run", "Run the analysis.", None))
        self.assertEqual({name: (item.status, item.stale) for name, item in by_name(state).items()}, {"unit-tests": ("passed", ()), "build": ("passed", ("the commit changed",))})
        self.assertEqual(state.story, STORY)

    def test_the_newest_checkpoint_is_the_base_and_events_before_it_are_history_only(self):
        early = event(3, results=[result("lint", "failed")], day="2026-09-30")
        first = self.checkpoint(1, "2026-10-01", pending="An earlier question?")
        second = self.checkpoint(1, "2026-10-05", pending=None)
        state = state_of([early, first, second])
        self.assertIsNone(state.pending_choice)
        self.assertNotIn("lint", by_name(state))  # the checkpoint is the full state at its time
        self.assertEqual(state.history, (early, first, second))


class ClaimTests(unittest.TestCase):
    def test_a_second_claim_is_shown_beside_the_first_and_locks_nothing(self):
        state = state_of([made(CLAIM, 1), made(CLAIM, 5, author="sam-dev", machine="sams-mac")])
        self.assertEqual([(claim.author, claim.machine, claim.at) for claim in state.claims], [("pat-dev", "pats-macbook", stamp(1)), ("sam-dev", "sams-mac", stamp(5))])

    def test_the_same_developer_on_the_same_machine_holds_it_once(self):
        state = state_of([made(CLAIM, 1), made(CLAIM, 9)])
        self.assertEqual([claim.at for claim in state.claims], [stamp(9)])
        other_machine = state_of([made(CLAIM, 1), made(CLAIM, 9, machine="pats-mini")])
        self.assertEqual(len(other_machine.claims), 2)

    def test_a_handover_note_releases_its_author_s_claim_and_no_one_else_s(self):
        note = made(HANDOVER, 20)
        state = state_of([made(CLAIM, 1), made(CLAIM, 5, author="sam-dev", machine="sams-mac"), event(10), note])
        self.assertEqual([claim.author for claim in state.claims], ["sam-dev"])
        self.assertEqual(state.handover, note.record)
        self.assertEqual((state.step, state.next_action), ("checks-run", EVENT["next_action"]))  # the work itself is where it was

    def test_a_task_that_was_taken_up_again_is_no_longer_released(self):
        state = state_of([made(CLAIM, 1), made(HANDOVER, 20), made(CLAIM, 30, author="sam-dev", machine="sams-mac"), event(31, "resumed", author="sam-dev", machine="sams-mac")])
        self.assertIsNone(state.handover)
        self.assertEqual([claim.author for claim in state.claims], ["sam-dev"])
        self.assertEqual(state.step, "resumed")
        self.assertEqual(len(state.history), 4)  # the note stays in the history

    def test_a_claim_saved_in_a_checkpoint_outlives_its_own_record(self):
        old_claim = made(CLAIM, 1, "2026-04-14")
        saved = taskstate.checkpoint_state(taskstate.compute([old_claim, event(2, day="2026-04-14")], TASK, REPO))
        checkpoint = event(1, "checkpoint", day="2026-10-01", state=saved)
        state = state_of([old_claim, checkpoint])
        self.assertEqual([(claim.author, claim.at) for claim in state.claims], [("pat-dev", stamp(1, "2026-04-14"))])
        released = state_of([old_claim, checkpoint, made(HANDOVER, 5, "2026-10-02")])
        self.assertEqual(released.claims, ())


class LoadTests(InitCase):
    """Through the real store, and with the real team repo for the question about its rules."""

    def setUp(self):
        super().setUp()
        self.assertTrue(sync.sync_team_repo(self.address).ok)
        self.clone = paths.team_repo_clone(self.address)

    def revision(self):
        return self.git(self.clone, "rev-parse", "refs/remotes/origin/main")

    def merge_into_team_repo(self, path, text):
        (self.work / path).parent.mkdir(parents=True, exist_ok=True)
        (self.work / path).write_text(text, encoding="utf-8")
        self.git(self.work, "add", "-A")
        self.git(self.work, "commit", "--quiet", "-m", f"change {path}")
        self.git(self.work, "push", "--quiet", "origin", "HEAD:main")
        self.assertTrue(sync.sync_team_repo(self.address).ok)
        return self.revision()

    def test_published_and_unpublished_records_give_one_state(self):
        store = recordstore.ensure(self.address)
        published = recordstore.write(self.address, started(2).record)
        self.git(store, "add", "-A")
        self.git(store, "commit", "--quiet", "-m", "records")
        recordstore.write(self.address, event(10, results=[result("build")]).record)
        loaded = taskstate.load(self.address, TASK, REPO, now=NOW)
        self.assertEqual((loaded.state.step, loaded.state.story, loaded.expired), ("checks-run", STORY, 0))
        self.assertEqual(loaded.state.history[0], published)
        self.assertEqual((loaded.newer, loaded.unreadable), ((), ()))
        self.assertIsNone(taskstate.load(self.address, {"tracker": "jira", "key": "SHOP-9"}, REPO, now=NOW).state)

    def test_a_record_from_a_newer_tool_is_skipped_and_named(self):
        recordstore.write(self.address, event(10, results=[result("build")]).record)
        newer = changed(event(20, results=[result("build", "failed")]).record, schema="tug.task-event/2", shape="unknown to this tool")
        folder = recordstore.folder(self.address) / recordstore.task_path(TASK)
        (folder / f"{newer['id']}.json").write_text(json.dumps(newer), encoding="utf-8")
        (folder / "broken.json").write_text("{", encoding="utf-8")
        loaded = taskstate.load(self.address, TASK, REPO, now=NOW)
        self.assertEqual(by_name(loaded.state)["build"].status, "passed")  # the newer record was not guessed at
        self.assertEqual([item.path.rsplit("/", 1)[1] for item in loaded.newer], [f"{newer['id']}.json"])
        self.assertIn("A newer tug wrote it", loaded.newer[0].reason)
        self.assertEqual([item.path.rsplit("/", 1)[1] for item in loaded.unreadable], ["broken.json"])

    def test_expired_records_in_the_store_are_counted_and_not_used(self):
        recordstore.write(self.address, started(2, "2026-01-10").record)
        recordstore.write(self.address, event(10, results=[result("build")]).record)
        loaded = taskstate.load(self.address, TASK, REPO, now=NOW)
        self.assertEqual((loaded.expired, loaded.state.story, len(loaded.state.history)), (1, None, 1))

    def test_the_team_repo_itself_says_whether_its_rules_changed(self):
        first = self.revision()
        after_note = self.merge_into_team_repo("knowledge/checkout/fees.md", "---\ntitle: Fees\n---\nA note.\n")
        after_rule = self.merge_into_team_repo("policies/ios/IOS-777.md", "---\nid: IOS-777\n---\nA rule.\n")
        rules_changed = taskstate.rules_changed_in(self.clone)
        self.assertIs(rules_changed(first, after_note), False)  # a new knowledge note is no change of the rules
        self.assertIs(rules_changed(first, after_rule), True)
        self.assertIs(rules_changed(after_rule, after_rule), False)
        self.assertIsNone(rules_changed("9" * 40, after_rule))  # a revision this clone does not have

        report = made(ANALYSIS_REPORT, 20, team_repo_revision=first)
        recordstore.write(self.address, report.record)
        recordstore.write(self.address, event(21, "analysis-done", results=[result("analysis", "failed", report)], team_repo_revision=first).record)

        def stale(now_revision):
            loaded = taskstate.load(self.address, TASK, REPO, now=NOW, team_repo_revision=now_revision, rules_changed=rules_changed)
            return by_name(loaded.state)["analysis"].stale

        self.assertEqual(stale(after_note), ())
        self.assertEqual(stale(after_rule), ("the team's rules changed",))


if __name__ == "__main__":
    unittest.main()
