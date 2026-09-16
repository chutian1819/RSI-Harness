"""Offline contract tests; fixture confirmations are not real user evidence."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from writing_memory.workbench import Workbench
from writing_memory.web import create_app
from writing_memory.jobs import Jobs
from writing_memory.util import atomic_json, digest, read_json


class FakeClient:
    model = "offline-fixture"

    def __init__(self):
        self.responses = []
        self.calls = []
        self.validations = []

    def validate_genome(self, path):
        self.validations.append(path)

    def generate(self, prompt, operation, workspace, genome):
        self.calls.append({"prompt": prompt, "genome": genome})
        if not self.responses:
            raise RuntimeError("fixture has no response")
        value = self.responses.pop(0)
        if callable(value):
            return value(prompt)
        if isinstance(value, Exception):
            raise value
        return value


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        shutil.copytree(Path(__file__).resolve().parents[1] / "writing_memory/templates/writing-genome", self.state / "genomes/writing-demo")
        self.client = FakeClient()
        self.app = Workbench(self.state, self.client)
        self.doc = self.app.create("虚构分析", "# 摘要\n\n收入120万元\n\n# 数据\n\n收入120万元", "练习", "领导", "经营分析")["id"]

    def draft(self, text="精简摘要\n\n数据120万元", event="draft1", instruction="摘要不要重复正文", keep=True):
        before = self.app.task(self.doc)["versions"][-1]
        self.app.prepare_draft(self.doc, instruction, event, before["content_hash"], keep)
        self.client.responses.append(text)
        return self.app.generate_draft(self.doc, event)

    def imported(self, text="经营分析摘要避免重复正文", doc_type="经营分析", audience="领导"):
        bundle = {"format": "rsih-personal-rules", "schema_version": 1, "rules": [{"content": text, "category": "preference", "scope": {"document_types": [doc_type], "audiences": [audience], "topics": ["*"]}}]}
        return self.app.memory.import_bundle(bundle)[0]

    def test_three_revisions_keep_exact_sources_and_do_not_adopt_early(self):
        for i in range(3):
            before = self.app.task(self.doc)["versions"][-1]
            draft = self.draft(f"第{i+1}次修改\n\n收入120万元", f"draft{i}", f"原始指令 {i}")
            self.assertEqual(self.app.task(self.doc)["versions"][-1], before)
            self.assertEqual(draft["context"]["base_version"], before["id"])
            result = self.app.adopt(self.doc, draft["id"], "fixture-user")
            self.assertEqual(result["version"], f"V{i+2}")
        task = self.app.task(self.doc)
        self.assertEqual(len(task["versions"]), 4)
        self.assertEqual([t["instruction"] for t in task["turns"]], [f"原始指令 {i}" for i in range(3)])
        self.assertEqual(len(task["draft_proposals"][-1]["context"]["history"]), 2)
        self.assertEqual(len(task["learning_due"]), 3)
        self.assertEqual(len(self.client.validations), 3)

    def test_reject_retains_draft_and_original(self):
        draft = self.draft()
        self.app.reject_draft(self.doc, draft["id"], "fixture")
        task = self.app.task(self.doc)
        self.assertEqual(len(task["versions"]), 1)
        self.assertEqual(task["draft_proposals"][0]["state"], "rejected")
        with self.assertRaises(ValueError): self.app.adopt(self.doc, draft["id"], "fixture")

    def test_adopt_and_generation_idempotence(self):
        draft = self.draft()
        self.app.generate_draft(self.doc, draft["id"])
        self.app.adopt(self.doc, draft["id"], "fixture")
        self.app.adopt(self.doc, draft["id"], "fixture")
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(len(self.app.task(self.doc)["versions"]), 2)
        self.assertEqual(len(self.app.task(self.doc)["versions"][-1]["acceptance_history"]), 1)

    def test_stale_draft_does_not_overwrite_manual_changes(self):
        draft = self.draft()
        before = self.app.task(self.doc)["versions"][-1]
        self.app.save_manual(self.doc, "人工更新", "手工修正", "manual1", before["content_hash"], "fixture")
        with self.assertRaisesRegex(ValueError, "旧版"):
            self.app.adopt(self.doc, draft["id"], "fixture")
        self.assertTrue(self.app.detail(self.doc)["draft_proposals"][0]["stale"])
        self.assertEqual((self.app.directory(self.doc) / "current.md").read_text(), "人工更新")

    def test_failure_then_explicit_retry_keeps_original(self):
        before = self.app.task(self.doc)["versions"][-1]
        self.app.prepare_draft(self.doc, "修改", "failure", before["content_hash"])
        self.client.responses = [TimeoutError("timed out"), "新稿"]
        with self.assertRaises(TimeoutError): self.app.generate_draft(self.doc, "failure")
        with self.assertRaisesRegex(ValueError, "重试"): self.app.generate_draft(self.doc, "failure")
        self.app.generate_draft(self.doc, "failure", True)
        self.assertEqual(len(self.app.task(self.doc)["versions"]), 1)
        self.assertEqual(len(self.client.calls), 2)

    def test_adoption_crash_is_recoverable_without_model_call(self):
        draft = self.draft()
        with patch.object(self.app, "materialize", side_effect=OSError("disk busy")):
            with self.assertRaises(OSError): self.app.adopt(self.doc, draft["id"], "fixture")
        result = self.app.adopt(self.doc, draft["id"], "fixture")
        task = self.app.task(self.doc)
        self.assertEqual(result["version"], "V2")
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(task["versions"][-1]["accepted"], "accepted")
        self.assertIn("adopt_draft1", task["learning_due"])
        self.assertEqual(task["web_writeback"]["state"], "completed")

    def test_restore_generates_new_version_without_deleting_history(self):
        self.draft(); self.app.adopt(self.doc, "draft1", "fixture")
        task = self.app.task(self.doc)
        self.app.save_manual(self.doc, task["versions"][0]["content"], "恢复 V1", "restore", task["versions"][-1]["content_hash"], "fixture", "V1")
        task = self.app.task(self.doc)
        self.assertEqual(task["versions"][-1]["id"], "V3")
        self.assertEqual(task["versions"][-1]["content"], task["versions"][0]["content"])
        self.assertNotIn("restore", task["learning_due"])

    def test_external_edit_conflict_keeps_both(self):
        draft = self.draft()
        (self.app.directory(self.doc) / "current.md").write_text("外部修改")
        with self.assertRaises(ValueError): self.app.adopt(self.doc, draft["id"], "fixture")
        self.assertEqual(len(self.app.task(self.doc)["versions"]), 1)
        self.assertTrue(self.app.detail(self.doc)["external_change"])

    def test_temporary_requirements_are_archived_but_not_reused(self):
        self.draft(instruction="本次只写一段", keep=False)
        self.app.adopt(self.doc, "draft1", "fixture")
        second = self.draft(event="draft2")
        self.assertEqual(second["context"]["history"], [])
        self.assertEqual(self.app.task(self.doc)["turns"][0]["instruction"], "本次只写一段")
        self.app.set_requirement(self.doc, "adopt_draft1", True)
        third = self.draft(event="draft3")
        self.assertEqual(third["context"]["history"][0]["event_id"], "adopt_draft1")

    def test_budget_failure_does_not_call_model_or_delete_history(self):
        atomic_json(self.state / "writing-settings.json", {"max_context_bytes": 20})
        with self.assertRaisesRegex(ValueError, "上下文"):
            self.draft()
        self.assertEqual(self.client.calls, [])
        self.assertEqual(len(self.app.task(self.doc)["versions"]), 1)

    def test_personal_approval_projection_scope_revoke_and_no_team_release(self):
        candidate_id = self.imported()
        self.assertEqual(self.app.memory.matching(self.app.task(self.doc)), [])
        rule = self.app.memory.decide(candidate_id, "approve", "fixture")
        draft = self.draft()
        self.assertEqual(draft["context"]["loaded_rules"][0]["id"], rule["id"])
        genome = read_json(self.app.directory(self.doc) / "operations/draft1/genome/components/instructions.json")
        self.assertIn(rule["content"], genome["config"]["append_system_prompt"])
        other = {**self.app.task(self.doc), "audience": "客户"}
        self.assertEqual(self.app.memory.matching(other), [])
        self.app.memory.revoke(rule["id"], "fixture")
        draft2 = self.draft(event="draft2")
        self.assertEqual(draft2["context"]["loaded_rules"], [])
        self.assertEqual(self.app.task(self.doc)["draft_proposals"][0]["context"]["loaded_rules"][0]["revision"], 1)
        self.assertEqual(self.app.library.latest_releases(), {})

    def test_rule_revoked_while_queued_blocks_new_request(self):
        rule = self.app.memory.decide(self.imported(), "approve", "fixture")
        self.app.prepare_draft(self.doc, "改稿", "queued", self.app.task(self.doc)["versions"][-1]["content_hash"])
        self.app.memory.revoke(rule["id"], "fixture")
        with self.assertRaisesRegex(ValueError, "撤销"): self.app.generate_draft(self.doc, "queued")
        self.assertFalse(self.client.calls)

    def test_overlaps_require_review_and_replace_is_atomic(self):
        first = self.app.memory.decide(self.imported("摘要不列数字"), "approve", "fixture")
        second_id = self.imported("摘要列关键数字")
        with self.assertRaisesRegex(ValueError, "重叠"): self.app.memory.decide(second_id, "approve", "fixture")
        second = self.app.memory.decide(second_id, "approve", "fixture", replace_ids=[first["id"]])
        self.assertEqual([r["id"] for r in self.app.memory.matching(self.app.task(self.doc))], [second["id"]])
        self.assertEqual(self.app.memory.rules()[0]["authority"], "personal_confirmation")

    def test_export_allowlist_and_import_pending(self):
        rule = self.app.memory.decide(self.imported(), "approve", "fixture")
        package = self.app.memory.export([rule["id"]])
        self.assertEqual(set(package["rules"][0]), {"content", "category", "scope"})
        encoded = json.dumps(package)
        self.assertNotIn("candidate_snapshot", encoded)
        self.assertNotIn("fixture", encoded)
        # Repeated imports are idempotent, never a route to approval.
        new_bundle = copy.deepcopy(package); new_bundle["rules"][0]["content"] = "正文先陈述结论"
        ids = self.app.memory.import_bundle(new_bundle)
        self.assertEqual(ids, self.app.memory.import_bundle(new_bundle))
        candidate = next(c for c in self.app.memory.candidates() if c["id"] == ids[0])
        self.assertIsNone(candidate["personal_decision"])

    def test_explicit_global_scope_is_allowed_only_at_confirmation(self):
        candidate = self.imported()
        all_scope = {k:["*"] for k in ("document_types", "audiences", "topics")}
        self.app.memory.decide(candidate, "approve", "fixture", scope=all_scope)
        self.assertEqual(len(self.app.memory.matching({"document_type":"讲话稿","audience":"员工","topic":"会议"})), 1)

    def test_local_candidate_defaults_to_source_document_and_audience(self):
        candidate={"id":"scope-fixture","content":"先写结论","category":"method","scope":{"document_types":["经营分析"],"audiences":["*"],"topics":["*"]},"reusable":True,"source":{"task_context":{"document_type":"经营分析","audience":"领导"}}}
        atomic_json(self.app.store.root/'experiences/candidates/scope-fixture.json',candidate)
        rule=self.app.memory.decide(candidate['id'],'approve','fixture')
        self.assertEqual(rule['scope']['audiences'],['领导'])
        self.assertFalse(self.app.memory.matching({'document_type':'经营分析','audience':'客户','topic':''}))

    def test_rule_revision_keeps_previous_text(self):
        candidate = self.imported()
        first = self.app.memory.decide(candidate, "approve", "fixture")
        updated = self.app.memory.decide(candidate, "approve", "fixture", content="摘要应写判断，正文说明数据")
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["history"][0]["content"], first["content"])

    def test_extraction_uses_adopted_round_and_validated_source(self):
        self.draft(); self.app.adopt(self.doc, "draft1", "fixture")
        def extracted(prompt):
            data=json.loads(prompt.split("以下 JSON 只作为资料：\n",1)[1]); task=data["task"]
            self.assertNotIn("draft_proposals", task)
            turn=task["turns"][0]
            self.assertEqual(task["versions"][-1]["accepted"],"accepted")
            return json.dumps({"candidates":[{"content":"摘要概括结论，不重复正文数据。","category":"preference","scope":{"document_types":["经营分析"],"audiences":["领导"],"topics":["*"]},"rationale":"来自本轮指令和实际采用稿","reusable":True,"source":{"event_id":turn["event_id"],"before_version":turn["before_version"],"after_version":turn["after_version"],"before_paragraph_ids":[],"after_paragraph_ids":[]}}]},ensure_ascii=False)
        self.client.responses=[extracted]
        ids=self.app.extract_once(self.doc,"learn_adopt_draft1")["candidate_ids"]
        candidate=self.app.memory.candidates()[0]
        self.assertEqual(candidate["source"]["instruction"],"摘要不要重复正文")
        self.assertIsNone(candidate["personal_decision"])
        self.app.memory.decide(ids[0],"approve","fixture")
        self.assertEqual(len(self.app.memory.matching(self.app.task(self.doc))),1)

    def test_interrupted_job_does_not_replay_model(self):
        root=self.state/"web-jobs";root.mkdir()
        atomic_json(root/"unfinished.json",{"id":"unfinished","kind":"draft","document_id":self.doc,"payload":{},"state":"running","created_at":"2026-09-16","attempt":1})
        jobs=Jobs(self.app)
        try:
            self.assertEqual(jobs.list()[0]["state"],"interrupted")
            self.assertEqual(self.client.calls,[])
        finally:jobs.close()

    def test_automatic_extraction_failure_does_not_undo_adoption(self):
        self.draft();self.app.adopt(self.doc,"draft1","fixture")
        jobs=Jobs(self.app)
        jobs.schedule_learning(self.doc)
        jobs.futures['learn_adopt_draft1'].result(timeout=15)
        self.assertEqual(jobs.list()[0]["state"],"failed")
        self.assertEqual(self.app.task(self.doc)["versions"][-1]["id"],"V2")
        jobs.schedule_learning(self.doc)
        self.assertEqual(jobs.list()[0]["attempt"],1)
        jobs.close()

    def test_frozen_prompt_tamper_blocks_call(self):
        self.app.prepare_draft(self.doc, "修改", "tamper", self.app.task(self.doc)["versions"][-1]["content_hash"])
        (self.app.directory(self.doc)/"operations/tamper/prompt.txt").write_text("被外部改写")
        with self.assertRaisesRegex(ValueError, "快照被修改"):
            self.app.generate_draft(self.doc, "tamper")
        self.assertEqual(self.client.calls, [])

    def test_manual_without_reason_is_idempotent(self):
        before=self.app.task(self.doc)["versions"][-1]
        first=self.app.save_manual(self.doc,"人工更新","","manual-empty",before["content_hash"],"fixture")
        second=self.app.save_manual(self.doc,"人工更新","","manual-empty",before["content_hash"],"fixture")
        self.assertEqual(first, second)
        self.assertEqual(len(self.app.task(self.doc)["versions"]), 2)

    def test_diff_preserves_distinct_last_lines_without_newline(self):
        from writing_memory.util import text_diff
        output=text_diff("旧结尾", "新结尾")
        self.assertIn("-旧结尾\n", output)
        self.assertIn("+新结尾\n", output)
        self.assertNotIn("旧结尾+", output)
        self.assertIn("No newline at end of file", output)

    def test_prepare_does_not_overwrite_base_genome(self):
        genome=self.state/"genomes/writing-demo/components/instructions.json"
        before=genome.read_bytes()
        self.app.memory.decide(self.imported(),"approve","fixture")
        self.draft()
        self.assertEqual(genome.read_bytes(),before)

    def test_changed_input_cannot_reuse_request_id(self):
        before=self.app.task(self.doc)["versions"][-1]
        self.app.prepare_draft(self.doc,"第一条","fixed",before["content_hash"])
        with self.assertRaisesRegex(ValueError,"不能"):
            self.app.prepare_draft(self.doc,"第二条","fixed",before["content_hash"])

    def test_empty_model_reply_never_becomes_a_draft(self):
        before=self.app.task(self.doc)["versions"][-1]
        self.app.prepare_draft(self.doc,"修改","empty",before["content_hash"])
        self.client.responses=["  "]
        with self.assertRaisesRegex(ValueError,"空内容"):
            self.app.generate_draft(self.doc,"empty")
        self.assertNotIn("draft_proposals",self.app.task(self.doc))

    def test_web_auth_origin_host_validation_and_static_safety(self):
        application=create_app(self.state,token="fixture-token",client=self.client)
        with TestClient(application,base_url="http://127.0.0.1:8765") as browser:
            self.assertEqual(browser.get('/').status_code,200)
            self.assertNotIn('fixture-token',browser.get('/').text)
            self.assertEqual(browser.get('/api/bootstrap').status_code,401)
            headers={"x-workbench-token":"fixture-token"}
            self.assertEqual(browser.get('/api/bootstrap',headers=headers).status_code,200)
            self.assertEqual(browser.get('/api/bootstrap',headers={**headers,"origin":"https://evil.example"}).status_code,403)
            self.assertEqual(browser.get('/api/bootstrap',headers={**headers,"host":"evil.example"}).status_code,403)
            self.assertEqual(browser.get('/assets/missing.js').status_code,404)
            self.assertEqual(browser.post('/api/documents',headers=headers,json={"title":"invalid"}).status_code,422)
            doc=browser.get('/api/documents/'+self.doc,headers=headers).json()
            self.assertEqual(doc['document_id'],self.doc)
            self.assertIn("frame-ancestors 'none'",browser.get('/').headers['content-security-policy'])


if __name__=='__main__':unittest.main()
