"""Explicit paid smoke test with fictional text and isolated, disposable state.

Simulated adoption here is test evidence only, never the owner's writing preference.
The source credential is read, never printed or modified.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from writing_memory.rsih_setup import setup
from writing_memory.util import atomic_json, read_json
from writing_memory.workbench import Workbench


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--credentials', type=Path, required=True)
    parser.add_argument('--rsih', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='rsih-real-smoke-') as folder:
        state = Path(folder)
        setup(state, args.rsih, skip_key=True)
        key = read_json(args.credentials).get('DEEPSEEK_API_KEY')
        if not key:
            raise ValueError('指定的凭据文件没有 DeepSeek 密钥')
        atomic_json(state/'credentials.json', {'DEEPSEEK_API_KEY':key})
        (state/'credentials.json').chmod(0o600)
        app = Workbench(state)
        first = app.create('虚构验收：九月经营分析','## 摘要\n本月收入120万元，同比增长10%。\n\n## 经营数据\n本月收入120万元，同比增长10%。', '虚构软件测试，不是真实业务', '测试负责人', '经营分析', demo=True)['id']
        instruction = '对这类面向测试负责人的经营分析，摘要应概括总体判断，具体数字只放正文，避免重复。请按这个写法修改，不编造新事实，输出完整材料。'
        before = app.task(first)['versions'][-1]
        app.prepare_draft(first, instruction, 'smoke_draft', before['content_hash'])
        draft = app.generate_draft(first, 'smoke_draft')
        app.adopt(first, draft['id'], '自动化虚构验收角色（非真实用户认可）')
        extracted = app.extract_once(first, 'learn_adopt_smoke_draft')
        candidates = [c for c in app.memory.candidates() if c.get('reusable') and c['category'] in {'method','preference'}]
        if candidates:
            rule = app.memory.decide(candidates[0]['id'], 'approve', '自动化虚构验收角色（非真实用户认可）')
            origin = 'real_model_extracted_candidate_confirmed_by_test_harness'
        else:
            ids = app.memory.import_bundle({'format':'rsih-personal-rules','schema_version':1,'rules':[{'content':'经营分析的摘要只概括总体判断，具体数字放正文，避免重复。','category':'preference','scope':{'document_types':['经营分析'],'audiences':['测试负责人'],'topics':['*']}}]})
            rule = app.memory.decide(ids[0], 'approve', '人工构造的软件测试角色（非用户认可）')
            origin = 'explicit_fixture_rule_because_no_reusable_candidate'
        second = app.create('虚构验收：十月经营分析', '十月收入130万元，同比增长8%；服务客户310家，同比增长6%。', '虚构软件测试，不是真实业务', '测试负责人', '经营分析', demo=True)['id']
        app.prepare_draft(second, '请整理成一份包含摘要和经营数据两部分的简短经营分析，不编造原因和成果。', 'smoke_transfer', app.task(second)['versions'][-1]['content_hash'])
        transfer = app.generate_draft(second, 'smoke_transfer')
        assert any(item['id']==rule['id'] for item in transfer['context']['loaded_rules'])
        summary = {'scenario':'fictional real-model smoke, not actual user approval', 'model':app.client.model,
                   'calls':3, 'candidate_count':len(extracted['candidate_ids']), 'rule_origin':origin,
                   'rule_content':rule['content'], 'loaded_rule_ids':[r['id'] for r in transfer['context']['loaded_rules']],
                   'first_draft':draft['content'], 'second_draft':transfer['content'],
                   'temporary_data_removed_on_exit':True}
        atomic_json(args.output, summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__=='__main__':main()
