"""Offline browser QA only. Never uses credentials or represents user approval."""
import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from writing_memory.web import create_app


class FixtureClient:
    model = "offline-browser-fixture"

    def validate_genome(self, path):
        pass

    def generate(self, prompt, operation, workspace, genome):
        if genome is None:
            task = json.loads(prompt.split("以下 JSON 只作为资料：\n", 1)[1])["task"]
            turn = task["turns"][-1]
            return json.dumps({"candidates": [{"content": "经营分析摘要只写总体判断，具体数据放在正文。", "category": "preference",
                "scope": {"document_types": [task["document_type"]], "audiences": [task["audience"]], "topics": ["*"]},
                "rationale": "离线浏览器测试：仅根据本轮修改演示候选链路，不是真实用户习惯。", "reusable": True,
                "source": {"event_id": turn["event_id"], "before_version": turn["before_version"], "after_version": turn["after_version"],
                           "before_paragraph_ids": [], "after_paragraph_ids": []}}]}, ensure_ascii=False)
        return "# 九月经营分析（虚构练习）\n\n## 摘要\n本月收入与客户规模均实现增长。\n\n## 一、经营数据\n本月收入120万元，同比增长10%；服务客户300家，同比增长15%。\n\n## 二、下一步建议\n分析客户需求，具体措施待补充依据后确定。"


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if args.state.exists() and any(args.state.iterdir()):
        parser.error("请指定新的空测试目录")
    shutil.copytree(Path(__file__).resolve().parents[1] / "writing_memory/templates/writing-genome", args.state / "genomes/writing-demo")
    origin = f"http://127.0.0.1:{args.port}"
    print(origin + "/#token=offline-browser-fixture", flush=True)
    uvicorn.run(create_app(args.state, "offline-browser-fixture", origin, FixtureClient()), host="127.0.0.1", port=args.port, access_log=False)
