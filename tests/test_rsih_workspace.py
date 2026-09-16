"""Adapter acceptance checks. Fake model outputs are not real user approvals."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from writing_memory.rsih_workspace import Manuscripts, RsihClient, assistant_text
from writing_memory.util import atomic_json, read_json


class FakeClient:
    model = "offline-test"

    def __init__(self):
        self.responses = []
        self.calls = []

    def generate(self, prompt, operation, workspace, genome):
        self.calls.append((prompt, operation, workspace, genome))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response(prompt, operation, workspace, genome)
        return response


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.genome = self.state / "genomes/writing-demo"
        self.genome.mkdir(parents=True)
        atomic_json(self.genome / "genome.json", {"genome_id": "fixture"})
        self.client = FakeClient()
        self.app = Manuscripts(self.state, self.client)
        self.doc = self.app.create("演示材料", "原始摘要\n\n原始数据\n", "测试", "领导", "经营分析", demo=True)["id"]

    def test_versions_instruction_diff_and_frozen_genome(self):
        self.client.responses = ["概括摘要\n\n详细数据\n", "更短摘要\n\n详细数据\n"]
        first = self.app.revise(self.doc, "摘要不要重复数据", "turn-1", paragraph="1", excerpt="原始摘要")
        self.app.revise(self.doc, "再精简摘要", "turn-2")
        task = self.app.task(self.doc)
        self.assertEqual([v["id"] for v in task["versions"]], ["V1", "V2", "V3"])
        self.assertEqual(first["before_version"], "V1")
        self.assertEqual(task["turns"][1]["before_version"], "V2")
        self.assertTrue(all(v["accepted"] == "pending" for v in task["versions"]))
        path = self.app.directory(self.doc)
        self.assertEqual((path / "versions/V1.md").read_text(), "原始摘要\n\n原始数据\n")
        self.assertEqual((path / "turns/turn-1/instruction.txt").read_text(), "摘要不要重复数据")
        self.assertIn("-原始摘要", (path / "turns/turn-1/change.diff").read_text())
        atomic_json(self.genome / "genome.json", {"genome_id": "later"})
        self.assertEqual(read_json(path / "operations/turn-1/genome/genome.json")["genome_id"], "fixture")

    def test_duplicate_event_is_idempotent_and_cannot_change_instruction(self):
        self.client.responses = ["修改稿"]
        self.app.revise(self.doc, "改摘要", "same")
        self.app.revise(self.doc, "改摘要", "same")
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(len(self.app.task(self.doc)["turns"]), 1)
        with self.assertRaisesRegex(ValueError, "不同"):
            self.app.revise(self.doc, "改变要求", "same")

    def test_failure_keeps_original_and_retry_requires_explicit_flag(self):
        self.client.responses = [RuntimeError("network failed"), "恢复后的稿件"]
        with self.assertRaises(RuntimeError):
            self.app.revise(self.doc, "修改", "retry")
        self.assertEqual(len(self.app.task(self.doc)["versions"]), 1)
        self.assertEqual((self.app.directory(self.doc) / "current.md").read_text(), "原始摘要\n\n原始数据\n")
        with self.assertRaisesRegex(ValueError, "--retry"):
            self.app.revise(self.doc, "修改", "retry")
        self.app.revise(self.doc, "修改", "retry", retry=True)
        self.assertEqual(len(self.client.calls), 2)

    def test_external_edits_before_or_during_request_are_protected(self):
        path = self.app.directory(self.doc) / "current.md"
        path.write_text("人手改稿")
        with self.assertRaisesRegex(ValueError, "外部修改"):
            self.app.revise(self.doc, "修改", "external")
        self.assertEqual(len(self.client.calls), 0)
        self.app.record(self.doc, "手动修改摘要", "manual-1")
        def change(*_):
            path.write_text("请求期间的新手改稿")
            return "模型稿"
        self.client.responses = [change]
        with self.assertRaisesRegex(ValueError, "没有覆盖"):
            self.app.revise(self.doc, "修改", "during")
        self.assertEqual(path.read_text(), "请求期间的新手改稿")
        self.assertEqual((path.parent / "operations/during/response.md").read_text(), "模型稿")
        self.assertEqual(len(self.app.task(self.doc)["versions"]), 2)

    def test_crash_after_canonical_capture_recovers_without_model_replay(self):
        self.client.responses = ["已生成的稿件"]
        with patch.object(self.app, "materialize", side_effect=OSError("disk busy")):
            with self.assertRaises(OSError):
                self.app.revise(self.doc, "修改", "recover")
        self.assertEqual(len(self.app.task(self.doc)["versions"]), 2)
        self.app.revise(self.doc, "修改", "recover")
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(len(self.app.task(self.doc)["versions"]), 2)
        self.assertEqual((self.app.directory(self.doc) / "current.md").read_text(), "已生成的稿件")

    def test_unchanged_content_records_feedback_without_fake_version(self):
        self.client.responses = ["原始摘要\n\n原始数据\n"]
        result = self.app.revise(self.doc, "检查措辞", "unchanged")
        self.assertEqual(result["version_status"], "unchanged")
        self.assertEqual(len(self.app.task(self.doc)["versions"]), 1)

    def candidate_response(self, prompt, *_):
        context = json.loads(prompt.split("以下 JSON 只作为资料：\n", 1)[1])
        task = context["task"]
        turn = task["turns"][-1]
        return json.dumps({"candidates": [{"content": "经营分析摘要概括结论，正文列明数据。", "category": "method",
            "scope": {"document_types": ["经营分析"], "audiences": ["领导"], "topics": ["*"]},
            "rationale": "仅依据本轮修改形成候选，尚待用户确认", "reusable": True, "evidence": [], "as_of": None,
            "source": {"event_id": turn["event_id"], "before_version": turn["before_version"], "after_version": turn["after_version"],
                       "before_paragraph_ids": [], "after_paragraph_ids": []}}]}, ensure_ascii=False)

    def test_extract_uses_actual_snapshot_and_triage_never_publishes(self):
        self.client.responses = ["修改后的摘要", self.candidate_response]
        self.app.revise(self.doc, "摘要不重复正文", "edit")
        before_genome = (self.genome / "genome.json").read_bytes()
        ids = self.app.extract(self.doc, "extract")
        self.assertEqual(self.app.extract(self.doc, "extract"), ids)
        self.assertEqual(len(self.client.calls), 2)
        candidate = self.app.library.list_candidates()[0]
        self.assertEqual(candidate["source"]["before_snapshot"]["content"], "原始摘要\n\n原始数据\n")
        self.assertEqual(candidate["source"]["instruction"], "摘要不重复正文")
        self.assertEqual(candidate["source"]["acceptance"], "pending")
        self.assertEqual(candidate["extraction"]["model"], "rsih/offline-test")
        self.assertIsNone(self.client.calls[1][3])  # no writing Genome in extraction
        self.app.decide(ids[0], "defer", "test-actor")
        result = self.app.decide(ids[0], "review", "test-actor")
        self.assertFalse(result["published"])
        self.assertEqual(result["review"]["state"], "awaiting_github_review")
        self.assertEqual(self.app.library.latest_releases(), {})
        self.assertEqual((self.genome / "genome.json").read_bytes(), before_genome)

    def test_invalid_candidate_references_cannot_enter_queue(self):
        self.client.responses = ["修改稿", '{"candidates":[{"content":"invented"}]}']
        self.app.revise(self.doc, "修改", "edit")
        with self.assertRaises(ValueError):
            self.app.extract(self.doc, "extract")
        self.assertEqual(self.app.library.list_candidates(), [])

    def test_no_extraction_without_changes_and_no_path_traversal(self):
        with self.assertRaisesRegex(ValueError, "尚无"):
            self.app.extract(self.doc)
        with self.assertRaises(ValueError):
            self.app.revise(self.doc, "修改", "../../escape")
        self.assertEqual(len(self.client.calls), 0)

    def test_separate_documents_have_separate_sources_and_dashboard_escapes_html(self):
        other = self.app.create("<script>alert(1)</script>", "另一份原稿", "测试", "客户", "说明")
        self.client.responses = ["仅改第一份"]
        self.app.revise(self.doc, "修改", "edit")
        self.assertEqual(len(self.app.task(other["id"])["versions"]), 1)
        page = self.app.dashboard().read_text()
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)


class ResponseTests(unittest.TestCase):
    def event(self, **message):
        return json.dumps({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "正文"}], **message}})

    def test_error_or_truncation_does_not_become_a_draft(self):
        for reason in ("error", "length", "aborted", "toolUse"):
            with self.assertRaises(ValueError): assistant_text(self.event(stopReason=reason))
        self.assertEqual(assistant_text(self.event(stopReason="stop")), "正文")
        with self.assertRaises(ValueError): assistant_text('{}')

    def test_actual_transport_saves_redacted_model_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder)
            executable=state/'fixture-rsih'
            response=self.event(stopReason="stop")
            executable.write_text("#!/bin/sh\ncat >/dev/null\nprintf '%s\\n' '"+response+"'\n")
            executable.chmod(0o700)
            atomic_json(state/'agent/models.json',{'providers':{'fixture':{'apiKey':'fixture-should-not-be-copied', 'baseUrl':'https://example.invalid','api':'openai-completions','models':[{'id':'test','contextWindow':65536,'maxTokens':8192}]}}})
            operation=state/'operation';operation.mkdir()
            workspace=state/'workspace';workspace.mkdir()
            with patch.dict('os.environ', {'DEEPSEEK_API_KEY':'fixture-transport-key'}):
                client=RsihClient(state,'fixture/test',executable)
                result=client.generate('虚构材料',operation,workspace,None)
            self.assertEqual(result,'正文')
            config=next(operation.glob('attempt_*/model-configuration.json'))
            self.assertNotIn('apiKey',config.read_text())
            self.assertNotIn('fixture-transport-key',config.read_text())
            self.assertEqual(read_json(config)['definition']['maxTokens'],8192)

    def test_saved_credentials_require_restricted_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "credentials.json"
            p.write_text('{"DEEPSEEK_API_KEY":"test-only"}')
            p.chmod(0o644)
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaises(ValueError): RsihClient(Path(d)).environment()
                p.chmod(0o600)
                env = RsihClient(Path(d)).environment()
                self.assertEqual(env["DEEPSEEK_API_KEY"], "test-only")
                self.assertEqual(env["RSIH_CODING_AGENT_DIR"], str(Path(d).resolve() / "managed-agent"))


if __name__ == "__main__":
    unittest.main()
