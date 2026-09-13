import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from writing_memory.core import Store
from writing_memory.integrations import Outbox, SourceImporter, run_stop_hook, trace_id
from writing_memory.util import atomic_json, digest, read_json


def jsonl(*records):
    return b"".join((json.dumps(record, ensure_ascii=False) + "\n").encode() for record in records)


def record(kind, payload):
    return {"timestamp": "2026-09-08T01:00:00Z", "type": kind, "payload": payload}


def turn(number, instruction="先写结论，再写依据"):
    return [record("event_msg", {"type": "task_started", "turn_id": "turn-" + str(number)}),
            record("event_msg", {"type": "user_message", "message": instruction}),
            record("response_item", {"type": "function_call", "name": "apply_patch", "call_id": "c1", "arguments": "history data only"}),
            record("event_msg", {"type": "task_complete", "turn_id": "turn-" + str(number)})]


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        self.root = self.project / "assets"
        self.store = Store(self.root)
        self.store.init()
        self.task = self.store.create_task("测试", "决策", "团队", "报告")
        self.importer = SourceImporter(self.root)
        self.session = "session-test"
        self.transcript = self.project / "explicit.jsonl"
        self.header = record("session_meta", {"id": self.session, "cwd": str(self.project)})

    def tearDown(self):
        self.temp.cleanup()

    def test_text_roles_order_exact_bytes_missing_history_and_no_fabrication(self):
        raw = "无标记开头\r\n## 用户\r\n请改第二段。\r\n## assistant\r\n已更新文件。\r\n".encode()
        path = self.project / "history.md"
        path.write_bytes(raw)
        report = self.importer.import_file(self.task["id"], path, source="kimi")
        self.assertEqual([m["role"] for m in report["messages"]], ["unknown", "user", "assistant"])
        self.assertEqual((self.root / report["raw_source"]["path"]).read_bytes(), raw)
        self.assertTrue(report["history_versions_missing"])
        self.assertEqual(self.store.get_task(self.task["id"])["versions"], [])
        self.assertEqual(len(self.store.get_task(self.task["id"])["turns"]), 1)
        duplicate = self.importer.import_file(self.task["id"], path, source="kimi")
        self.assertEqual(report["id"], duplicate["id"])
        self.assertEqual(Outbox(self.root).status()["total"], 3)
        self.assertIn(report["raw_source"]["path"], self.store._git("ls-files").stdout)

    def test_role_word_inside_code_block_is_not_interpreted(self):
        messages = self.importer.parse_text("## user\n示例：\n```md\n## assistant\n```\n仍是用户。\n")
        self.assertEqual(len(messages), 1)
        self.assertIn("## assistant", messages[0]["content"])

    def test_final_document_snapshot_once_and_long_content_preserved(self):
        path = self.project / "history.txt"
        path.write_text("[user]\n先写短。\n[assistant]\n已改。\n[user]\n再展开。\n[assistant]\n已改。\n", encoding="utf-8")
        doc = self.project / "draft.md"
        doc.write_text("结论" * 25000, encoding="utf-8")
        report = self.importer.import_file(self.task["id"], path, document_path=doc)
        task = self.store.get_task(self.task["id"])
        self.assertEqual(len(task["versions"]), 1)
        self.assertEqual(task["versions"][0]["content"], "结论" * 25000)
        self.assertEqual(len(report["messages"]), 4)
        self.assertTrue(report["history_versions_missing"])

    def test_incremental_partial_tail_restart_and_duplicate(self):
        prefix = jsonl(self.header, *turn(1)[:-1])
        tail = jsonl(turn(1)[-1])
        self.transcript.write_bytes(prefix + tail[:20])
        first = self.importer.import_codex(self.task["id"], self.transcript, self.session)
        self.assertEqual(first["offset"], len(prefix))
        self.assertFalse(first["completed"])
        self.assertEqual(first["status"], "pending_tail")
        self.assertEqual((self.root / first["raw_source"]["path"]).read_bytes(), prefix + tail[:20])
        self.transcript.write_bytes(prefix + tail)
        second = SourceImporter(self.root).import_codex(self.task["id"], self.transcript, self.session)
        self.assertTrue(second["completed"])
        self.assertEqual(second["offset"], len(prefix + tail))
        duplicate = self.importer.import_codex(self.task["id"], self.transcript, self.session)
        self.assertEqual(len(second["messages"]), len(duplicate["messages"]))
        self.assertEqual(second["messages"][2]["event_id"], first["messages"][2]["event_id"])
        self.assertEqual(duplicate["messages"][3]["record"]["payload"]["name"], "apply_patch")
        self.assertEqual(duplicate["messages"][3]["raw_line"].encode(), jsonl(turn(1)[2]))

    def test_session_binding_rewrite_and_read_close_failures_are_persisted(self):
        self.transcript.write_bytes(jsonl(self.header))
        self.importer.import_codex(self.task["id"], self.transcript, self.session)
        with self.assertRaises(ValueError):
            self.importer.import_codex(self.task["id"], self.transcript, "another-session")
        old = self.transcript.read_bytes()
        self.transcript.write_bytes(old.replace(b"session-test", b"session-else"))
        with self.assertRaises(ValueError):
            self.importer.import_codex(self.task["id"], self.transcript, self.session)
        states = [read_json(path) for path in (self.root / "imports").glob("codex_*.json")]
        self.assertTrue(all(state["status"] == "error" for state in states))
        self.transcript.write_bytes(old)
        with patch.object(Path, "read_bytes", side_effect=OSError("simulated close error")):
            with self.assertRaises(OSError):
                self.importer.import_codex(self.task["id"], self.transcript, self.session)
        states = [read_json(path) for path in (self.root / "imports").glob("codex_*.json")]
        state = next(state for state in states if state["session_id"] == self.session)
        self.assertEqual(state["error"], "OSError")
        self.assertEqual(state["offset"], len(old))
        self.assertEqual((self.root / state["raw_sources"][0]["path"]).read_bytes(), old)

    def test_live_arms_before_writing_and_captures_exact_instruction(self):
        doc = self.project / "draft.md"
        doc.write_text("原稿。", encoding="utf-8")
        self.transcript.write_bytes(jsonl(self.header))
        armed = self.importer.import_codex(self.task["id"], self.transcript, self.session, doc, self.project, live=True)
        self.assertEqual(armed["live_status"], "armed")
        doc.write_text("结论先行。", encoding="utf-8")
        complete = jsonl(turn(1)[-1])
        self.transcript.write_bytes(jsonl(self.header, *turn(1)[:-1]) + complete[:-5])
        pending = self.importer.import_codex(self.task["id"], self.transcript, self.session, doc, self.project, live=True)
        self.assertEqual(pending["live_status"], "pending_completion")
        self.assertEqual(len(self.store.get_task(self.task["id"])["versions"]), 1)
        self.transcript.write_bytes(jsonl(self.header, *turn(1)))
        captured = self.importer.import_codex(self.task["id"], self.transcript, self.session, doc, self.project, live=True)
        self.assertEqual(captured["live_status"], "captured")
        task = self.store.get_task(self.task["id"])
        self.assertEqual(len(task["versions"]), 2)
        self.assertEqual(task["turns"][-1]["instruction"], "先写结论，再写依据")
        again = self.importer.import_codex(self.task["id"], self.transcript, self.session, doc, self.project, live=True)
        self.assertEqual(again["live_status"], "unchanged")
        self.assertEqual(len(self.store.get_task(self.task["id"])["turns"]), 2)

    def test_live_gap_does_not_pair_multiple_turns_with_current_document(self):
        doc = self.project / "draft.md"
        doc.write_text("初稿", encoding="utf-8")
        self.transcript.write_bytes(jsonl(self.header))
        self.importer.import_codex(self.task["id"], self.transcript, self.session, doc, live=True)
        self.transcript.write_bytes(jsonl(self.header, *turn(1), *turn(2)))
        doc.write_text("最终观察到的文稿", encoding="utf-8")
        result = self.importer.import_codex(self.task["id"], self.transcript, self.session, doc, live=True)
        self.assertEqual(result["live_status"], "unobserved_intermediate_versions")
        self.assertEqual(result["live_gaps"][0]["unobserved_intermediate_versions"], 1)
        task = self.store.get_task(self.task["id"])
        self.assertEqual(len(task["versions"]), 2)
        self.assertEqual(task["turns"][-1]["source"], "codex-gap")

    def test_stop_before_marker_preserves_that_turn_document_for_later_completion(self):
        doc = self.project / "draft.md"
        doc.write_text("基线", encoding="utf-8")
        self.transcript.write_bytes(jsonl(self.header))
        self.importer.import_codex(self.task["id"], self.transcript, self.session, doc, live=True)
        first_turn = turn(1)
        # Current desktop may expose user input only in response_item.
        first_turn[1] = record("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "只改第一段"}]})
        self.transcript.write_bytes(jsonl(self.header, *first_turn[:-1]))
        doc.write_text("第一轮真实文稿", encoding="utf-8")
        pending = self.importer.import_codex(self.task["id"], self.transcript, self.session, doc, live=True, stop_turn_id="turn-1")
        self.assertEqual(pending["live_status"], "pending_completion")
        self.assertFalse(pending["completed"])
        # Next turn already edits working file, so finalizing turn 1 must use
        # its independently archived Stop snapshot, not today's working file.
        self.transcript.write_bytes(jsonl(self.header, *first_turn, *turn(2)[:-1]))
        doc.write_text("第二轮真实文稿", encoding="utf-8")
        self.importer.import_codex(self.task["id"], self.transcript, self.session, doc, live=True, stop_turn_id="turn-2")
        task = self.store.get_task(self.task["id"])
        self.assertEqual(task["versions"][-1]["content"], "第一轮真实文稿")
        self.assertEqual(task["turns"][-1]["instruction"], "只改第一段")
        self.transcript.write_bytes(jsonl(self.header, *first_turn, *turn(2)))
        result = self.importer.import_codex(self.task["id"], self.transcript, self.session, doc, live=True)
        self.assertEqual(result["live_status"], "captured")
        self.assertEqual(self.store.get_task(self.task["id"])["versions"][-1]["content"], "第二轮真实文稿")

    def test_crash_between_source_save_and_capture_recovers_without_original(self):
        source = self.project / "source.md"
        doc = self.project / "draft.md"
        source.write_text("## user\n请缩写。", encoding="utf-8")
        doc.write_text("归档文稿", encoding="utf-8")
        with patch.object(self.importer, "_capture", side_effect=RuntimeError("simulated crash")):
            with self.assertRaises(RuntimeError):
                self.importer.import_file(self.task["id"], source, document_path=doc)
        source.unlink()
        doc.unlink()
        result = SourceImporter(self.root).recover_saved()
        self.assertEqual(len(result["recovered_import_ids"]), 1)
        self.assertEqual(self.store.get_task(self.task["id"])["versions"][0]["content"], "归档文稿")
        self.assertEqual(result["network_requests"], 0)

    def test_codex_baseline_capture_failure_recovers_archived_document(self):
        self.transcript.write_bytes(jsonl(self.header))
        doc = self.project / "draft.md"
        doc.write_text("原始基线", encoding="utf-8")
        with patch.object(Store, "capture", side_effect=OSError("disk write failure")):
            with self.assertRaises(OSError):
                self.importer.import_codex(self.task["id"], self.transcript, self.session, doc, live=True)
        self.transcript.unlink()
        doc.unlink()
        result = SourceImporter(self.root).recover_saved()
        self.assertEqual(len(result["recovered_import_ids"]), 1)
        self.assertEqual(self.store.get_task(self.task["id"])["versions"][0]["content"], "原始基线")

    def test_hook_requires_all_explicit_bindings_and_never_syncs(self):
        self.transcript.write_bytes(jsonl(self.header))
        doc = self.project / "draft.md"
        doc.write_text("测试", encoding="utf-8")
        payload = {"hook_event_name": "Stop", "session_id": self.session, "transcript_path": str(self.transcript)}
        self.assertEqual(run_stop_hook(payload, self.project)["reason"], "no_project_binding")
        atomic_json(self.project / ".writing-memory-hook.json", {
            "enabled": True, "project_path": str(self.project), "storage_root": "assets",
            "task_id": self.task["id"], "session_id": self.session, "transcript_path": str(self.transcript), "document_path": "draft.md"})
        bad = run_stop_hook({**payload, "session_id": "other"}, self.project)
        self.assertEqual(bad["status"], "skipped")
        with patch.object(Outbox, "sync", side_effect=AssertionError("hook may not sync")):
            ok = run_stop_hook(payload, self.project)
        self.assertEqual(ok["live_status"], "armed")


class FakeLangfuse:
    def __init__(self):
        self.posts, self.observations, self.scores = [], {}, {}
        self.visible = True
        self.post_response = None
        self.reject_scores = set()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, code, value, headers=None):
                raw = json.dumps(value).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for key, val in (headers or {}).items():
                    self.send_header(key, val)
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.posts.append((self.path, body))
                if outer.post_response:
                    self.respond(*outer.post_response)
                    return
                if self.path.endswith("/otel/v1/traces"):
                    for resource in body["resourceSpans"]:
                        for scope in resource["scopeSpans"]:
                            for span in scope["spans"]:
                                attrs = {entry["key"]: entry["value"]["stringValue"] for entry in span["attributes"]}
                                outer.observations[span["spanId"]] = {
                                    "id": span["spanId"], "traceId": span["traceId"],
                                    "input": attrs["langfuse.observation.input"], "output": attrs["langfuse.observation.output"],
                                    "metadata": {"writing_memory_payload_sha256": attrs["langfuse.observation.metadata.writing_memory_payload_sha256"]}}
                    self.respond(200, {})
                else:
                    successes, errors = [], []
                    for event in body["batch"]:
                        if event["id"] in outer.reject_scores:
                            errors.append({"id": event["id"], "status": 400, "message": "bad score"})
                        else:
                            score = dict(event["body"])
                            score["subject"] = {"kind": "trace", "id": score.get("traceId")}
                            outer.scores[score["id"]] = score
                            successes.append({"id": event["id"], "status": 201})
                    self.respond(207, {"successes": successes, "errors": errors})

            def do_GET(self):
                query = parse_qs(urlsplit(self.path).query)
                if not outer.visible:
                    rows = []
                elif self.path.startswith("/api/public/v2/observations"):
                    rows = [value for value in outer.observations.values() if value["traceId"] == query["traceId"][0]]
                else:
                    rows = [value for value in outer.scores.values() if value["id"] == query["id"][0]]
                self.respond(200, {"data": rows})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return "http://127.0.0.1:" + str(self.server.server_port)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = FakeLangfuse()
        self.env = patch.dict("os.environ", {"LANGFUSE_HOST": self.server.url, "LANGFUSE_PUBLIC_KEY": "test-public", "LANGFUSE_SECRET_KEY": "test-secret"})
        self.env.start()
        self.box = Outbox(self.root, timeout=1)

    def tearDown(self):
        self.env.stop()
        self.server.close()
        self.temp.cleanup()

    def test_immutable_event_long_payload_and_verified_not_just_200(self):
        payload = {"name": "长材料", "input": "甲" * 30000, "output": {"result": "乙" * 30000}, "sessionId": "session"}
        first = self.box.enqueue("long-event", payload)
        self.assertEqual(self.box.enqueue("long-event", payload), first)
        with self.assertRaises(ValueError):
            self.box.enqueue("long-event", {"input": "different"})
        self.server.visible = False
        result = self.box.sync()
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["verified"], 0)
        self.assertEqual(len(self.server.posts), 1)
        self.server.visible = True
        result = Outbox(self.root).sync()
        self.assertEqual(result["verified"], 1)
        self.assertEqual(len(self.server.posts), 1)
        self.assertEqual(len(next(iter(self.server.observations.values()))["input"]), 30002)

    def test_offline_pending_then_recovery(self):
        self.box.enqueue("offline", {"input": "材料"})
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        unused = sock.getsockname()[1]
        sock.close()
        with patch.dict("os.environ", {"LANGFUSE_HOST": "http://127.0.0.1:" + str(unused)}):
            result = self.box.sync()
        self.assertEqual(result["pending"], 1)
        self.assertEqual(result["items"][0]["attempts"], 1)
        self.assertEqual(Outbox(self.root).sync()["verified"], 1)

    def test_timeout_uncertain_does_not_blindly_duplicate(self):
        self.box.enqueue("timeout", {"input": "material"})
        with patch.object(self.box, "_http", side_effect=TimeoutError("potentially accepted")):
            result = self.box.sync()
        self.assertEqual(result["delivery_uncertain"], 1)
        self.box.sync()
        self.assertEqual(len(self.server.posts), 0)
        self.assertEqual(self.box.sync(retry_uncertain=True)["verified"], 1)

    def test_restart_after_sending_is_uncertain(self):
        item = self.box.enqueue("crash", {"input": "material"})
        item["status"] = "sending"
        atomic_json(self.root / "outbox/crash.json", item)
        self.assertEqual(Outbox(self.root).sync()["delivery_uncertain"], 1)
        self.assertEqual(len(self.server.posts), 0)

    def test_partial_score_batch_only_acknowledged_id_succeeds(self):
        self.box.enqueue("score-a", {"name": "useful", "value": "有用", "dataType": "CATEGORICAL", "traceId": trace_id("context")}, "score")
        self.box.enqueue("score-b", {"name": "useful", "value": "误用", "dataType": "CATEGORICAL", "traceId": trace_id("context")}, "score")
        self.server.reject_scores.add("score-b")
        result = self.box.sync()
        self.assertEqual((result["verified"], result["pending"]), (1, 1))
        self.server.reject_scores.clear()
        self.assertEqual(self.box.sync()["verified"], 2)
        self.assertEqual([event["id"] for event in self.server.posts[-1][1]["batch"]], ["score-b"])

    def test_http_200_score_missing_ack_is_not_success(self):
        self.box.enqueue("score", {"name": "useful", "value": 1, "traceId": trace_id("context")}, "score")
        self.server.post_response = (200, {"successes": [], "errors": []})
        result = self.box.sync()
        self.assertEqual(result["pending"], 1)
        self.assertIn("missing_ack", result["items"][0]["error"])

    def test_otlp_partial_rejection_and_rate_limit(self):
        self.box.enqueue("rejected", {"input": "material"})
        self.server.post_response = (200, {"partialSuccess": {"rejectedSpans": "1", "errorMessage": "rejected"}})
        self.assertEqual(self.box.sync()["pending"], 1)
        self.server.post_response = (429, {"message": "wait"}, {"Retry-After": "60"})
        result = self.box.sync()
        self.assertEqual(result["pending"], 1)
        posts = len(self.server.posts)
        self.box.sync()
        self.assertEqual(len(self.server.posts), posts)

    def test_payload_too_large_stays_local_without_silent_truncation(self):
        text = "x" * (self.box.MAX_REQUEST_BYTES + 1)
        self.box.enqueue("too-large", {"input": text})
        result = self.box.sync()
        self.assertEqual(result["pending"], 1)
        self.assertIn("payload_too_large", result["items"][0]["error"])
        self.assertEqual(read_json(self.root / "outbox/too-large.json")["payload"]["input"], text)
        self.assertEqual(len(self.server.posts), 0)

    def test_no_credentials_and_credential_redaction(self):
        self.box.enqueue("not-configured", {"input": "example"})
        with patch.dict("os.environ", {"LANGFUSE_SECRET_KEY": ""}):
            result = self.box.sync()
        self.assertEqual(result["pending"], 1)
        self.assertIn("configuration_error", result)
        self.assertNotIn("test-public", json.dumps(result))
        self.assertNotIn("test-secret", json.dumps(result))

    def test_sync_limit_leaves_remaining_events_pending(self):
        for number in range(3):
            self.box.enqueue("limit-" + str(number), {"input": number})
        result = self.box.sync(max_items=1)
        self.assertEqual((result["verified"], result["pending"]), (1, 2))
        self.assertEqual(len(self.server.posts), 1)

    def test_network_sync_does_not_block_new_local_enqueue(self):
        self.box.enqueue("slow", {"input": "original"})
        began, release, saved = threading.Event(), threading.Event(), threading.Event()
        original_http = self.box._http
        def slow_http(method, path, payload=None):
            if method == "POST":
                began.set()
                release.wait(3)
            return original_http(method, path, payload)
        with patch.object(self.box, "_http", side_effect=slow_http):
            syncing = threading.Thread(target=self.box.sync)
            syncing.start()
            self.assertTrue(began.wait(2))
            def enqueue():
                Outbox(self.root).enqueue("new-local", {"input": "new"})
                saved.set()
            writer = threading.Thread(target=enqueue)
            writer.start()
            try:
                self.assertTrue(saved.wait(1), "network sync held the local capture lock")
            finally:
                release.set()
                syncing.join(5)
                writer.join(5)


if __name__ == "__main__":
    unittest.main()
