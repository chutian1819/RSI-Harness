"use strict";
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const E = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const tokenInHash = new URLSearchParams(location.hash.slice(1)).get("token");
if (tokenInHash) {
  sessionStorage.setItem("workbench-token", tokenInHash);
  history.replaceState(null, "", location.pathname);
}
const state = {
  documents: [],
  doc: null,
  tab: "write",
  page: "home",
  memory: null,
  jobs: [],
  jobSignature: "",
  dirty: false,
  editingHash: null,
};
const requestId = () => "web_" + crypto.randomUUID().replaceAll("-", "");
const date = (value) =>
  value
    ? new Date(value).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";
const scopeText = (scope) =>
  ["document_types", "audiences", "topics"]
    .map((k) =>
      (scope?.[k] || []).map((x) => (x === "*" ? "不限" : x)).join("、"),
    )
    .join(" / ");
const actor = () => {
  const value = $("#actor").value.trim();
  if (!value) {
    $("#actor").focus();
    throw Error(
      "请先在右上角填写确认人姓名。这会记录谁采用了稿件或确认了偏好。",
    );
  }
  return value;
};
$("#actor").value = localStorage.getItem("workbench-actor") || "";
$("#actor").addEventListener("change", () =>
  localStorage.setItem("workbench-actor", $("#actor").value.trim()),
);
function notice(message, good = false) {
  const n = $("#notice");
  n.textContent = message;
  n.className = good ? "success" : "";
  n.hidden = false;
  n.scrollIntoView({ block: "nearest", behavior: "smooth" });
  const dialog = $("dialog[open]");
  if (dialog) {
    let error = $(".dialog-notice", dialog);
    if (!error) {
      error = document.createElement("p");
      error.className = "hint dialog-notice";
      error.setAttribute("role", "alert");
      dialog.prepend(error);
    }
    error.textContent = message;
    error.scrollIntoView({ block: "nearest" });
  }
}
function hideNotice() {
  $("#notice").hidden = true;
}
async function api(path, body) {
  const response = await fetch("/api" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "X-Workbench-Token": sessionStorage.getItem("workbench-token") || "",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw Error(
      "本机服务未完整返回结果。记录可能已保存，请先刷新查看，再检查终端错误。",
    );
  }
  if (!response.ok)
    throw Error(
      typeof data.detail === "string"
        ? data.detail
        : "输入内容不完整或格式不正确，请检查后重试。",
    );
  return data;
}
function action(fn) {
  return async (event) => {
    const button = event?.currentTarget;
    try {
      if (button?.tagName === "BUTTON") button.disabled = true;
      await fn(event);
    } catch (e) {
      notice(e.message);
    } finally {
      if (button?.tagName === "BUTTON")
        button.disabled =
          button.id === "generate" &&
          state.jobs.some(
            (j) =>
              j.document_id === state.doc?.document_id &&
              j.kind === "draft" &&
              ["queued", "running"].includes(j.state),
          );
    }
  };
}
function download(filename, text, type = "text/plain;charset=utf-8") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function allowNavigate() {
  return (
    !state.dirty ||
    confirm("正文中有尚未保存的手工修改。离开会丢弃这部分输入，是否继续？")
  );
}
window.addEventListener("beforeunload", (e) => {
  if (state.dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});
function sidebar() {
  $("#doc-count").textContent = state.documents.length;
  $("#documents").innerHTML = state.documents.length
    ? state.documents
        .map(
          (d) =>
            `<button class="doc-link ${state.doc?.document_id === d.id && state.page === "document" ? "selected" : ""}" data-doc="${E(d.id)}"><strong>${E(d.title)}</strong><small>${E(d.document_type)} · ${E(d.version)}</small></button>`,
        )
        .join("")
    : '<p class="empty-list">第一篇材料，还在等你。</p>';
  $$("[data-doc]").forEach(
    (b) =>
      (b.onclick = action(async () => {
        if (allowNavigate()) await openDocument(b.dataset.doc);
      })),
  );
}
async function refreshList() {
  const data = await api("/bootstrap");
  state.documents = data.documents;
  state.boot = data;
  sidebar();
  $("#connection").textContent = data.diagnostics.ready
    ? `${data.model} · 本机已就绪`
    : "尚未完成本机配置";
}
function home() {
  state.page = "home";
  state.doc = null;
  state.dirty = false;
  sidebar();
  $("#breadcrumb").textContent = "你的写作工作台";
  $("#content").innerHTML =
    `<div class="hero"><div class="eyebrow">A WORKSPACE THAT REMEMBERS</div><h1>好材料，改出来。<br>好习惯，<em>留下来。</em></h1><p>保存每一稿和每一句修改意见。让 AI 从真实修改中提出写作经验，由你决定哪些值得用于下一篇。</p><div class="hero-actions"><button class="primary" id="hero-new">开始一篇材料 ↗</button><button class="secondary" id="hero-demo">用虚构材料练习</button></div><div class="step-grid"><article class="step-card"><div class="step-number">01 / WRITE</div><h3>写下与修改</h3><p>导入原稿，或提出起草要求。每轮指令都有对应的修改前后文。</p></article><article class="step-card"><div class="step-number">02 / COMPARE</div><h3>比较再采用</h3><p>看清删了什么、加了什么。你采用之前，当前稿保持原样。</p></article><article class="step-card"><div class="step-number">03 / REMEMBER</div><h3>确认后记住</h3><p>偏好需要你确认。按文种和读者应用，随时可以撤销。</p></article></div>${!state.boot?.diagnostics.ready ? '<div class="hint">还没有完成配置。先按「从这里开始」安装引擎并执行 setup；没有密钥时仍可建档和查看材料。</div>' : ""}</div>`;
  $("#hero-new").onclick = openNew;
  $("#hero-demo").onclick = () => openNew(true);
}
const demoText =
  "# 九月经营分析（虚构练习）\n\n## 摘要\n本月收入120万元，同比增长10%；服务客户300家，同比增长15%。\n\n## 一、经营数据\n本月收入120万元，同比增长10%；服务客户300家，同比增长15%。\n\n## 二、下一步建议\n建议进一步分析客户需求，具体措施待补充依据后确定。";
function openNew(demo = false) {
  if (!allowNavigate()) return;
  const f = $("#new-form");
  f.reset();
  if (demo === true) {
    for (const [k, v] of Object.entries({
      title: "练习：九月经营分析",
      document_type: "经营分析",
      audience: "部门负责人",
      purpose: "虚构材料学习练习",
      initial: demoText,
    }))
      f.elements[k].value = v;
  }
  $("#new-dialog").showModal();
}
$("#new-document").onclick = () => openNew();
$("#home").onclick = (e) => {
  e.preventDefault();
  if (allowNavigate()) home();
};
$$("[data-close]").forEach(
  (b) => (b.onclick = () => $("#" + b.dataset.close).close()),
);
$("#initial-file").onchange = action(async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  if (!/\.(md|txt)$/i.test(file.name) || file.size > 700000)
    throw Error("请导入不超过 700 KB 的 UTF-8 .md 或 .txt 文件。");
  const text = new TextDecoder("utf-8", { fatal: true }).decode(
    await file.arrayBuffer(),
  );
  $("#new-form").elements.initial.value = text;
});
$("#new-form").onsubmit = action(async (e) => {
  e.preventDefault();
  const button = $("button[type=submit]", e.target);
  button.disabled = true;
  try {
    const data = Object.fromEntries(new FormData(e.target));
    const doc = await api("/documents", data);
    $("#new-dialog").close();
    await refreshList();
    await openDocument(doc.id);
  } finally {
    button.disabled = false;
  }
});
async function openDocument(id) {
  state.doc = await api("/documents/" + id);
  state.page = "document";
  state.tab = "write";
  state.dirty = false;
  state.editingHash = null;
  hideNotice();
  renderDocument();
  sidebar();
}
async function reloadDocument() {
  if (state.page !== "document" || !state.doc) return;
  const instruction = $("#instruction")?.value || "";
  const keep = $("#keep-requirement")?.checked ?? true;
  const manual = state.dirty ? $("#editor")?.value : null;
  const reason = $("#manual-reason")?.value || "";
  state.doc = await api("/documents/" + state.doc.document_id);
  renderDocument();
  if ($("#instruction")) {
    $("#instruction").value = instruction;
    $("#keep-requirement").checked = keep;
  }
  if (manual !== null && $("#editor")) {
    $("#editor").value = manual;
    $("#manual-reason").value = reason;
  }
  sidebar();
}
function renderDocument() {
  const d = state.doc,
    v = d.versions.at(-1);
  $("#breadcrumb").textContent = d.title;
  $("#content").innerHTML =
    `<div class="page-heading"><div><div class="eyebrow">YOUR DOCUMENT / ${E(v.id)}</div><h1>${E(d.title)}</h1><p class="muted">${E(d.document_type)} · 写给${E(d.audience)}${d.purpose ? " · " + E(d.purpose) : ""}</p></div><button class="secondary" id="export-document">导出当前稿 ↓</button></div><div class="tabs"><button class="tab ${state.tab === "write" ? "active" : ""}" data-tab="write">写作与修改</button><button class="tab ${state.tab === "history" ? "active" : ""}" data-tab="history">版本与原始指令 <span class="badge">${d.versions.length}</span></button><button class="tab ${state.tab === "context" ? "active" : ""}" data-tab="context">本篇要求与记忆 <span class="badge">${d.applicable_rules.length}</span></button></div>${d.recovery_error || d.external_change ? `<div class="hint">${E(d.recovery_error || "检测到 current.md 被外部编辑。为避免覆盖，请先使用原有 record 命令登记，再刷新。")}</div>` : ""}<div id="document-body"></div>`;
  $("#export-document").onclick = () =>
    download(d.title.replace(/[\\/:*?"<>|]/g, "_") + ".md", v.content);
  $$("[data-tab]").forEach(
    (b) =>
      (b.onclick = () => {
        if (state.dirty && !allowNavigate()) return;
        state.dirty = false;
        state.tab = b.dataset.tab;
        renderDocument();
      }),
  );
  if (state.tab === "history") return renderHistory();
  if (state.tab === "context") return renderContext();
  renderWriting();
}
function diffHtml(text) {
  return (
    '<div class="diff">' +
    text
      .split("\n")
      .map(
        (line) =>
          `<div class="${line.startsWith("+") ? "added" : line.startsWith("-") ? "removed" : line.startsWith("@@") ? "range" : ""}">${E(line) || " "}</div>`,
      )
      .join("") +
    "</div>"
  );
}
function draftHTML(draft) {
  if (!draft)
    return '<div class="empty-draft"><div class="empty-symbol">稿</div><h3>下一稿，从你的意见开始</h3><p>在下方写出本轮修改要求。生成的草稿会出现在这里，采用前不会覆盖当前稿。</p></div>';
  return `<div class="panel-body"><div class="draft-meta"><span class="badge pending">${draft.stale ? "基于旧版 · 不能直接采用" : "待你采用"}</span><span class="muted">基于 ${E(draft.base_version)}</span></div><p class="muted">修改要求：${E(draft.instruction)}</p><details open><summary>前后差异</summary>${diffHtml(draft.diff)}</details><details><summary>查看完整草稿</summary><div class="prose">${E(draft.content)}</div></details><details><summary>本次实际使用的记忆（${draft.context.loaded_rules.length}）</summary>${loadedHTML(draft.context.loaded_rules)}<p class="muted">${E(draft.context.model)} · 保留 ${draft.context.history.length} 条历史要求</p></details><div class="panel-actions"><button class="primary" id="adopt-draft" ${draft.stale ? "disabled" : ""}>采用这一稿</button><button class="secondary" id="reject-draft">不采用</button></div><p class="muted">采用后会再调用一次模型提炼候选偏好；不会自动启用。</p></div>`;
}
function renderWriting() {
  const d = state.doc,
    v = d.versions.at(-1),
    draft = [...(d.draft_proposals || [])]
      .reverse()
      .find((x) => x.state === "pending");
  $("#document-body").innerHTML =
    `<div class="workspace-grid"><section class="panel"><div class="panel-head"><h3>当前正式稿</h3><span class="badge">${E(v.id)} · 已保存</span></div><div class="panel-body"><textarea class="document-editor" id="editor" aria-label="当前正文" placeholder="目前没有正文。在下方输入要求，让 AI 起草第一版。">${E(v.content)}</textarea><input id="manual-reason" placeholder="手工修改说明（可选，帮助准确理解修改）" aria-label="手工修改说明"><div class="panel-actions"><button class="secondary" id="save-manual">保存手工修改</button><button class="quiet" id="discard-edit">还原输入</button><span class="muted" id="edit-status">${state.dirty ? "有未保存的输入" : "在这里也可直接编辑正文"}</span></div></div></section><section class="panel"><div class="panel-head"><h3>AI 修改草稿</h3><span class="badge pending">比较后再采用</span></div>${draftHTML(draft)}</section></div><section class="instruction-panel"><label for="instruction">这一轮，你希望怎样写？</label><textarea id="instruction" rows="3" placeholder="例如：摘要改为一句总体判断，具体数据留在正文。不新增未经提供的原因和成果。"></textarea><div class="instruction-actions"><label><input type="checkbox" id="keep-requirement" checked> 采用后，本篇后续修改继续遵循这条要求</label><button class="primary" id="generate">${v.content ? "生成修改草稿" : "根据要求起草"} ↗</button></div><p class="muted">取消勾选表示本轮临时要求。生成会把本次写作上下文发送给你配置的 DeepSeek API。</p></section>`;
  $("#editor").oninput = () => {
    if (!state.dirty) state.editingHash = v.content_hash;
    state.dirty = true;
    $("#edit-status").textContent = "有未保存的输入";
  };
  $("#discard-edit").onclick = () => {
    $("#editor").value = v.content;
    state.dirty = false;
    state.editingHash = null;
    $("#edit-status").textContent = "已还原为保存稿";
  };
  $("#save-manual").onclick = action(async () => {
    if (!state.dirty) throw Error("正文没有手工修改。");
    await api("/documents/" + d.document_id + "/manual", {
      request_id: requestId(),
      content: $("#editor").value,
      instruction: $("#manual-reason").value,
      expected_hash: state.editingHash || v.content_hash,
      actor: actor(),
    });
    state.dirty = false;
    await refreshList();
    await reloadDocument();
    notice("手工修改已保存，候选提炼在后台进行。", true);
  });
  $("#generate").onclick = action(async () => {
    if (
      state.jobs.some(
        (j) =>
          j.document_id === d.document_id &&
          j.kind === "draft" &&
          ["queued", "running"].includes(j.state),
      )
    )
      throw Error("本篇已有生成任务，请先等它完成。");
    if (state.dirty)
      throw Error("请先保存或还原手工输入，再让 AI 修改已保存的版本。");
    const instruction = $("#instruction").value;
    if (!instruction.trim()) throw Error("请先写出本轮要求。");
    await api("/documents/" + d.document_id + "/drafts", {
      request_id: requestId(),
      instruction,
      expected_hash: v.content_hash,
      keep_requirement: $("#keep-requirement").checked,
    });
    notice("已提交。正文保持不变，生成完成后请在右侧比较。", true);
    await pollJobs();
  });
  if (draft) {
    $("#adopt-draft").onclick = action(async () => {
      if (state.dirty) throw Error("请先处理尚未保存的手工输入。");
      await api(
        "/documents/" + d.document_id + "/drafts/" + draft.id + "/adopt",
        { actor: actor() },
      );
      await refreshList();
      await reloadDocument();
      notice("已采用。AI 正在提炼候选偏好，请稍后到「写作记忆」确认。", true);
    });
    $("#reject-draft").onclick = action(async () => {
      await api(
        "/documents/" + d.document_id + "/drafts/" + draft.id + "/reject",
        { actor: actor() },
      );
      await reloadDocument();
      notice("草稿已归档，当前稿未改变。", true);
    });
  }
}
function loadedHTML(rules) {
  return rules.length
    ? rules
        .map(
          (r) =>
            `<p class="scope">${E(r.content)}<br><span class="inline-code">${E(r.id)} · r${r.revision}</span></p>`,
        )
        .join("")
    : '<p class="muted">本次没有加载个人规则。</p>';
}
function renderHistory() {
  const d = state.doc;
  $("#document-body").innerHTML =
    [...d.versions]
      .reverse()
      .map((v) => {
        const turns = d.turns.filter((t) => t.after_version === v.id);
        return `<article class="panel history-card"><div class="panel-body"><div class="history-row"><h3>${E(v.id)} <span class="muted">${date(v.created_at)}</span></h3>${v.id !== d.versions.at(-1).id ? `<button class="secondary" data-restore="${E(v.id)}">以此稿生成新版本</button>` : '<span class="badge">当前稿</span>'}</div>${turns.map((t) => `<p class="muted">${E(t.before_version)} → ${E(t.after_version)} · ${E(t.instruction)}</p><span class="metadata">${E(t.event_id)}</span>`).join("") || '<p class="muted">初始原稿</p>'}<details><summary>展开完整正文</summary><div class="prose">${E(v.content)}</div></details></div></article>`;
      })
      .join("") +
    `<h2>所有 AI 草稿</h2>` +
    ([...(d.draft_proposals || [])]
      .reverse()
      .map(
        (p) =>
          `<article class="panel history-card"><div class="panel-body"><span class="badge">${E({ pending: "待采用", adopted: "已采用", rejected: "未采用" }[p.state])}</span><p>${E(p.instruction)}</p><p class="metadata">${E(p.id)} · ${E(p.base_version)}${p.adopted_version ? " → " + E(p.adopted_version) : ""}</p><details><summary>完整草稿与差异</summary><div class="prose">${E(p.content)}</div>${diffHtml(p.diff)}</details><details><summary>本轮上下文与实际规则</summary>${loadedHTML(p.context.loaded_rules)}<div class="prose metadata">${E(JSON.stringify(p.context, null, 2))}</div></details></div></article>`,
      )
      .join("") || '<p class="muted">还没有生成草稿。</p>');
  $$("[data-restore]").forEach(
    (b) =>
      (b.onclick = action(async () => {
        const v = d.versions.find((x) => x.id === b.dataset.restore);
        await api("/documents/" + d.document_id + "/manual", {
          request_id: requestId(),
          content: v.content,
          instruction: "用户恢复 " + v.id + " 全文",
          expected_hash: d.versions.at(-1).content_hash,
          actor: actor(),
          restore_version: v.id,
        });
        await refreshList();
        await reloadDocument();
        notice("已从旧稿生成新版本，历史没有删除。", true);
      })),
  );
}
function renderContext() {
  const d = state.doc;
  $("#document-body").innerHTML =
    `<div class="hint good">本轮明确要求优先，其次是本篇有效历史要求，再其次是个人偏好。下面的规则是当前适用规则；每次生成时实际加载的快照在「版本与原始指令」中。</div><h2>当前适用的个人记忆</h2>${loadedHTML(d.applicable_rules)}<h2>本篇历史要求</h2><p class="muted">取消后仅停止在后续调用中使用，原始记录仍保留。恢复旧稿的操作说明不作为写作要求。</p>${d.turns.map((t) => `<article class="panel history-card"><div class="panel-body"><label class="check-label"><input type="checkbox" data-requirement="${E(t.event_id)}" ${d.instruction_scopes?.[t.event_id] === false ? "" : "checked"} ${t.source === "web_restore" ? "disabled" : ""}>${E(t.instruction)}</label><small class="muted">${E(t.before_version)} → ${E(t.after_version)} · ${E(t.event_id)}</small></div></article>`).join("") || '<p class="muted">第一次修改后，指令会记录在这里。</p>'}`;
  $$("[data-requirement]").forEach(
    (b) =>
      (b.onchange = action(async () => {
        await api(
          "/documents/" +
            d.document_id +
            "/requirements/" +
            b.dataset.requirement,
          { enabled: b.checked },
        );
        await reloadDocument();
      })),
  );
}
async function openMemory() {
  if (!allowNavigate()) return;
  state.dirty = false;
  state.page = "memory";
  state.memory = await api("/memory");
  sidebar();
  renderMemory();
}
$("#open-memory").onclick = action(openMemory);
function renderMemory() {
  const m = state.memory;
  $("#breadcrumb").textContent = "写作记忆";
  const candidates = m.candidates.filter(
    (c) => !["approve", "reject"].includes(c.personal_decision?.decision),
  );
  const active = m.rules.filter((r) => r.state === "active");
  $("#content").innerHTML =
    `<div class="page-heading"><div><div class="eyebrow">PERSONAL WRITING MEMORY</div><h1>把好习惯，留给下一篇。</h1><p class="muted">候选只是 AI 的建议。你确认后，才会成为个人规则。</p></div><button class="secondary" id="import-memory">导入同事的规则</button></div><div class="section-title"><h2>等你判断 <span class="badge pending">${candidates.length}</span></h2></div><div class="rule-grid">${candidates.map((c) => `<article class="rule-card"><div class="rule-top"><span class="badge pending">${c.personal_decision?.decision === "defer" ? "已暂缓" : "候选偏好"}</span><small class="muted">${date(c.created_at)}</small></div><p class="rule-text">${E(c.content)}</p><div class="scope">${E(scopeText(c.scope))}</div><p class="muted">${E(c.rationale)}</p><button class="secondary" data-candidate="${E(c.id)}">查看依据与处理 →</button></article>`).join("") || '<p class="empty-list">还没有待确认候选。采用一稿后会自动提炼，也可能因证据不足返回零条。</p>'}</div><div class="section-title"><h2>已经确认 <span class="badge">${active.length}</span></h2><button class="secondary" id="export-memory">分享选中规则 ↓</button></div><div class="rule-grid">${active.map((r) => `<article class="rule-card"><div class="rule-top"><label class="check-label"><input type="checkbox" data-share-rule="${E(r.id)}">选择分享</label><span class="badge">r${r.revision} · 生效中</span></div><p class="rule-text">${E(r.content)}</p><div class="scope">${E(scopeText(r.scope))}</div><p class="muted">${E(r.confirmed_by)} 确认 · ${date(r.confirmed_at)}</p><div class="panel-actions"><button class="secondary" data-edit-rule="${E(r.id)}">编辑规则</button><button class="quiet" data-revoke="${E(r.id)}">撤销</button></div></article>`).join("") || '<p class="empty-list">尚无生效规则。不会把一次临时要求擅自当作长期习惯。</p>'}</div><details><summary>已拒绝、已撤销的记录</summary>${m.candidates
      .filter((c) => c.personal_decision?.decision === "reject")
      .map((c) => `<p class="muted">不采纳 · ${E(c.content)}</p>`)
      .join("")}${m.rules
      .filter((r) => r.state === "revoked")
      .map(
        (r) => `<p class="muted">已撤销 · ${E(r.content)} · r${r.revision}</p>`,
      )
      .join("")}</details>`;
  $$("[data-candidate]").forEach(
    (b) =>
      (b.onclick = action(() =>
        openCandidate(m.candidates.find((c) => c.id === b.dataset.candidate)),
      )),
  );
  $$("[data-edit-rule]").forEach(
    (b) =>
      (b.onclick = action(() => {
        const r = m.rules.find((x) => x.id === b.dataset.editRule);
        return openCandidate(
          { ...r.candidate_snapshot, content: r.content, scope: r.scope },
          r.id,
        );
      })),
  );
  $$("[data-revoke]").forEach(
    (b) =>
      (b.onclick = action(async () => {
        await api("/memory/rules/" + b.dataset.revoke + "/revoke", {
          actor: actor(),
        });
        await openMemory();
        notice("已撤销，后续请求不再使用；过往调用快照保持原样。", true);
      })),
  );
  $("#import-memory").onclick = () => $("#import-rules").click();
  $("#export-memory").onclick = action(async () => {
    const ids = $$("[data-share-rule]:checked").map((x) => x.dataset.shareRule);
    const bundle = await api("/memory/export", { ids });
    $("#share-text").value = JSON.stringify(bundle, null, 2);
    $("#share-dialog").showModal();
  });
}
async function openCandidate(candidate, editingRuleId = null) {
  const c = candidate,
    s = { ...c.scope };
  const source = c.source || {};
  if (
    !editingRuleId &&
    source.task_context?.document_type &&
    source.task_context?.audience
  ) {
    s.document_types = [source.task_context.document_type];
    s.audiences = [source.task_context.audience];
  }
  $$(".dialog-notice").forEach((x) => x.remove());
  $("#memory-dialog-content").innerHTML =
    `<div class="eyebrow">YOU HAVE THE FINAL SAY</div><h2>这条经验，值得记住吗？</h2><label>确认人<input id="memory-actor" value="${E($("#actor").value)}" placeholder="你的姓名" maxlength="100"></label><p class="muted">${E(c.rationale || "修改后会生成新的规则修订，过去的调用快照不变。")}</p><label>规则内容<textarea id="rule-content" rows="3" maxlength="4000">${E(c.content)}</textarea></label><div class="form-grid"><label>适用文种<input id="scope-type" value="${E(s.document_types.join("、"))}"></label><label>适用读者<input id="scope-audience" value="${E(s.audiences.join("、"))}"></label></div><label>适用主题<input id="scope-topic" value="${E(s.topics.join("、"))}"></label><p class="muted">多项用「、」分隔。* 表示不限；全部改为 * 才会成为通用偏好。</p><details><summary>查看真实来源</summary>${source.kind === "imported_rule" ? '<p class="muted">由分享包导入，不附带同事的原稿或原始指令。</p>' : `<p class="muted">${E(source.task_context?.title)} · ${E(source.before_version)} → ${E(source.after_version)}</p><p>${E(source.instruction)}</p><div class="prose">修改前：\n${E(source.before_snapshot?.content)}\n\n修改后：\n${E(source.after_snapshot?.content)}</div><p class="metadata">${E(source.event_id)}</p>`}</details><div id="overlaps"></div>${c.reusable !== true || !["method", "preference"].includes(c.category) ? '<div class="hint">此候选属于事实、一次性要求或其他资料，不能确认为长期写作偏好。</div>' : ""}<div class="dialog-actions"><button class="quiet" id="defer-candidate">暂缓</button><button class="secondary" id="reject-candidate">不采纳</button><button class="primary" id="approve-candidate" ${c.reusable !== true || !["method", "preference"].includes(c.category) ? "disabled" : ""}>确认并启用</button></div><div class="dialog-actions"><button class="quiet" id="close-candidate">关闭</button></div>`;
  $("#memory-dialog").showModal();
  let overlapRows = [];
  const memoryActor = () => {
    const name = $("#memory-actor").value.trim();
    if (!name) {
      $("#memory-actor").focus();
      throw Error("请填写确认人姓名。");
    }
    $("#actor").value = name;
    localStorage.setItem("workbench-actor", name);
    return name;
  };
  if (editingRuleId) {
    $("#defer-candidate").hidden = true;
    $("#reject-candidate").hidden = true;
  }
  const value = () => ({
    content: $("#rule-content").value,
    category: c.category,
    scope: {
      document_types: $("#scope-type")
        .value.split("、")
        .map((x) => x.trim())
        .filter(Boolean),
      audiences: $("#scope-audience")
        .value.split("、")
        .map((x) => x.trim())
        .filter(Boolean),
      topics: $("#scope-topic")
        .value.split("、")
        .map((x) => x.trim())
        .filter(Boolean),
    },
  });
  let previewSeq = 0;
  async function preview() {
    const seq = ++previewSeq;
    try {
      const rows = await api("/memory/preview", value());
      if (seq !== previewSeq || !$("#memory-dialog").open) return;
      overlapRows = rows.filter((r) => r.id !== editingRuleId);
      $("#overlaps").innerHTML = overlapRows.length
        ? `<div class="overlap"><strong>以下规则适用范围重叠，请核对是否冲突</strong>${overlapRows.map((r) => `<p>${E(r.content)}</p><label><input type="checkbox" data-replace="${E(r.id)}">用新规则替换这一条（不勾选则共存）</label>`).join("")}<label><input type="checkbox" id="reviewed-overlap">我已核对以上规则及范围</label></div>`
        : "";
    } catch (e) {
      $("#overlaps").textContent = e.message;
    }
  }
  for (const id of [
    "rule-content",
    "scope-type",
    "scope-audience",
    "scope-topic",
  ])
    $("#" + id).addEventListener("change", () => preview());
  $("#approve-candidate").onclick = action(async () => {
    const body = value();
    if (overlapRows.length && !$("#reviewed-overlap")?.checked)
      throw Error("请先核对重叠规则，或缩小适用范围。");
    await api("/memory/candidates/" + c.id, {
      decision: "approve",
      actor: memoryActor(),
      content: body.content,
      scope: body.scope,
      reviewed_overlap_ids: overlapRows.map((r) => r.id),
      replace_ids: $$("[data-replace]:checked").map((x) => x.dataset.replace),
    });
    $("#memory-dialog").close();
    await openMemory();
    notice(
      "偏好已启用。新建同类材料后，可在「本篇要求与记忆」检查是否匹配。",
      true,
    );
  });
  for (const [id, decision] of [
    ["defer-candidate", "defer"],
    ["reject-candidate", "reject"],
  ])
    $("#" + id).onclick = action(async () => {
      await api("/memory/candidates/" + c.id, {
        decision,
        actor: memoryActor(),
      });
      $("#memory-dialog").close();
      await openMemory();
    });
  $("#close-candidate").onclick = () => $("#memory-dialog").close();
  if (["method", "preference"].includes(c.category)) await preview();
}
$("#import-rules").onchange = action(async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  if (file.size > 500000) throw Error("规则包过大。");
  const data = JSON.parse(await file.text());
  await api("/memory/import", data);
  e.target.value = "";
  await openMemory();
  notice("已导入为候选，尚未生效。请逐条确认。", true);
});
$("#download-rules").onclick = action(async () => {
  const data = JSON.parse($("#share-text").value);
  if (data.format !== "rsih-personal-rules" || !Array.isArray(data.rules))
    throw Error("规则包格式不正确，请保留 format 和 rules。");
  const cleaned = {
    format: "rsih-personal-rules",
    schema_version: 1,
    rules: data.rules.map((r) => ({
      content: r.content,
      category: r.category,
      scope: {
        document_types: r.scope.document_types,
        audiences: r.scope.audiences,
        topics: r.scope.topics,
      },
    })),
  };
  download(
    "写作规则分享.json",
    JSON.stringify(cleaned, null, 2),
    "application/json",
  );
  $("#share-dialog").close();
});
const jobNames = { draft: "生成草稿", extract: "提炼候选" };
const jobStates = {
  queued: "排队中",
  running: "处理中",
  succeeded: "已完成",
  failed: "未完成",
  interrupted: "上次中断",
  cancelled: "已取消",
};
let polling = false;
async function pollJobs() {
  if (polling) return;
  polling = true;
  try {
    const rows = await api("/jobs");
    const sig = rows.map((j) => j.id + ":" + j.state).join("|");
    const changed = sig !== state.jobSignature;
    state.jobSignature = sig;
    state.jobs = rows;
    if ($("#generate"))
      $("#generate").disabled = rows.some(
        (j) =>
          j.document_id === state.doc?.document_id &&
          j.kind === "draft" &&
          ["queued", "running"].includes(j.state),
      );
    $("#jobs").innerHTML = rows
      .slice(0, 8)
      .map(
        (j) =>
          `<div class="job ${E(j.state)}">${["running", "queued"].includes(j.state) ? '<span class="spinner"></span>' : ""}<div class="job-text">${E(jobNames[j.kind])} · ${E(jobStates[j.state])}<small>${E(state.documents.find((d) => d.id === j.document_id)?.title || j.document_id)} · ${date(j.created_at)}${j.state === "running" ? " · 等待模型返回完整结果" : ""}${j.error ? " · " + E(j.error) : ""}${j.result?.candidate_ids ? " · " + j.result.candidate_ids.length + " 条候选" : ""}</small></div>${["failed", "interrupted", "cancelled"].includes(j.state) ? `<button class="secondary" data-retry="${E(j.id)}">重试（可能计费）</button>` : ""}${j.state === "queued" ? `<button class="quiet" data-cancel="${E(j.id)}">取消</button>` : ""}</div>`,
      )
      .join("");
    $$("[data-retry]").forEach(
      (b) =>
        (b.onclick = action(async () => {
          await api("/jobs/" + b.dataset.retry + "/retry", {});
          await pollJobs();
        })),
    );
    $$("[data-cancel]").forEach(
      (b) =>
        (b.onclick = action(async () => {
          await api("/jobs/" + b.dataset.cancel + "/cancel", {});
          await pollJobs();
        })),
    );
    if (changed) {
      await refreshList();
      if (state.page === "document") await reloadDocument();
      if (state.page === "memory" && !$("#memory-dialog").open) {
        state.memory = await api("/memory");
        renderMemory();
      }
    }
  } catch (e) {
    $("#connection").textContent = "连接已断开，请检查终端中的服务";
  } finally {
    polling = false;
  }
}
function guide() {
  if (!allowNavigate()) return;
  state.page = "guide";
  state.dirty = false;
  sidebar();
  $("#breadcrumb").textContent = "从这里开始";
  $("#content").innerHTML =
    `<div class="guide"><div class="eyebrow">LEARN BY DOING / 六个小练习</div><h1>从第一次修改，到真正用上记忆。</h1><p class="muted">先用虚构材料完成练习，再进入日常写作。完整教程在项目 docs/GETTING_STARTED.md。</p><article><h2>01 / 认识你的工具</h2><p>DeepSeek 负责写；RSIH 根据 Genome（工作说明书）调用它；工作台负责版本和记忆。这里改变的是模型每次收到的规则，不是训练模型权重。</p></article><article><h2>02 / 配置一次，以后直接启动</h2><p>终端进入项目目录，先安装，再配置你自己的 API 密钥：</p><pre><code>./scripts/install-macos.sh\n.venv/bin/writing-memory-rsih setup\n.venv/bin/writing-memory-rsih web</code></pre><p>如果已安装引擎，可用 setup --rsih 引擎完整路径。输入密钥时不显示字符是正常现象。doctor 只检查本机文件，第一次生成才会真正验证网络与额度。</p></article><article><h2>03 / 做第一轮修改</h2><p>新建材料，文种填写「经营分析」、读者填写「部门负责人」。在首页选择「用虚构材料练习」，提出：</p><div class="hint">摘要改成一句总体判断，具体数据留在正文，不编造原因和成果。</div><p>检查草稿中的增删，满意后填写右上角确认人并采用。采用后才成为正式版本；自动提炼会再调用一次模型。</p></article><article><h2>04 / 找到每次修改的来处</h2><p>「版本与原始指令」保存每份正文、原始要求及草稿；恢复旧稿会生成新版本。「本篇要求与记忆」可停止沿用某条旧要求，原记录仍在。</p></article><article><h2>05 / 验证偏好真的生效</h2><p>进入「写作记忆」，核对来源后确认候选。新建同文种、同读者的第二篇材料，在「本篇要求与记忆」检查匹配情况。再生成一稿，查看调用快照和实际正文。加载规则与遵守规则是两个不同的验证点。</p><p>候选为零条可能表示证据不足；不要为了看到“学会了”强行制造规则。</p></article><article><h2>06 / 理解 RSI，并分享你的方法</h2><p>确认偏好 → 生成派生 Genome → 下一次调用应用 → 检查效果 → 修改或撤销，这就是本工作台的改进循环。原版 <code>rsih</code> 是 Agent 入口，<code>gee</code> 用于根据历史辅助生成 Genome，它不是后台自动训练器。</p><p>分享时只选择规则并核对文字。不要把包含密钥和材料的整个个人数据目录发给同事。</p></article><article><h2>遇到问题怎么办</h2><p>401：密钥不正确或失效；余额/限流：检查 DeepSeek 开放平台；回复截断：缩小材料或核实输出上限；本地上下文预算不足：拆分材料，历史不会被自动裁掉。</p><p>任务失败先看下方记录。点击重试可能再次计费；重启服务不会偷偷重新发送未明确完成的请求。个人数据保存在 <code>~/.local/share/rsih-writing-lab/</code>，更新代码不会搬走它。</p></article></div>`;
}
$("#open-guide").onclick = guide;
(async () => {
  try {
    await refreshList();
    home();
    await pollJobs();
    setInterval(pollJobs, 2000);
  } catch (e) {
    home();
    notice(e.message);
  }
})();
