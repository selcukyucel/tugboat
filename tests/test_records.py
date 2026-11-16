import copy
import json
import unittest
from datetime import datetime, timezone

from tugboat import records
from tugboat.errors import DataError

from test_formats import Cases, changed
from test_knowledge import GITHUB_TOKEN

COMMIT = "1f" * 20
TEAM_REVISION = "2a" * 20
COMMON = {
    "id": "20261115T101530Z-3f9a1c2b7d4e",
    "at": "2026-11-15T10:15:30Z",
    "tool_version": "0.29.0",
    "task": {"tracker": "github-projects", "key": "acme/shop-ios#311"},
    "repo": "acme/shop-ios",
    "platform": "ios",
    "author": "pat-dev",
    "machine": "pats-macbook",
}
WHERE = {"branch": "feat/checkout", "commit": COMMIT}
AGENT_SESSION = {"session": "9b46f234-8e14-472d-a384-89b77064b42e", "surface": "claude-code"}
NO_SESSION = {"session": None, "surface": None}
RESULT = {"name": "unit-tests", "status": "failed", "summary": "2 of 140 tests failed", "report": "20261115T101400Z-aaaaaaaaaaaa"}

CLAIM = {"schema": "tug.claim/1", **COMMON}
EVENT = {
    "schema": "tug.task-event/1",
    **COMMON,
    **WHERE,
    **AGENT_SESSION,
    "team_repo_revision": TEAM_REVISION,
    "step": "checks-run",
    "results": [{"name": "build", "status": "passed", "summary": None, "report": None}, RESULT],
    "next_action": "Fix the two failing tests in CheckoutTests.",
    "agent": {"produced": True, "files": [{"path": "Shop/Checkout/Total.swift", "lines": 42}]},
}
REPORT_BASE = {"schema": "tug.report/1", **COMMON, **WHERE, **AGENT_SESSION, "team_repo_revision": TEAM_REVISION}
CHECKS_REPORT = {
    **REPORT_BASE,
    "report": "checks",
    "checks": [
        {"name": "build", "status": "passed", "summary": None, "seconds": 95},
        {"name": "unit-tests", "status": "failed", "summary": "CheckoutTests.testTotal: expected 12, got 10", "seconds": 61},
        {"name": "ui-tests", "status": "blocked", "summary": "No simulator named iPhone 17.", "seconds": None},
    ],
}
FINDING = {"severity": "high", "title": "Force unwrap of the session token", "detail": "The token can be nil after sign-out.", "path": "Shop/Auth/Session.swift", "line": 88, "rule": "IOS-004"}
ANALYSIS_REPORT = {**REPORT_BASE, "report": "analysis", "status": "failed", "reason": None, "findings": [FINDING]}
CRITERION = {"text": "The total shows the delivery fee on its own line.", "status": "met", "evidence": "CheckoutUITests.testFeeLine passes on this commit."}
ACCEPTANCE_REPORT = {
    **REPORT_BASE,
    "report": "acceptance",
    "source": {"kind": "task-list", "name": "Acceptance criteria"},
    "criteria": [CRITERION, {"text": "It works offline.", "status": "unverified", "evidence": None}],
    "blocked_reason": None,
}
QA_PACK = {
    **REPORT_BASE,
    "report": "qa-pack",
    "changes": ["The checkout total has a line for the delivery fee."],
    "criteria": [CRITERION],
    "risks": ["Rounding for currencies with no decimals."],
    "exploratory_tests": ["Change the address after the fee is shown."],
    "build": "2.8.9 (72)",
    "feature_flags": [{"name": "delivery_fee_line", "state": "on for the test group"}],
}
SUMMARY = {
    "schema": "tug.session-summary/1",
    **COMMON,
    **WHERE,
    **AGENT_SESSION,
    "summary": "Added the delivery fee line to the checkout total.\n\nThe fee comes from the cart response, so no new request was needed.",
    "files_touched": ["Shop/Checkout/Total.swift", "ShopTests/CheckoutTests.swift"],
    "decisions": ["Kept the fee as a separate row, not part of the subtotal."],
    "open_questions": ["Should the fee be hidden when it is zero?"],
}
HANDOVER = {
    "schema": "tug.handover/1",
    **COMMON,
    **WHERE,
    **NO_SESSION,
    "reason": "I am away from Monday.",
    "next_steps": "Two tests in CheckoutTests still fail. The rounding is the likely cause.",
}
STORY = {"title": "Show the delivery fee in the checkout total", "url": "https://github.com/acme/shop-ios/issues/311", "epic": "Checkout", "milestone": None}
STATE_RESULT = {**RESULT, "kind": "checks", "commit": COMMIT, "team_repo_revision": TEAM_REVISION, "at": "2026-11-15T10:14:00Z"}
STATE = {
    "step": "checks-run",
    "commit": COMMIT,
    "results": [STATE_RESULT],
    "pending_choice": "Fix the findings, or leave the code as it is?",
    "next_action": "Wait for the developer's choice.",
    "story": STORY,
    "claims": [{"author": "pat-dev", "machine": "pats-macbook", "at": "2026-11-15T09:00:00Z"}],
}
STARTED = changed(EVENT, step="started", story=STORY, results=[], agent={"produced": False, "files": []})
CHOICE = changed(EVENT, step="choice-made", choice={"selected": "fix-findings", "detail": "Only the force unwrap."})
CHECKPOINT = changed(EVENT, step="checkpoint", state=STATE)
EVERY = {
    "claim": CLAIM, "event": EVENT, "started event": STARTED, "choice event": CHOICE, "checkpoint event": CHECKPOINT,
    "checks report": CHECKS_REPORT, "analysis report": ANALYSIS_REPORT, "acceptance report": ACCEPTANCE_REPORT, "qa pack": QA_PACK,
    "session summary": SUMMARY, "handover note": HANDOVER,
}  # fmt: skip


