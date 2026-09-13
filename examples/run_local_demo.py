"""Create an explicitly synthetic revision demo; no network or real approval."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from writing_memory.core import Store
from writing_memory.delivery import recover
from writing_memory.experience import ExperienceLibrary
from writing_memory.util import atomic_json, atomic_write


def run(destination: Path) -> dict:
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("演示输出目录必须尚不存在，避免覆盖已有资料")
    destination.mkdir(parents=True)
    manuscript = destination / "演示文稿.md"
    atomic_write(manuscript, "# 离线演示：业务试点\n\n行业变化较快，我们还需要继续研究。\n\n现有材料仅用于试点讨论。\n")
    store = Store(destination / "assets")
    store.init()
    task = store.create_task("离线演示文稿（虚构）", "演示修改追溯，不代表真实业务判断", "领导", "决策方案", manuscript, topic="试点")
    task_id = task["id"]
    paragraph_id = task["versions"][0]["paragraphs"][1]["id"]
    revisions = [
        ("先在这段给出推荐路径，再说明依据。", "建议先开展小范围试点，再依据结果决定是否扩大。"),
        ("同一段补充适用边界，不能泛化为全面推广。", "建议先开展小范围试点；现有依据仅支持验证可行性，暂不支持全面推广。"),
    ]
    for index, (instruction, replacement) in enumerate(revisions, 1):
        current = task["versions"][-1]
        paragraph = next(p for p in current["paragraphs"] if p["id"] == paragraph_id)
        content = current["content"][:paragraph["start"]] + replacement + current["content"][paragraph["end"]:]
        atomic_write(manuscript, content)
        task = store.capture(task_id, content, instruction, "offline-demo", f"demo-turn-{index}",
                             target_version=current["id"], target_paragraph=paragraph_id, excerpt=paragraph["text"], source="synthetic_demo")
    inserted = "用途：供讨论试点路径。\n\n" + task["versions"][-1]["content"]
    atomic_write(manuscript, inserted)
    task = store.capture(task_id, inserted, "在全文前补充用途，保留已有段落。", "offline-demo", "demo-turn-3", source="synthetic_demo")
    task = store.restore_paragraph(task_id, "V2", paragraph_id, paragraph_id, "恢复 V2 的结论段，其他内容保留 V4。", "离线演示角色（非真实审核）")
    library = ExperienceLibrary(store.root)
    extraction = library.prepare_extraction(task)
    proposal = {"candidates": [{"content": "面向领导决策的试点方案，先给推荐路径，再说明依据和边界。", "category": "method",
        "scope": {"document_types": ["决策方案"], "audiences": ["领导"], "topics": ["试点"]},
        "source": {"event_id": "demo-turn-1", "before_version": "V1", "after_version": "V2",
                   "before_paragraph_ids": [paragraph_id], "after_paragraph_ids": [paragraph_id]},
        "rationale": "离线验收用预置候选，来自该演示第一轮修改；未调用模型，未经过真实人工审核。", "reusable": True}]}
    proposal_path = destination / "演示候选.json"
    atomic_json(proposal_path, proposal)
    candidates = library.import_candidates(task, proposal_path, extraction_id=extraction["id"])
    review = library.prepare_review([candidates[0]["id"]])
    next_task = store.create_task("下一份演示任务", "检查未发布候选不会自动进入新写作", "领导", "决策方案", topic="试点")
    exported = library.export_for_task(next_task)
    recovered = recover(store.root)
    rows = ["| 版本 | 实际内容变化 |", "|---|---|", "| V1 | 原始稿 |", "| V2 | 同段改为推荐路径在前 |", "| V3 | 同段补充适用边界 |", "| V4 | 前面插入用途，已有段落编号保留 |", "| V5 | 结论段恢复自 V2，其他内容保留 V4 |"]
    report = "\n".join(["# 本地演示结果", "", "本目录全部业务内容、指令和操作者均为离线验收样例。没有调用模型或远程服务，没有真实审核、发布或反馈。", "", *rows, "",
        f"任务编号：`{task_id}`", f"固定段落编号：`{paragraph_id}`", "",
        "已生成带来源的候选与送审文件。新任务导出条目数为 0，证明未经 GitHub 审核合并的候选不会自动加载。", "",
        f"[实际任务记录](assets/tasks/{task_id}.json)", f"[提炼上下文](assets/extractions/{extraction['id']}/context.json)",
        f"[送审文件](assets/reviews/{review['id']}/experiences/approved/{candidates[0]['id']}.json)", ""])
    atomic_write(destination / "演示结果.md", report)
    return {"output": str(destination), "report": str(destination / "演示结果.md"), "task_id": task_id,
            "versions": len(task["versions"]), "candidate_count": len(candidates), "published_experiences": len(exported["entries"]),
            "network_requests": 0, "delivery_pending": recovered["delivery"]["pending"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("demo-output"))
    args = parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False, indent=2))
