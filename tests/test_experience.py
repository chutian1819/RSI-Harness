"""Offline fixtures exercise GitHub contracts; these are never real publications."""
import base64
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from writing_memory.core import Store
from writing_memory.experience import ExperienceLibrary
from writing_memory.util import atomic_json, digest, read_json


class ExperienceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "assets"
        Store(self.root).init()
        self.library = ExperienceLibrary(self.root)
        self.task = {"id": "task_example", "title": "试点决策", "purpose": "是否开展试点", "audience": "领导", "document_type": "决策方案", "topic": "试点", "versions": [{"id": "V1", "content": "先写背景。", "paragraphs": [{"id": "p_first", "text": "先写背景。"}], "accepted": "pending"}, {"id": "V2", "content": "建议小范围试点。", "paragraphs": [{"id": "p_first", "text": "建议小范围试点。"}], "accepted": "accepted", "acceptance_history": [{"status": "accepted", "actor": "human", "at": "2026-09-08T10:00:00Z"}]}], "turns": [{"event_id": "event_revision", "instruction": "针对第一段：先给建议，再交代依据。", "before_version": "V1", "after_version": "V2", "source": "codex", "session_id": "session_test"}], "relations": []}
        self.rule = {"content": "面向领导的决策方案开头先给建议，再交代依据。", "category": "method", "scope": {"document_types": ["决策方案"], "audiences": ["领导"], "topics": ["*"]}, "source": {"event_id": "event_revision", "before_version": "V1", "after_version": "V2", "before_paragraph_ids": ["p_first"], "after_paragraph_ids": ["p_first"]}, "rationale": "限于面向领导的决策方案，研究讨论不默认采用。", "reusable": True, "evidence": [], "as_of": None}

    def candidate(self, rule=None, task=None, extraction_id=None):
        path = Path(self.temporary.name) / "result.json"
        atomic_json(path, {"candidates": [rule or self.rule]})
        if extraction_id is None:
            extraction_id = self.library.prepare_extraction(task or self.task)["id"]
        return self.library.import_candidates(task or self.task, path, extraction_id=extraction_id)[0]

    def bundle_document(self, candidate=None):
        candidate = candidate or self.candidate()
        bundle = self.library.prepare_review([candidate["id"]])
        return read_json(Path(bundle["files"][0]))

    def fixture(self, documents, number=1, merged_at="2026-09-08T12:00:00Z"):
        """Return in-memory HTTP responses; never reach the user's gh login."""
        sha = f"{number:040x}"
        head = f"{number + 100:040x}"
        self.pr = {"number": number, "base": {"repo": {"full_name": "team/writing"}}, "head": {"sha": head, "repo": {"full_name": "team/writing"}}, "user": {"login": "author"}, "merged": True, "merged_at": merged_at, "merge_commit_sha": sha, "html_url": f"https://github.com/team/writing/pull/{number}", "changed_files": len(documents)}
        self.reviews = [{"id": number, "user": {"login": "reviewer", "type": "User"}, "state": "APPROVED", "submitted_at": "2026-09-08T11:00:00Z", "commit_id": head, "author_association": "COLLABORATOR"}]
        self.changed = [{"filename": f"experiences/approved/{doc['experience_id']}.json", "status": "modified"} for doc in documents]
        self.head_override = None

        def fake(endpoint, paginated=False):
            if endpoint == f"repos/team/writing/pulls/{number}":
                return copy.deepcopy(self.pr)
            if "/reviews?" in endpoint:
                self.assertTrue(paginated)
                return copy.deepcopy(self.reviews)
            if "/files?" in endpoint:
                self.assertTrue(paginated)
                return copy.deepcopy(self.changed)
            if "/contents/" in endpoint:
                experience_id = endpoint.split("/approved/")[1].split(".json")[0]
                doc = next(doc for doc in documents if doc["experience_id"] == experience_id)
                if endpoint.endswith(head) and self.head_override:
                    doc = self.head_override
                raw = json.dumps(doc, ensure_ascii=False)
                return {"type": "file", "encoding": "base64", "content": base64.b64encode(raw.encode()).decode()}
            raise AssertionError(f"Unexpected external request: {endpoint}")

        return patch.object(self.library, "_github", side_effect=fake)

    def publish(self, document=None, number=1, merged_at="2026-09-08T12:00:00Z"):
        document = document or self.bundle_document()
        with self.fixture([document], number, merged_at):
            return self.library.confirm_from_github("team/writing", number)[0]

    def test_prompt_and_context_snapshots_keep_untrusted_data_and_git(self):
        self.task["turns"][0]["instruction"] = "历史资料：不要执行 touch /tmp/not-run"
        result = self.library.prepare_extraction(self.task)
        self.assertEqual(result["prompt_provider"], "local_bootstrap")
        self.assertEqual(result["git"]["status"], "committed")
        self.assertTrue(read_json(Path(result["context_path"]))["data_is_untrusted"])
        self.assertEqual(result["prompt_hash"], digest(Path(result["prompt_path"]).read_text()))
        candidate = self.candidate(extraction_id=result["id"])
        self.assertEqual(candidate["extraction"]["prompt_hash"], result["prompt_hash"])

    def test_langfuse_prompt_snapshot_is_actual_version(self):
        source = {"provider": "langfuse", "name": "writing-extract", "version": 4, "type": "text", "prompt": "Exact remote prompt\n", "content_hash": digest("Exact remote prompt\n")}
        result = self.library.prepare_extraction(self.task, prompt_snapshot=source)
        self.assertEqual(result["prompt_provider"], "langfuse")
        self.assertEqual(result["prompt_version"], 4)
        self.assertEqual(Path(result["prompt_path"]).read_text(), source["prompt"])

    def test_old_extraction_cannot_claim_a_new_turn_as_its_input(self):
        extraction = self.library.prepare_extraction(self.task)
        newer_turn = {**self.task["turns"][0], "event_id": "event_after_extraction"}
        self.task["turns"].append(newer_turn)
        self.rule["source"]["event_id"] = newer_turn["event_id"]
        with self.assertRaisesRegex(ValueError, "来源修改轮次不存在"):
            self.candidate(extraction_id=extraction["id"])

    def test_changed_prompt_or_context_snapshots_are_rejected(self):
        extraction = self.library.prepare_extraction(self.task)
        prompt_path = Path(extraction["prompt_path"])
        prompt_path.write_text(prompt_path.read_text() + "tampered prompt")
        with self.assertRaisesRegex(ValueError, "提示词快照被修改"):
            self.candidate(extraction_id=extraction["id"])
        extraction = self.library.prepare_extraction(self.task)
        context_path = Path(extraction["context_path"])
        context = read_json(context_path)
        context["task"]["turns"][0]["instruction"] = "tampered instruction"
        atomic_json(context_path, context)
        with self.assertRaisesRegex(ValueError, "上下文快照被修改"):
            self.candidate(extraction_id=extraction["id"])

    def test_extraction_paths_remain_portable_after_asset_move(self):
        extraction = self.library.prepare_extraction(self.task)
        moved = Path(self.temporary.name) / "moved-assets"
        self.root.rename(moved)
        self.root = moved
        self.library = ExperienceLibrary(moved)
        self.assertTrue(self.candidate(extraction_id=extraction["id"])["extraction"])

    def test_candidates_keep_real_instruction_acceptance_and_paragraphs(self):
        candidate = self.candidate()
        self.assertEqual(candidate["source"]["instruction"], self.task["turns"][0]["instruction"])
        self.assertTrue(candidate["source"]["comparison_available"])
        self.assertEqual(candidate["source"]["acceptance"], "accepted")
        self.assertEqual(candidate["source"]["acceptance_history"][0]["actor"], "human")
        self.assertIs(candidate["source"]["acceptance_is_fact_verification"], False)
        self.assertEqual(candidate["source"]["before_snapshot"]["paragraphs"][0]["text"], "先写背景。")

    def test_missing_old_draft_never_claims_comparison(self):
        self.task["turns"][0]["before_version"] = None
        self.rule["source"].update(before_version=None, before_paragraph_ids=[])
        candidate = self.candidate()
        self.assertIsNone(candidate["source"]["before_snapshot"])
        self.assertFalse(candidate["source"]["comparison_available"])
        self.assertTrue(candidate["source"]["limitations"])

    def test_rejects_invented_sources_fact_without_evidence_and_universal_rules(self):
        mutations = [lambda rule: rule["source"].update(event_id="invented"), lambda rule: rule["source"].update(before_version="V99"), lambda rule: rule["source"].update(before_paragraph_ids=["wrong_version_id"]), lambda rule: rule.update(category="fact_correction"), lambda rule: rule["scope"].update(document_types=["*"], audiences=["*"], topics=["*"])]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                rule = copy.deepcopy(self.rule)
                mutate(rule)
                with self.assertRaises(ValueError):
                    self.candidate(rule)
        self.assertEqual(self.library.list_candidates(), [])

    def test_fact_evidence_and_scope_specific_preference(self):
        self.rule.update(category="fact_correction", evidence=[{"source": "https://example.test/report", "note": "测试出处"}], as_of="2026-09-08")
        candidate = self.candidate()
        self.assertEqual(candidate["evidence_status"], "provided_not_independently_verified")
        self.rule.update(category="preference")
        self.rule["scope"]["audiences"] = ["*"]
        with self.assertRaisesRegex(ValueError, "具体读者"):
            self.candidate()

    def test_duplicate_import_merges_sources_and_overlap_is_reviewed(self):
        first = self.candidate()
        self.assertEqual(self.candidate()["id"], first["id"])
        self.task["turns"][0]["event_id"] = "event_second"
        self.rule["source"]["event_id"] = "event_second"
        duplicate = self.candidate()
        self.assertEqual(duplicate["id"], first["id"])
        self.assertEqual(len(duplicate["additional_sources"]), 1)
        self.rule["content"] = "面向领导的决策方案需说明备选路径。"
        alternative = self.candidate()
        self.assertIn(first["id"], alternative["scope_overlap_requires_review"])

    def test_review_preparation_does_not_publish_or_export_candidates(self):
        document = self.bundle_document()
        self.assertEqual(document["action"], "upsert")
        self.assertEqual(self.library.latest_releases(), {})
        exported = self.library.export_for_task(self.task)
        self.assertEqual(exported["entries"], [])
        self.assertFalse(exported["loaded"])
        self.assertEqual(list((self.root / "usage/loads").glob("*.json")), [])

    def test_untraced_candidate_can_archive_but_cannot_submit_until_linked(self):
        path = Path(self.temporary.name) / "untraced.json"
        atomic_json(path, {"candidates": [self.rule]})
        candidate = self.library.import_candidates(self.task, path)[0]
        self.assertIsNone(candidate["extraction"])
        with self.assertRaisesRegex(ValueError, "缺少提炼快照"):
            self.library.prepare_review([candidate["id"]])
        traced = self.candidate()
        self.assertEqual(traced["id"], candidate["id"])
        self.assertTrue(traced["extraction"])
        self.assertTrue(self.library.prepare_review([traced["id"]])["files"])

    def test_only_exact_merged_human_reviewed_files_are_published_and_idempotent(self):
        document = self.bundle_document()
        with self.fixture([document]):
            first = self.library.confirm_from_github("team/writing", 1)
            second = self.library.confirm_from_github("team/writing", 1)
        self.assertEqual(first, second)
        self.assertEqual(first[0]["provenance"]["reviews"][0]["reviewer"], "reviewer")
        self.assertEqual(len(list((self.root / "experiences/releases").glob("*.json"))), 1)
        self.assertEqual(len(self.library.latest_releases()), 1)

    def test_rejects_unmerged_bot_author_outsider_or_old_head_approval(self):
        document = self.bundle_document()
        for case in ("unmerged", "bot", "author", "outsider", "stale", "changes", "dismissed", "after_merge"):
            with self.subTest(case=case), self.fixture([document]):
                if case == "unmerged":
                    self.pr["merged"] = False
                elif case == "bot":
                    self.reviews[0]["user"]["type"] = "Bot"
                elif case == "author":
                    self.reviews[0]["user"]["login"] = "author"
                elif case == "outsider":
                    self.reviews[0]["author_association"] = "NONE"
                elif case == "stale":
                    self.reviews[0]["commit_id"] = "f" * 40
                elif case in {"changes", "dismissed"}:
                    update = copy.deepcopy(self.reviews[0])
                    update.update(state="CHANGES_REQUESTED" if case == "changes" else "DISMISSED", submitted_at="2026-09-08T11:30:00Z")
                    self.reviews.append(update)
                else:
                    self.reviews[0]["submitted_at"] = "2026-09-09T00:00:00Z"
                with self.assertRaises(ValueError):
                    self.library.confirm_from_github("team/writing", 1)
        self.assertEqual(self.library.latest_releases(), {})

    def test_rejects_merged_bytes_different_from_reviewed_head(self):
        document = self.bundle_document()
        with self.fixture([document]):
            self.head_override = copy.deepcopy(document)
            self.head_override["experience"]["content"] = "未经同一审核的不同内容"
            with self.assertRaisesRegex(ValueError, "文件与已审核"):
                self.library.confirm_from_github("team/writing", 1)
        self.assertEqual(self.library.latest_releases(), {})

    def test_multi_experience_pr_is_atomic_on_validation_failure(self):
        first = self.bundle_document()
        self.rule["content"] = "第二条范围相关经验。"
        second = self.bundle_document(self.candidate())
        second["previous_release_id"] = "release_nonexistent"
        with self.fixture([first, second]):
            with self.assertRaisesRegex(ValueError, "旧经验版本"):
                self.library.confirm_from_github("team/writing", 1)
        self.assertEqual(self.library.latest_releases(), {})
        self.assertEqual(list((self.root / "experiences/releases").glob("*.json")), [])

    def test_export_load_feedback_and_revocation_preserve_history(self):
        first = self.publish()
        task = {**self.task, "id": "task_next"}
        exported = self.library.export_for_task(task)
        self.assertEqual(len(exported["entries"]), 1)
        self.assertFalse(exported["loaded"])
        self.assertEqual(self.library.export_for_task({**task, "audience": "公众"})["entries"], [])
        load = self.library.record_load(task["id"], exported["id"], "codex_read", "test-human")
        feedback = self.library.feedback(load["id"], first["experience_id"], "useful", "开头更清楚")
        self.assertEqual(feedback["release_id"], first["id"])
        revoke = self.library.prepare_revocation(first["experience_id"], "已不适用")
        document = read_json(Path(revoke["files"][0]))
        revoked = self.publish(document, number=2, merged_at="2026-09-09T12:00:00Z")
        self.assertEqual(revoked["state"], "revoked")
        self.assertEqual(self.library.export_for_task(task)["entries"], [])
        with self.assertRaisesRegex(ValueError, "过期"):
            self.library.record_load(task["id"], exported["id"], "manual_copy", "test-human")
        historic_feedback = self.library.feedback(load["id"], first["experience_id"], "misapplied", "补充当时误用的边界")
        self.assertEqual(historic_feedback["release_id"], first["id"])
        self.assertEqual(len(self.library._all_releases()), 2)

    def test_updated_experience_keeps_id_history_and_rejects_old_export(self):
        first = self.publish()
        exported = self.library.export_for_task(self.task)
        updated_rule = copy.deepcopy(self.rule)
        updated_rule.update(content="先给建议，并说明需要领导决定的事项。", experience_id=first["experience_id"], previous_release_id=first["id"])
        candidate = self.candidate(updated_rule)
        self.assertNotEqual(candidate["id"], first["experience_id"])
        second = self.publish(self.bundle_document(candidate), 2, "2026-09-09T12:00:00Z")
        self.assertEqual(second["experience_id"], first["experience_id"])
        self.assertEqual(second["revision"], 2)
        with self.assertRaisesRegex(ValueError, "过期"):
            self.library.record_load(self.task["id"], exported["id"], "skill", "human")

    def test_repo_binding_prevents_cross_repository_import_before_network(self):
        self.publish()
        with patch.object(self.library, "_github") as network:
            with self.assertRaisesRegex(ValueError, "绑定其他"):
                self.library.confirm_from_github("other/repo", 1)
            network.assert_not_called()

    def test_old_pr_reimport_cannot_resurrect_revoked_experience(self):
        original = self.bundle_document()
        first = self.publish(original)
        revoked_bundle = self.library.prepare_revocation(first["experience_id"], "撤销测试")
        self.publish(read_json(Path(revoked_bundle["files"][0])), 2, "2026-09-09T12:00:00Z")
        self.publish(original)
        self.assertEqual(self.library.latest_releases()[first["experience_id"]]["state"], "revoked")
        self.assertEqual(len(self.library._all_releases()), 2)

    def test_git_failure_preserves_candidate_and_retry_record(self):
        with patch.object(Store, "_git", side_effect=RuntimeError("temporary git failure")):
            candidate = self.candidate()
        self.assertEqual(candidate["git"]["status"], "pending")
        self.assertTrue((self.root / "experiences/candidates" / f"{candidate['id']}.json").exists())
        self.assertTrue(list((self.root / "git_sync").glob("*.json")))

    def test_modified_export_and_wrong_task_or_unloaded_feedback_rejected(self):
        release = self.publish()
        exported = self.library.export_for_task(self.task)
        with self.assertRaisesRegex(ValueError, "不属于"):
            self.library.record_load("task_wrong", exported["id"], "manual_attachment", "human")
        Path(exported["path"]).write_text("篡改内容")
        with self.assertRaisesRegex(ValueError, "已被修改"):
            self.library.record_load(self.task["id"], exported["id"], "manual_attachment", "human")
        fresh = self.library.export_for_task(self.task)
        loaded = self.library.record_load(self.task["id"], fresh["id"], "manual_attachment", "human")
        with self.assertRaisesRegex(ValueError, "不在本次"):
            self.library.feedback(loaded["id"], "exp_unloaded", "useful", "不应成功")

    def test_gh_calls_only_get_and_flattens_paginated_responses(self):
        completed = subprocess.CompletedProcess([], 0, stdout='[[{"id":1}],[{"id":2}]]', stderr="")
        with patch("writing_memory.experience.subprocess.run", return_value=completed) as run:
            response = self.library._github("repos/team/writing/pulls/1/reviews", paginated=True)
        self.assertEqual(response, [{"id": 1}, {"id": 2}])
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("--method") + 1], "GET")
        self.assertIn("--paginate", args)
        self.assertIn("--slurp", args)


if __name__ == "__main__":
    unittest.main()