def nested(base, path, value):
    """A deep copy of base with one value deep inside replaced. A value of ... removes it."""
    result = copy.deepcopy(base)
    target = result
    for key in path[:-1]:
        target = target[key]
    if value is ...:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return result


class EveryKindTests(Cases):
    def test_each_kind_validates_and_comes_back_unchanged(self):
        for name, record in EVERY.items():
            with self.subTest(name=name):
                self.assertEqual(records.validate_record(copy.deepcopy(record)), record)

    def test_a_missing_field_is_refused_in_every_kind(self):
        for name, record in EVERY.items():
            for field in record:
                if field == "schema":
                    continue
                with self.subTest(name=name, field=field), self.assertRaises(DataError) as caught:
                    records.validate_record(changed(record, **{field: ...}))
                self.assertIn(field, str(caught.exception))

    def test_an_extra_field_is_refused_in_every_kind_and_at_every_depth(self):
        for name, record in EVERY.items():
            with self.subTest(name=name), self.assertRaisesRegex(DataError, "unknown fields: transcript"):
                records.validate_record(changed(record, transcript="User: hello"))
        for name, record, path in (
            ("task", CLAIM, ("task", "title")),
            ("result", EVENT, ("results", 0, "log")),
            ("agent", EVENT, ("agent", "model")),
            ("agent file", EVENT, ("agent", "files", 0, "content")),
            ("story", STARTED, ("story", "body")),
            ("choice", CHOICE, ("choice", "by")),
            ("state", CHECKPOINT, ("state", "chat")),
            ("check", CHECKS_REPORT, ("checks", 0, "command")),
            ("finding", ANALYSIS_REPORT, ("findings", 0, "code")),
            ("criterion", ACCEPTANCE_REPORT, ("criteria", 0, "comment")),
            ("criteria source", ACCEPTANCE_REPORT, ("source", "field_id")),
            ("feature flag", QA_PACK, ("feature_flags", 0, "owner")),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(DataError, f"unknown fields: {path[-1]}"):
                records.validate_record(nested(record, path, "x"))

    def test_every_record_carries_the_tool_version_and_a_time_in_utc(self):
        checked = records.validate_record(changed(CLAIM, at="2026-11-15T12:15:30+02:00", tool_version=" 0.29.0 "))
        self.assertEqual((checked["at"], checked["tool_version"]), ("2026-11-15T10:15:30Z", "0.29.0"))
        self.refuse(
            records.validate_record,
            {
                "no timezone": (changed(CLAIM, at="2026-11-15T10:15:30"), "must include a timezone"),
                "a version that is not three numbers": (changed(CLAIM, tool_version="0.29"), "tool_version must look like 1.2.3"),
                "an ID of another shape": (changed(CLAIM, id="claim-1"), "id must look like"),
                "a path in the ID": (changed(CLAIM, id="../../etc/passwd"), "id must look like"),
                "an author that is no account name": (changed(CLAIM, author="Pat Smith"), "author must be a GitHub account name"),
                "a machine name of two lines": (changed(CLAIM, machine="a\nb"), "machine must be one line"),
                "another platform": (changed(CLAIM, platform="web"), "platform must be one of"),
                "a repo that is an address": (changed(CLAIM, repo="git@github.com:acme/shop-ios.git"), "repo must be a GitHub repository"),
            },
        )

    def test_both_trackers_give_the_same_task_shape(self):
        jira = records.validate_record(changed(CLAIM, task={"tracker": "jira", "key": "SHOP-123"}))
        github = records.validate_record(CLAIM)
        self.assertEqual(set(jira["task"]), set(github["task"]))
        self.refuse(
            records.validate_record,
            {
                "a Jira key for GitHub": (changed(CLAIM, task={"tracker": "github-projects", "key": "SHOP-123"}), "owner/name#123"),
                "a GitHub issue for Jira": (changed(CLAIM, task={"tracker": "jira", "key": "acme/shop-ios#311"}), "Jira issue key such as SHOP-123"),
                "issue number zero": (changed(CLAIM, task={"tracker": "github-projects", "key": "acme/shop-ios#0"}), "owner/name#123"),
                "another tracker": (changed(CLAIM, task={"tracker": "linear", "key": "SHOP-123"}), "tracker must be one of"),
            },
        )


class SchemaTests(Cases):
    def test_a_record_from_a_newer_tool_is_named_as_such_and_not_read(self):
        for kind in records.KINDS:
            with self.subTest(kind=kind), self.assertRaises(records.NewerSchema) as caught:
                records.validate_record({"schema": f"tug.{kind}/2", "anything": "a newer tool may have added"})
            self.assertEqual((caught.exception.kind, caught.exception.version), (kind, 2))
            self.assertIn("A newer tug wrote it", str(caught.exception))
        self.assertTrue(issubclass(records.NewerSchema, DataError))

    def test_anything_that_is_not_a_record_schema_is_refused(self):
        self.refuse(
            records.validate_record,
            {
                "no schema": (changed(CLAIM, schema=...), 'must have a "schema"'),
                "a team repo file": (changed(CLAIM, schema="tug.profile/1"), 'must have a "schema"'),
                "number zero": (changed(CLAIM, schema="tug.claim/0"), 'must have a "schema"'),
                "no number": (changed(CLAIM, schema="tug.claim"), 'must have a "schema"'),
                "not an object": (["tug.claim/1"], "record must be a JSON object"),
            },
        )

    def test_a_record_is_checked_as_the_kind_its_schema_names(self):
        with self.assertRaisesRegex(DataError, 'claim must have "schema": "tug.claim/1"'):
            records.validate_claim(changed(CLAIM, schema="tug.handover/1"))
        self.assertEqual(records.kind_of(EVENT), "task-event")
        self.assertEqual(records.schema_name(records.SUMMARY), "tug.session-summary/1")

    def test_new_ids_sort_by_time_and_do_not_repeat(self):
        early = records.new_id(datetime(2026, 11, 15, 10, 15, 30, tzinfo=timezone.utc))
        late = records.new_id(datetime(2026, 11, 15, 10, 15, 31, tzinfo=timezone.utc))
        self.assertTrue(early.startswith("20261115T101530Z-"))
        self.assertLess(early, late)
        made = {records.new_id() for _ in range(200)}
        self.assertEqual(len(made), 200)
        for made_id in (early, *made):
            records.validate_record(changed(CLAIM, id=made_id))


class EventTests(Cases):
    def test_an_event_holds_what_the_brief_and_a_resume_need(self):
        checked = records.validate_event(EVENT)
        for field in ("task", "platform", "repo", "branch", "commit", "session", "surface", "author", "at", "team_repo_revision", "step", "results", "next_action", "tool_version", "agent"):
            with self.subTest(field=field):
                self.assertIn(field, checked)

    def test_the_four_results_stay_distinct(self):
        for status in ("passed", "failed", "blocked", "untested"):
            checked = records.validate_event(nested(EVENT, ("results", 0, "status"), status))
            self.assertEqual(checked["results"][0]["status"], status)
        self.refuse(
            records.validate_event,
            {
                "a made-up status": (nested(EVENT, ("results", 0, "status"), "ok"), "status must be one of"),
                "the same check twice": (changed(EVENT, results=[RESULT, RESULT]), "lists 'unit-tests' more than once"),
                "a report that is no record ID": (nested(EVENT, ("results", 1, "report"), "report.html"), "report must look like"),
            },
        )

    def test_the_step_decides_which_extra_field_an_event_has(self):
        self.refuse(
            records.validate_event,
            {
                "a start without the story": (changed(STARTED, story=...), "must have 'story' at the step 'started'"),
                "a story at another step": (changed(EVENT, story=STORY), "must have 'story' at the step 'started', and at no other step"),
                "a choice without what was chosen": (changed(CHOICE, choice=...), "must have 'choice' at the step 'choice-made'"),
                "a checkpoint without the state": (changed(CHECKPOINT, state=...), "must have 'state' at the step 'checkpoint'"),
                "an unknown step": (changed(EVENT, step="deployed"), "step must be one of"),
                "an unknown choice": (nested(CHOICE, ("choice", "selected"), "ship-it"), "selected must be one of"),
                "a state whose step is the checkpoint itself": (nested(CHECKPOINT, ("state", "step"), "checkpoint"), "must be the step the task had reached"),
                "a saved result without the commit it was made for": (nested(CHECKPOINT, ("state", "results", 0, "commit"), ...), "missing fields: commit"),
                "a saved result of an unknown kind": (nested(CHECKPOINT, ("state", "results", 0, "kind"), "coverage"), "kind must be one of"),
                "a saved claim by no account": (nested(CHECKPOINT, ("state", "claims", 0, "author"), "Pat Smith"), "author must be a GitHub account name"),
                "a saved claim with an extra field": (nested(CHECKPOINT, ("state", "claims", 0, "team"), "ios"), "unknown fields: team"),
                "a state without the story field": (nested(CHECKPOINT, ("state", "story"), ...), "missing fields: story"),
                "a story link with a sign-in": (nested(STARTED, ("story", "url"), "https://pat:pw@example.com/x"), "https address with no sign-in"),
                "a story link that is not https": (nested(STARTED, ("story", "url"), "javascript:alert(1)"), "https address"),
            },
        )

    def test_the_position_is_a_full_commit_and_a_real_branch_name(self):
        self.assertIsNone(records.validate_event(changed(EVENT, branch=None))["branch"])  # a detached HEAD
        self.refuse(
            records.validate_event,
            {
                "a short commit": (changed(EVENT, commit="1f1f1f1"), "commit must be a full Git commit ID"),
                "a branch with dots": (changed(EVENT, branch="feat/../main"), "branch is not a valid branch name"),
                "an option as a branch": (changed(EVENT, branch="--force"), "branch is not a valid branch name"),
                "a short team repo revision": (changed(EVENT, team_repo_revision="main"), "team_repo_revision must be a full Git commit ID"),
            },
        )

    def test_agent_line_counts_belong_to_an_agent_session(self):
        by_hand = changed(EVENT, **NO_SESSION, agent={"produced": False, "files": []})
        self.assertEqual(records.validate_event(by_hand)["agent"], {"produced": False, "files": []})
        self.refuse(
            records.validate_event,
            {
                "lines counted for nobody": (changed(EVENT, agent={"produced": False, "files": [{"path": "a.swift", "lines": 3}]}), "says that no agent made the change"),
                "an agent with no surface": (changed(EVENT, **NO_SESSION), "the record names no surface"),
                "a session without its surface": (changed(EVENT, surface=None), "session and surface together"),
                "an unknown surface": (changed(EVENT, surface="xcode"), "surface must be one of"),
                "zero lines": (nested(EVENT, ("agent", "files", 0, "lines"), 0), "lines must be between 1"),
                "a path that leaves the repo": (nested(EVENT, ("agent", "files", 0, "path"), "../other/x.swift"), "must be a relative path"),
                "the same file twice": (nested(EVENT, ("agent", "files"), [{"path": "a.swift", "lines": 1}, {"path": "a.swift", "lines": 2}]), "lists 'a.swift' more than once"),
                "produced as a word": (nested(EVENT, ("agent", "produced"), "yes"), "produced must be true or false"),
            },
        )


class ReportTests(Cases):
    def test_several_checks_are_a_pass_only_when_each_one_passed(self):
        for statuses, expected in (
            (["passed", "passed"], "passed"),
            (["passed", "untested"], "untested"),
            (["passed", "blocked", "untested"], "blocked"),
            (["passed", "blocked", "failed"], "failed"),
            ([], "untested"),
        ):
            with self.subTest(statuses=statuses):
                self.assertEqual(records.overall(statuses), expected)
        statuses = [check["status"] for check in records.validate_report(CHECKS_REPORT)["checks"]]
        self.assertEqual(records.overall(statuses), "failed")

    def test_a_checks_report_needs_a_check_and_a_known_kind(self):
        self.refuse(
            records.validate_report,
            {
                "no check at all": (changed(CHECKS_REPORT, checks=[]), "checks must have between 1 and 50 items"),
                "an unknown kind of report": (changed(CHECKS_REPORT, report="coverage"), "report must be one of"),
                "no kind": (changed(CHECKS_REPORT, report=...), "report is missing fields: report"),
                "the fields of another kind": (changed(CHECKS_REPORT, findings=[]), "checks report has unknown fields: findings"),
                "a time below zero": (nested(CHECKS_REPORT, ("checks", 0, "seconds"), -1), "seconds must be between 0"),
            },
        )

    def test_an_analysis_that_did_not_run_to_the_end_cannot_read_as_clean(self):
        clean = changed(ANALYSIS_REPORT, status="passed", findings=[])
        self.assertEqual(records.validate_report(clean)["findings"], [])
        blocked = changed(ANALYSIS_REPORT, status="blocked", reason="The reviewer skill is not installed.", findings=[])
        self.assertEqual(records.validate_report(blocked)["status"], "blocked")
        self.refuse(
            records.validate_report,
            {
                "passed with findings": (changed(ANALYSIS_REPORT, status="passed"), "is 'passed' and lists findings"),
                "failed with no finding": (changed(ANALYSIS_REPORT, findings=[]), "is 'failed' and lists no finding"),
                "blocked with no reason": (changed(ANALYSIS_REPORT, status="blocked", findings=[]), "is 'blocked' and gives no reason"),
                "untested with no reason": (changed(ANALYSIS_REPORT, status="untested", findings=[]), "is 'untested' and gives no reason"),
                "a rule ID of another shape": (nested(ANALYSIS_REPORT, ("findings", 0, "rule"), "rule-4"), "must look like COM-001"),
                "line zero": (nested(ANALYSIS_REPORT, ("findings", 0, "line"), 0), "line must be between 1"),
                "an unknown severity": (nested(ANALYSIS_REPORT, ("findings", 0, "severity"), "critical"), "severity must be one of"),
            },
        )

    def test_an_acceptance_report_cannot_be_empty_and_successful(self):
        blocked = changed(ACCEPTANCE_REPORT, criteria=[], blocked_reason="The story has no acceptance criteria.")
        self.assertEqual(records.validate_report(blocked)["criteria"], [])
        for status in ("met", "not-met", "blocked", "unverified", "not-applicable"):
            criterion = {**CRITERION, "status": status}
            self.assertEqual(records.validate_report(changed(ACCEPTANCE_REPORT, criteria=[criterion]))["criteria"][0]["status"], status)
        self.refuse(
            records.validate_report,
            {
                "no criterion and no reason": (changed(ACCEPTANCE_REPORT, criteria=[]), "A story without criteria gives a blocked report"),
                "criteria and a blocked reason": (changed(ACCEPTANCE_REPORT, blocked_reason="x"), "A blocked report lists no criterion"),
                "met with no evidence": (nested(ACCEPTANCE_REPORT, ("criteria", 0, "evidence"), None), "is 'met' and names no evidence"),
                "a status of its own": (nested(ACCEPTANCE_REPORT, ("criteria", 0, "status"), "passed"), "status must be one of"),
                "a criterion with no text": (nested(ACCEPTANCE_REPORT, ("criteria", 0, "text"), " "), "text cannot be empty"),
                "an unknown source kind": (nested(ACCEPTANCE_REPORT, ("source", "kind"), "comment"), "source kind must be one of"),
            },
        )

    def test_a_qa_pack_says_what_changed(self):
        self.refuse(
            records.validate_report,
            {
                "nothing changed": (changed(QA_PACK, changes=[]), "changes must have between 1 and 50 items"),
                "a criterion met without evidence": (nested(QA_PACK, ("criteria", 0, "evidence"), None), "is 'met' and names no evidence"),
                "a risk of two lines": (changed(QA_PACK, risks=["a\nb"]), r"risks\[0\] must be one line"),
            },
        )


class SummaryAndHandoverTests(Cases):
    TRANSCRIPT = "User: add the fee line\nAssistant: Done, I added it.\nUser: now run the tests\nAssistant: They pass."

    def test_a_raw_chat_is_refused_wherever_prose_goes(self):
        self.refuse(
            records.validate_record,
            {
                "a session summary": (changed(SUMMARY, summary=self.TRANSCRIPT), "summary reads like a chat transcript"),
                "a handover reason": (changed(HANDOVER, reason=self.TRANSCRIPT), "reason reads like a chat transcript"),
                "handover next steps": (changed(HANDOVER, next_steps=self.TRANSCRIPT), "next_steps reads like a chat transcript"),
                "a finding's detail": (nested(ANALYSIS_REPORT, ("findings", 0, "detail"), self.TRANSCRIPT), "detail reads like a chat transcript"),
                "a criterion's evidence": (nested(ACCEPTANCE_REPORT, ("criteria", 0, "evidence"), self.TRANSCRIPT), "evidence reads like a chat transcript"),
            },
        )

    def test_a_session_summary_is_short_and_comes_from_an_agent_session(self):
        self.refuse(
            records.validate_summary,
            {
                "no session": (changed(SUMMARY, **NO_SESSION), "must name its session and its surface"),
                "longer than a few paragraphs": (changed(SUMMARY, summary="x" * 4001), "summary cannot exceed 4000 characters"),
                "a file outside the repo": (changed(SUMMARY, files_touched=["/etc/hosts"]), "must be a relative path"),
                "the same file twice": (changed(SUMMARY, files_touched=["a.swift", "a.swift"]), "lists 'a.swift' more than once"),
                "a decision of two lines": (changed(SUMMARY, decisions=["a\nb"]), r"decisions\[0\] must be one line"),
            },
        )

    def test_a_handover_note_says_why_and_what_next(self):
        from_agent = records.validate_handover(changed(HANDOVER, **AGENT_SESSION))
        self.assertEqual(from_agent["surface"], "claude-code")
        self.refuse(
            records.validate_handover,
            {
                "no reason": (changed(HANDOVER, reason=""), "reason cannot be empty"),
                "no next steps": (changed(HANDOVER, next_steps=None), "next_steps must be text"),
            },
        )


class TextTests(unittest.TestCase):
    def test_every_piece_of_text_in_a_record_can_be_scanned(self):
        texts = list(records.text_values(ANALYSIS_REPORT))
        for expected in ("Force unwrap of the session token", "The token can be nil after sign-out.", "Shop/Auth/Session.swift", "pat-dev", "feat/checkout"):
            with self.subTest(expected=expected):
                self.assertIn(expected, texts)
        self.assertNotIn(88, texts)
        seeded = nested(HANDOVER, ("next_steps",), f"Use the token {GITHUB_TOKEN} for the staging API.")
        records.validate_record(seeded)  # the schema passes: the check for secrets is its own step before publishing
        self.assertTrue(any(GITHUB_TOKEN in text for text in records.text_values(seeded)))

    def test_every_piece_of_text_has_the_name_of_its_field(self):
        fields = dict(records.text_fields(CHECKS_REPORT))
        self.assertEqual(fields["checks[1].summary"], "CheckoutTests.testTotal: expected 12, got 10")
        self.assertEqual(fields["task.key"], "acme/shop-ios#311")
        self.assertEqual(fields["branch"], "feat/checkout")
        self.assertNotIn("checks[0].summary", fields)  # it holds no text
        self.assertEqual(dict(records.text_fields(QA_PACK))["feature_flags[0].state"], "on for the test group")
        self.assertEqual(list(records.text_values(CHECKS_REPORT)), list(fields.values()))

    def test_a_checked_record_is_plain_json(self):
        for name, record in EVERY.items():
            with self.subTest(name=name):
                checked = records.validate_record(record)
                self.assertEqual(json.loads(json.dumps(checked)), checked)


if __name__ == "__main__":
    unittest.main()
