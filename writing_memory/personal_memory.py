"""Personal confirmation is deliberately separate from GitHub publishing authority."""
from __future__ import annotations

import copy

from .core import Store
from .experience import ExperienceLibrary, SCOPE_KEYS, _json_hash, _required_text
from .util import atomic_json, file_lock, read_json, utc_now, validate_id


def clean_rule(value):
    if not isinstance(value, dict):
        raise ValueError("规则必须为对象")
    content = _required_text(value.get("content"), "规则内容")
    if len(content) > 4000:
        raise ValueError("单条规则不能超过 4000 字符")
    category = value.get("category", "preference")
    if category not in {"preference", "method"}:
        raise ValueError("个人长期规则只接收写作偏好和方法；事实或一次性要求请保留在材料中")
    scope = value.get("scope")
    if not isinstance(scope, dict):
        raise ValueError("请明确规则适用范围")
    normalized = {}
    for key in SCOPE_KEYS:
        items = scope.get(key)
        if not isinstance(items, list) or not 1 <= len(items) <= 30:
            raise ValueError("范围必须是非空列表")
        normalized[key] = sorted(set(_required_text(item, "适用范围") for item in items))
        if any(len(item) > 200 for item in normalized[key]) or ("*" in normalized[key] and len(normalized[key]) != 1):
            raise ValueError("通用范围请只填写 *，其他范围每项不超过 200 字符")
    return {"content": content, "category": category, "scope": normalized}


def overlaps(left, right):
    return all("*" in left[key] or "*" in right[key] or set(left[key]) & set(right[key]) for key in SCOPE_KEYS)


class PersonalMemory:
    def __init__(self, store: Store):
        self.store = store
        self.path = store.root / "experiences/personal/library.json"
        self.team = ExperienceLibrary(store.root)

    def read(self):
        return read_json(self.path) if self.path.exists() else {"schema_version": 1, "rules": {}, "imports": {}, "decisions": {}}

    def save(self, data):
        atomic_json(self.path, data)
        self.store.commit_path(self.path, "Record personal memory decision (not team publication)")

    def candidates(self):
        data = self.read()
        rows = self.team.list_candidates() + list(data["imports"].values())
        return [{**row, "personal_decision": data["decisions"].get(row["id"])} for row in rows]

    def rules(self):
        return list(self.read()["rules"].values())

    def matching(self, task):
        return [copy.deepcopy(rule) for rule in self.rules()
                if rule["state"] == "active" and self.team._matches(rule, task)]

    def preview(self, value):
        rule = clean_rule(value)
        return [item for item in self.rules() if item["state"] == "active" and overlaps(item["scope"], rule["scope"])]

    def decide(self, candidate_id, decision, actor, content=None, scope=None,
               reviewed_overlap_ids=None, replace_ids=None):
        actor = _required_text(actor, "确认人")
        validate_id(candidate_id)
        if decision not in {"approve", "defer", "reject"}:
            raise ValueError("不支持的处理方式")
        with file_lock(self.store.root):
            data = self.read()
            candidate = next((c for c in self.candidates() if c["id"] == candidate_id), None)
            if candidate is None:
                raise ValueError("候选不存在")
            record = {"decision": decision, "actor": actor, "at": utc_now()}
            if decision == "approve":
                if candidate.get("reusable") is not True:
                    raise ValueError("一次性要求不能确认成长效规则")
                default_scope = copy.deepcopy(candidate["scope"])
                source_context = (candidate.get("source") or {}).get("task_context", {})
                if source_context.get("document_type") and source_context.get("audience"):
                    default_scope.update(document_types=[source_context["document_type"]], audiences=[source_context["audience"]])
                rule = clean_rule({**candidate, "content": content if content is not None else candidate["content"],
                                   "scope": scope if scope is not None else default_scope})
                rule_id = "personal_" + _json_hash(candidate_id)[:24]
                previous = data["rules"].get(rule_id)
                fingerprint = _json_hash(rule)
                if previous and previous["state"] == "active" and previous["content_hash"] == fingerprint:
                    return previous
                overlapping = [r for r in data["rules"].values() if r["id"] != rule_id and r["state"] == "active" and overlaps(r["scope"], rule["scope"])]
                overlap_ids = {r["id"] for r in overlapping}
                replacements = set(replace_ids or [])
                if not replacements <= overlap_ids:
                    raise ValueError("只能替换当前生效且范围重叠的规则")
                if not overlap_ids <= set(reviewed_overlap_ids or []) | replacements:
                    raise ValueError("存在适用范围重叠的规则，请先核对冲突，再选择共存、替换或缩小范围")
                for item in overlapping:
                    if item["id"] in replacements:
                        data["rules"][item["id"]] = self._revision(item, "revoked", actor, "被新规则替换")
                record.update(rule_id=rule_id)
                result = {**rule, "id": rule_id, "candidate_id": candidate_id, "state": "active",
                          "authority": "personal_confirmation", "revision": previous["revision"] + 1 if previous else 1,
                          "content_hash": fingerprint, "confirmed_by": actor, "confirmed_at": record["at"],
                          "source": copy.deepcopy(candidate.get("source")), "candidate_snapshot": copy.deepcopy(candidate),
                          "history": (previous.get("history", []) + [{k: v for k, v in previous.items() if k != "history"}]) if previous else []}
                data["rules"][rule_id] = result
            else:
                result = record
            data["decisions"][candidate_id] = record
            self.save(data)
            return result

    @staticmethod
    def _revision(rule, state, actor, reason):
        return {**rule, "state": state, "revision": rule["revision"] + 1, "changed_by": actor,
                "changed_at": utc_now(), "reason": reason,
                "history": rule.get("history", []) + [{k: v for k, v in rule.items() if k != "history"}]}

    def revoke(self, rule_id, actor):
        _required_text(actor, "确认人")
        with file_lock(self.store.root):
            data = self.read()
            if rule_id not in data["rules"]:
                raise ValueError("规则不存在")
            rule = data["rules"][rule_id]
            if rule["state"] != "revoked":
                rule = data["rules"][rule_id] = self._revision(rule, "revoked", actor, "用户撤销")
                self.save(data)
            return rule

    def export(self, ids):
        rules = {r["id"]: r for r in self.rules() if r["state"] == "active"}
        if not ids or any(i not in rules for i in ids):
            raise ValueError("请选择当前生效的规则")
        # Strict allow-list: no source, candidate, history, path, instruction or key.
        return {"format": "rsih-personal-rules", "schema_version": 1,
                "rules": [clean_rule(rules[i]) for i in dict.fromkeys(ids)]}

    def import_bundle(self, bundle):
        if not isinstance(bundle, dict) or bundle.get("format") != "rsih-personal-rules" or bundle.get("schema_version") != 1:
            raise ValueError("不是受支持的规则分享包")
        rows = bundle.get("rules")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
            raise ValueError("分享包应包含 1–100 条规则")
        cleaned = [clean_rule(item) for item in rows]
        ids = []
        with file_lock(self.store.root):
            data = self.read()
            for rule in cleaned:
                candidate_id = "import_" + _json_hash(rule)[:24]
                ids.append(candidate_id)
                if candidate_id not in data["imports"]:
                    data["imports"][candidate_id] = {**rule, "id": candidate_id, "reusable": True,
                        "state": "candidate", "created_at": utc_now(), "rationale": "同事分享，仅为候选；请核对后确认。",
                        "source": {"kind": "imported_rule", "bundle_hash": _json_hash(cleaned)}}
            self.save(data)
        return ids
