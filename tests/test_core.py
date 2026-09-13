"""Acceptance coverage for durable recording and paragraph identity."""
import concurrent.futures
import multiprocessing
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from writing_memory.core import Store, split_paragraphs
from writing_memory.util import atomic_write


def _record_process(root, task_id, event_id):
    Store(Path(root)).capture(task_id, None, event_id, "process", event_id)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.store = Store(self.base / "assets")
        self.store.init()

    def tearDown(self):
        self.directory.cleanup()

    def task(self, content=None):
        document = None
        if content is not None:
            document = self.base / "draft.md"
            document.write_text(content, encoding="utf-8")
        return self.store.create_task("试点调研", "供领导判断试点", "领导", "调研", document)

    def capture(self, task, content, event="event-1", target=None):
        return self.store.capture(task["id"], content, "先写结论，再给依据", "session-1", event,
                                  target_paragraph=target)

    def test_three_edits_and_local_restore_preserve_other_paragraphs(self):
        task = self.task("背景\n\n建议研究\n\n成本未知\n")
        second_id = task["versions"][0]["paragraphs"][1]["id"]
        for number, conclusion in enumerate(("建议试点", "建议小规模试点", "建议下月开展小规模试点"), 1):
            content = f"背景\n\n{conclusion}\n\n成本未知\n"
            task = self.capture(task, content, f"edit-{number}", second_id)
            self.assertEqual(task["versions"][-1]["paragraphs"][1]["id"], second_id)
        Path(task["document_path"]).write_text(task["versions"][-1]["content"], encoding="utf-8")
        restored = self.store.restore_paragraph(task["id"], "V2", 2, second_id, "恢复 V2 第二段", "Rogers")
        self.assertEqual(len(restored["versions"]), 5)
        self.assertEqual(restored["versions"][-1]["content"], "背景\n\n建议试点\n\n成本未知\n")
        self.assertEqual(restored["versions"][-1]["restored_from"]["version_id"], "V2")
        self.assertEqual(Path(task["document_path"]).read_text(), restored["versions"][-1]["content"])
        self.assertEqual(restored["capture_result"]["before_version"], "V4")

    def test_insert_before_old_comment_does_not_drift(self):
        task = self.task("背景\n\n原结论")
        task = self.capture(task, "背景\n\n新结论", "edit", 2)
        relation = dict(task["relations"][0])
        task = self.capture(task, "摘要\n\n背景\n\n新结论", "insert")
        self.assertEqual(task["relations"][0], relation)
        self.assertEqual(task["relations"][0]["before_version"], "V1")
        self.assertEqual(task["relations"][0]["excerpt"], "原结论")
        self.assertEqual(task["versions"][-1]["paragraphs"][2]["id"], relation["after_paragraph"])

    def test_split_and_merge_are_pending_until_human_resolution(self):
        task = self.task("第一段\n\n第二段合并内容")
        task = self.capture(task, "第一段\n\n第二段\n\n拆出内容", "split", 2)
        relation = task["relations"][0]
        self.assertEqual(relation["status"], "pending")
        self.assertIsNone(relation["after_paragraph"])
        task = self.store.resolve_relation(task["id"], relation["id"], 2, "Rogers")
        self.assertEqual(task["relations"][0]["status"], "modified")
        task = self.capture(task, "第一段\n\n重新合成的第二段", "merge", 2)
        self.assertTrue(any(r["status"] == "pending" for r in task["relations"]))
        pending = next(r for r in task["relations"] if r["status"] == "pending")
        task = self.store.resolve_relation(task["id"], pending["id"], actor="Rogers")
        self.assertEqual(next(r for r in task["relations"] if r["id"] == pending["id"])["status"], "withdrawn")

    def test_repeated_identical_paragraphs_are_never_guessed(self):
        task = self.task("重复\n\n重复\n\n结尾")
        task = self.capture(task, "重复\n\n新增\n\n重复\n\n结尾")
        self.assertEqual(len(task["relations"]), 2)
        self.assertTrue(all(r["status"] == "pending" for r in task["relations"]))

    def test_retries_are_idempotent_and_changed_retry_is_rejected(self):
        task = self.task()
        task = self.capture(task, "第一稿")
        duplicate = self.capture(task, "第一稿")
        self.assertTrue(duplicate["capture_result"]["duplicate"])
        self.assertEqual(len(duplicate["turns"]), 1)
        with self.assertRaisesRegex(ValueError, "重试内容不同"):
            self.capture(task, "第二稿")
        saved = self.store.get_task(task["id"])
        self.assertEqual(len(saved["turns"]), 1)
        self.assertEqual(saved["versions"][0]["content"], "第一稿")

    def test_same_content_no_new_version_but_return_to_old_content_is_new(self):
        task = self.task()
        for content, event in (("A", "a1"), ("A", "a2"), ("B", "b"), ("A", "a3")):
            task = self.capture(task, content, event)
        self.assertEqual([v["content"] for v in task["versions"]], ["A", "B", "A"])
        self.assertEqual(len(task["turns"]), 4)

    def test_history_without_document_has_missing_status_and_no_fictional_versions(self):
        task = self.task()
        task = self.store.capture(task["id"], None, "文件已更新", "old-kimi", "history", source="import")
        self.assertEqual(task["versions"], [])
        self.assertTrue(task["capture_result"]["missing_version"])
        self.assertIsNone(task["capture_result"]["before_version"])
        self.assertIsNone(task["capture_result"]["after_version"])
        self.assertEqual(self.store.status()["missing_versions"], 1)

    def test_none_never_reads_bound_document(self):
        task = self.task("初始稿")
        Path(task["document_path"]).write_text("尚未快照的新稿", encoding="utf-8")
        task = self.capture(task, None)
        self.assertEqual(len(task["versions"]), 1)
        self.assertEqual(task["capture_result"]["version_status"], "discussion")
        self.assertEqual(self.store.status()["document_changes"][0]["status"], "changed")

    def test_initial_snapshot_and_human_acceptance(self):
        task = self.task("已有原稿")
        self.assertEqual(task["versions"][0]["source"], "initial")
        task = self.store.capture(task["id"], "已完善", "AI 回复已完善", "s", "done")
        self.assertEqual(task["versions"][-1]["accepted"], "pending")
        with self.assertRaises(ValueError):
            self.store.set_acceptance(task["id"], "V2", "accepted", "")
        task = self.store.set_acceptance(task["id"], "V2", "accepted", "Rogers")
        self.assertEqual(task["versions"][-1]["acceptance_history"][-1]["actor"], "Rogers")

    def test_git_failure_keeps_json_and_can_retry(self):
        task = self.task()
        git = self.store._git
        def fail_commit(*args, **kwargs):
            if "commit" in args:
                raise RuntimeError("模拟提交失败")
            return git(*args, **kwargs)
        with patch.object(self.store, "_git", side_effect=fail_commit):
            result = self.capture(task, "离线也不丢失")
        self.assertEqual(result["git_sync"]["status"], "pending")
        self.assertEqual(self.store.get_task(task["id"])["versions"][0]["content"], "离线也不丢失")
        self.assertEqual(len(self.store.status()["pending_commits"]), 1)
        self.assertEqual(self.store.retry_git()[0]["status"], "committed")
        self.assertEqual(self.store.status()["pending_commits"], [])

    def test_crash_after_json_save_before_git_has_visible_retry_state(self):
        task = self.task()
        with patch.object(self.store, "commit_path", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.capture(task, "已落盘但未提交")
        reopened = Store(self.store.root)
        saved = reopened.get_task(task["id"])
        self.assertEqual(saved["versions"][0]["content"], "已落盘但未提交")
        pending = reopened.status()["pending_commits"]
        self.assertEqual(len(pending), 1)
        self.assertTrue(pending[0]["recovered_from_disk"])
        reopened.retry_git()
        self.assertEqual(reopened.status()["pending_commits"], [])

    def test_git_commit_does_not_include_unrelated_staged_files(self):
        task = self.task()
        unrelated = self.store.root / "unrelated.txt"
        unrelated.write_text("用户暂存工作", encoding="utf-8")
        self.store._git("add", "--", "unrelated.txt")
        self.capture(task, "任务文稿")
        names = self.store._git("show", "--pretty=format:", "--name-only", "HEAD").stdout.splitlines()
        self.assertEqual([name for name in names if name], [f"tasks/{task['id']}.json"])
        self.assertIn("unrelated.txt", self.store._git("diff", "--cached", "--name-only").stdout)

    def test_recovery_scans_only_fixed_asset_roots_and_preserves_foreign_staging(self):
        assets = ("raw/record.bin", "sources/source.txt", "imports/import_1.json", "extractions/e/context.json",
                  "experiences/candidates/e.json", "experiences/releases/batch.json", "releases/r.json",
                  "usage/loads/u.json", "exports/e.md", "prompt_snapshots/p.md", "reviews/r/source.json")
        for relative in assets:
            atomic_write(self.store.root / relative, "saved asset")
        atomic_write(self.store.root / "outbox" / "delivery.json", "local-only")
        atomic_write(self.store.root / "extractions" / ".context.json.abcd1234", "interrupted temporary write")
        atomic_write(self.store.root / "foreign.txt", "already staged by user")
        self.store._git("add", "--", "foreign.txt")
        pending = self.store.status()["pending_commits"]
        self.assertEqual({item["path"] for item in pending}, set(assets))
        result = self.store.retry_git()
        self.assertTrue(all(item["status"] == "committed" for item in result))
        self.assertEqual(self.store.status()["pending_commits"], [])
        self.assertEqual(self.store._git("diff", "--cached", "--name-only").stdout.strip(), "foreign.txt")
        committed = set(self.store._git("ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines())
        self.assertTrue(set(assets).issubset(committed))
        self.assertNotIn("foreign.txt", committed)
        self.assertNotIn("outbox/delivery.json", committed)

    def test_init_upgrades_local_ignores_without_replacing_custom_rules(self):
        path = self.store.root / ".gitignore"
        path.write_text("custom-user-ignore\n.lock\ngit_sync/\n*.tmp\n", encoding="utf-8")
        self.store.init()
        self.assertIn("custom-user-ignore", path.read_text())
        self.assertIn("outbox/", path.read_text().splitlines())
        atomic_write(self.store.root / "outbox" / "event.json", "local state")
        self.assertEqual(self.store._git("check-ignore", "outbox/event.json").returncode, 0)
        with self.assertRaisesRegex(ValueError, "整个资产目录"):
            self.store.commit_path(self.store.root, "must never stage everything")
        with self.assertRaises(ValueError):
            self.store.commit_path(self.store.root / "outbox" / "event.json", "must stay local")

    def test_failed_bundle_commit_is_not_duplicated_by_recovery_scan(self):
        directory = self.store.root / "extractions" / "extract_1"
        atomic_write(directory / "prompt.md", "prompt")
        atomic_write(directory / "manifest.json", "{}")
        git = self.store._git
        def fail_commit(*args, **kwargs):
            if "commit" in args:
                raise RuntimeError("simulated failure")
            return git(*args, **kwargs)
        with patch.object(self.store, "_git", side_effect=fail_commit):
            self.store.commit_path(directory, "save extraction")
        self.assertEqual([p["path"] for p in self.store.status()["pending_commits"]], ["extractions/extract_1"])
        self.assertEqual(len(self.store.retry_git()), 1)

    def _restore_fixture(self):
        task = self.task("背景\n\n旧结论")
        task = self.capture(task, "背景\n\n新结论", "new", 2)
        document = Path(task["document_path"])
        document.write_text(task["versions"][-1]["content"], encoding="utf-8")
        return task, document

    def _interrupt_restore_write(self, task, document):
        def interrupted_write(path, content):
            if Path(path) == document:
                raise KeyboardInterrupt()
            return atomic_write(path, content)
        with patch("writing_memory.core.atomic_write", side_effect=interrupted_write):
            with self.assertRaises(KeyboardInterrupt):
                self.store.restore_paragraph(task["id"], "V1", 2, 2, "恢复旧结论", "Rogers")

    def test_restore_crash_before_document_write_has_durable_intent_and_retry(self):
        task, document = self._restore_fixture()
        self._interrupt_restore_write(task, document)
        saved = self.store.get_task(task["id"])
        self.assertEqual(saved["versions"][-1]["content"], "背景\n\n旧结论")
        self.assertEqual(saved["document_sync"]["status"], "pending")
        self.assertEqual(document.read_text(), "背景\n\n新结论")
        self.assertEqual(len(self.store.status()["document_sync_pending"]), 1)
        result = Store(self.store.root).retry_documents(task["id"])
        self.assertEqual(result[0]["status"], "synced")
        self.assertEqual(document.read_text(), "背景\n\n旧结论")
        self.assertEqual(len(self.store.get_task(task["id"])["versions"]), 3)
        self.assertEqual(self.store.status()["document_sync_pending"], [])

    def test_restore_crash_after_document_write_can_finalize_without_new_version(self):
        task, document = self._restore_fixture()
        save = self.store._save
        calls = 0
        def interrupted_completion(record, message):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt()
            return save(record, message)
        with patch.object(self.store, "_save", side_effect=interrupted_completion):
            with self.assertRaises(KeyboardInterrupt):
                self.store.restore_paragraph(task["id"], "V1", 2, 2, "恢复旧结论", "Rogers")
        self.assertEqual(document.read_text(), "背景\n\n旧结论")
        self.assertEqual(self.store.get_task(task["id"])["document_sync"]["status"], "pending")
        self.assertEqual(self.store.retry_documents(task["id"])[0]["status"], "synced")
        self.assertEqual(len(self.store.get_task(task["id"])["versions"]), 3)

    def test_restore_retry_preserves_intervening_external_changes_and_deletion(self):
        task, document = self._restore_fixture()
        self._interrupt_restore_write(task, document)
        document.write_text("同事的新修改", encoding="utf-8")
        result = self.store.retry_documents(task["id"])
        self.assertEqual(result[0]["status"], "conflict")
        self.assertEqual(document.read_text(), "同事的新修改")
        self.assertEqual(self.store.status()["document_sync_pending"][0]["status"], "conflict")
        document.unlink()
        self.assertEqual(self.store.retry_documents(task["id"])[0]["status"], "conflict")
        self.assertFalse(document.exists())

    def test_capture_after_interrupted_restore_marks_old_intent_superseded(self):
        task, document = self._restore_fixture()
        self._interrupt_restore_write(task, document)
        task = self.capture(task, "外部新稿", "external-new")
        self.assertEqual(task["document_sync"]["status"], "superseded")
        self.assertEqual(task["document_sync"]["version"], "V3")
        self.assertEqual(task["document_sync"]["superseded_by"], "V4")
        self.assertEqual(self.store.retry_documents(task["id"]), [])
        self.assertEqual(document.read_text(), "背景\n\n新结论")
        self.assertEqual(self.store.status()["document_sync"][0]["status"], "superseded")
        task = self.capture(task, "再次修改的外部稿", "external-next")
        self.assertEqual(task["document_sync"]["status"], "superseded")
        self.assertEqual(task["document_sync"]["version"], "V3")

    def test_initialization_refuses_outer_repository_without_touching_it(self):
        outer = self.base / "outer"
        outer.mkdir()
        subprocess.run(["git", "init", str(outer)], check=True, capture_output=True)
        before = set(path.name for path in outer.iterdir())
        with self.assertRaises(ValueError):
            Store(outer).init()
        self.assertEqual(set(path.name for path in outer.iterdir()), before)
        nested = Store(outer / "assets")
        nested.init()
        self.assertEqual(Path(nested._git("rev-parse", "--show-toplevel").stdout.strip()).resolve(), nested.root)

    def test_atomic_failure_preserves_whole_previous_task(self):
        task = self.task("初稿")
        with patch("writing_memory.core.atomic_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.capture(task, "不能完整写入的新稿")
        saved = self.store.get_task(task["id"])
        self.assertEqual(len(saved["versions"]), 1)
        self.assertEqual(saved["turns"], [])

    def test_thread_and_process_captures_do_not_lose_events(self):
        task = self.task()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(self.store.capture, task["id"], None, f"讨论{i}", "threads", f"thread-{i}") for i in range(8)]
            for future in futures:
                future.result()
        context = multiprocessing.get_context("spawn")
        processes = [context.Process(target=_record_process, args=(str(self.store.root), task["id"], f"process-{i}")) for i in range(3)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(30)
            self.assertEqual(process.exitcode, 0)
        saved = self.store.get_task(task["id"])
        self.assertEqual(len(saved["turns"]), 11)
        self.assertEqual(saved["versions"], [])

    def test_excerpt_mismatch_rejects_and_restore_protects_unrecorded_edits(self):
        task = self.task("第一段\n\n第二段")
        with self.assertRaisesRegex(ValueError, "摘录"):
            self.store.capture(task["id"], "改稿", "修改", "s", "e", target_paragraph=2, excerpt="不存在")
        Path(task["document_path"]).write_text("外部修改", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "未记录修改"):
            self.store.restore_paragraph(task["id"], "V1", 1, 2, "局部恢复", "Rogers")
        self.assertEqual(Path(task["document_path"]).read_text(), "外部修改")
        self.assertEqual(len(self.store.get_task(task["id"])["versions"]), 1)

    def test_paragraph_split_preserves_fenced_code_and_offsets(self):
        content = "前言\n\n```python\nfirst = 1\n\nsecond = 2\n```\n\n结束\n"
        paragraphs = split_paragraphs(content)
        self.assertEqual(len(paragraphs), 3)
        self.assertIn("\n\n", paragraphs[1]["text"])
        for paragraph in paragraphs:
            self.assertEqual(content[paragraph["start"]:paragraph["end"]], paragraph["text"])


if __name__ == "__main__":
    unittest.main()
