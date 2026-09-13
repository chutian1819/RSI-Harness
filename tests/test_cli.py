import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from writing_memory.cli import main
from writing_memory.core import Store
from writing_memory.delivery import recover, turn_event_id
from writing_memory.integrations import Outbox


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "assets"
        self.document = Path(self.directory.name) / "document.md"
        self.document.write_text("背景。\n\n原始结论。\n", encoding="utf-8")
        self.call("init")

    def call(self, *arguments, expected=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["--root", str(self.root), *map(str, arguments)])
        self.assertEqual(code, expected, stderr.getvalue())
        return json.loads(stdout.getvalue() if code == 0 else stderr.getvalue())

    def start(self):
        return self.call("start", "--title", "试点", "--purpose", "判断试点路径", "--audience", "领导",
                         "--document-type", "决策方案", "--document", self.document)

    def test_full_local_revision_and_restore_workflow(self):
        task = self.start()
        paragraph = task["versions"][0]["paragraphs"][1]["id"]
        self.document.write_text("背景。\n\n建议先开展小范围试点。\n", encoding="utf-8")
        saved = self.call("capture", task["id"], "--instruction", "这段先说建议。", "--session", "s1", "--event", "turn-1",
                          "--target-version", "V1", "--target-paragraph", paragraph, "--excerpt", "原始结论。")
        self.assertEqual(len(saved["versions"]), 2)
        accepted = self.call("accept", task["id"], "V2", "--actor", "测试使用者")
        self.assertEqual(accepted["versions"][-1]["accepted"], "accepted")
        restored = self.call("restore", task["id"], "--from-version", "V1", "--from-paragraph", paragraph,
                             "--current-paragraph", paragraph, "--instruction", "恢复旧段。", "--actor", "测试使用者")
        self.assertEqual(restored["versions"][-1]["id"], "V3")
        self.assertEqual(self.document.read_text(encoding="utf-8"), restored["versions"][-1]["content"])

    def test_recover_rebuilds_queue_after_save_before_enqueue_crash(self):
        task = self.start()
        Store(self.root).capture(task["id"], "修改后的实际文稿", "先结论", "s1", "e1")
        first = self.call("recover")
        self.assertEqual(first["turns_reconciled"], 1)
        jobs_before = list((self.root / "outbox").glob("*.json"))
        self.assertTrue(jobs_before)
        self.call("recover")
        self.assertEqual(jobs_before, list((self.root / "outbox").glob("*.json")))

    def test_same_turn_number_in_different_tasks_has_separate_delivery(self):
        first, second = self.start(), self.start()
        for task in (first, second):
            self.call("capture", task["id"], "--no-document", "--instruction", "讨论", "--session", "s1", "--event", "turn-1")
        self.assertNotEqual(turn_event_id(first["id"], "turn-1"), turn_event_id(second["id"], "turn-1"))

    def test_capture_conflicting_document_flags_returns_structured_error(self):
        task = self.start()
        result = self.call("capture", task["id"], "--no-document", "--document", self.document,
                           "--instruction", "讨论", "--session", "s", "--event", "e", expected=1)
        self.assertIn("不能同时", result["error"])

    def test_local_extraction_and_empty_export_do_not_publish_a_rule(self):
        task = self.start()
        extraction = self.call("extract", task["id"])
        self.assertTrue(Path(extraction["prompt_path"]).is_file())
        exported = self.call("export", task["id"])
        self.assertFalse(exported["loaded"])
        self.assertEqual(exported["entries"], [])

    def test_history_import_recovers_messages_after_original_is_deleted(self):
        task = self.start()
        original = Path(self.directory.name) / "old-chat.md"
        original.write_text("## user\n请修改旧稿。\n## assistant\n已更新文件。", encoding="utf-8")
        report = self.call("import", task["id"], original, "--source", "kimi")
        self.assertTrue(report["history_versions_missing"])
        original.unlink()
        for path in (self.root / "outbox").glob("*.json"):
            path.unlink()
        result = self.call("recover")
        self.assertGreaterEqual(result["imports_reconciled"], 1)

    def test_wrong_first_command_does_not_poison_initialization(self):
        uninitialized = Path(self.directory.name) / "new-assets"
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["--root", str(uninitialized), "export", "task_nonexistent"])
        self.assertEqual(code, 1)
        self.assertFalse(uninitialized.exists())
        self.assertTrue(Store(uninitialized).init()["initialized"])


if __name__ == "__main__":
    unittest.main()
