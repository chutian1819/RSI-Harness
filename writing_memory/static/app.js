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
  newReferences: [],
  selectedReferences: new Set(),
  uploading: false,
  composers: new Map(),
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
  if (state.uploading) {
    notice("资料正在解析，请完成后再离开。");
    return false;
  }
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
    `<div class="home-layout"><div class="hero"><div class="eyebrow">RSI / RECURSIVE SELF-IMPROVEMENT</div><h1>从资料到成稿，<br>从反馈到<em>更懂你。</em></h1><p>让 AI 写好这一篇，也留下写好下一篇的经验。<br>汇集参考资料、提出要求，把你确认的写作习惯带进每一次创作。</p><div class="hero-actions"><button class="primary" id="hero-new">开始写作 ↗</button><button class="secondary" id="hero-sources">上传资料生成</button></div><div class="format-strip"><span>PDF</span><span>PPT / PPTX</span><span>DOCX</span><span>图片 OCR</span><i>→</i><strong>Markdown</strong></div></div><aside class="rsi-loop" aria-label="RSI 改进循环"><div class="loop-top"><span>THE RSI LOOP</span><span class="live-dot">由你掌握</span></div><h2>每一轮反馈，<br>都有下一次回响。</h2><div class="loop-step"><b>01</b><div>生成与修改<small>参考资料 + 你的要求 + 适用习惯</small></div><span>↘</span></div><div class="loop-step"><b>02</b><div>比较与采用<small>保留原始指令和每一份版本</small></div><span>↓</span></div><div class="loop-step"><b>03</b><div>提炼与确认<small>你决定哪些经验值得留下</small></div><span>↙</span></div><div class="loop-return">↻ 应用于下一次写作</div><p>改进的是工作规则与上下文，<br>不是自动训练模型。</p></aside></div><div class="entry-heading"><h2>选择你的起点</h2><button class="quiet" id="hero-demo">使用虚构示例 ↗</button></div><div class="entry-grid"><button class="entry-card" data-start="blank"><span>01 / CREATE</span><h3>从零写一篇</h3><p>说清想写什么、写给谁，<br>把一个想法变成第一版。</p><b>开始起草 ↗</b></button><button class="entry-card" data-start="sources"><span>02 / SYNTHESIZE</span><h3>把多份资料变成一篇</h3><p>上传报告、演示和图片，<br>围绕你的要求组织内容。</p><b>添加参考资料 ↗</b></button><button class="entry-card" data-start="revise"><span>03 / REFINE</span><h3>让已有稿更进一步</h3><p>导入原稿并追加要求，<br>比较差异，确认后再采用。</p><b>导入原稿 ↗</b></button></div><div class="home-note"><span>你的经验，可以带走。</span><p>在「写作习惯」导出 Markdown，交给其他 Agent 读取；导出 JSON，在另一个 RSI 工作台中继续使用。</p><button class="quiet" id="hero-memory">管理写作习惯 →</button></div>${!state.boot?.diagnostics.ready ? '<div class="hint">首次使用请先按「使用指南」完成模型配置。未配置时仍可上传资料、建档和查看材料。</div>' : ""}`;
  $("#hero-new").onclick = () => openNew();
  $("#hero-sources").onclick = () => openNew(false, "sources");
  $("#hero-memory").onclick = action(openMemory);
  $$("[data-start]").forEach(
    (b) => (b.onclick = () => openNew(false, b.dataset.start)),
  );
  $("#hero-demo").onclick = () => openNew(true);
}
const demoText =
  "# 九月经营分析（虚构示例）\n\n## 摘要\n本月收入120万元，同比增长10%；服务客户300家，同比增长15%。\n\n## 一、经营数据\n本月收入120万元，同比增长10%；服务客户300家，同比增长15%。\n\n## 二、下一步建议\n建议进一步分析客户需求，具体措施待补充依据后确定。";
function openNew(demo = false, mode = "blank") {
  if (!allowNavigate()) return;
  const f = $("#new-form");
  f.reset();
  $("#new-form button[type=submit]").textContent = "创建并进入写作 →";
  state.newReferences = [];
  $("#new-reference-list").innerHTML = "";
  $$(".dialog-notice", $("#new-dialog")).forEach((n) => n.remove());
  setNewMode(demo === true ? "revise" : mode);
  if (demo === true) {
    for (const [k, v] of Object.entries({
      title: "示例：九月经营分析",
      document_type: "经营分析",
      audience: "部门负责人",
      purpose: "虚构材料流程演示",
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
  (b) =>
    (b.onclick = () => {
      if (!state.uploading) $("#" + b.dataset.close).close();
    }),
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
    if (state.uploading) throw Error("资料仍在解析，请稍等完成后再创建。");
    const data = Object.fromEntries(new FormData(e.target));
    data.reference_ids = state.newReferences.map((r) => r.id);
    const instruction = $("#first-instruction").value;
    if (state.newMode !== "revise") data.initial = "";
    const doc = await api("/documents", data);
    $("#new-dialog").close();
    await refreshList();
    await openDocument(doc.id);
    $("#instruction").value = instruction;
    state.composers.set(doc.id, { instruction, keep: true });
    if (instruction.trim()) {
      await submitDraft();
    }
  } finally {
    button.disabled = false;
  }
});
function setNewMode(mode) {
  state.newMode = mode;
  $("#initial-section").hidden = mode !== "revise";
  $("#new-references-section").hidden = false;
  $$("[data-mode]").forEach((b) => {
    b.classList.toggle("active", b.dataset.mode === mode);
    b.setAttribute("aria-pressed", String(b.dataset.mode === mode));
  });
}
$$("[data-mode]").forEach(
  (b) => (b.onclick = () => setNewMode(b.dataset.mode)),
);
$("#first-instruction").oninput = () => {
  $("#new-form button[type=submit]").textContent = $(
    "#first-instruction",
  ).value.trim()
    ? "创建并生成第一稿 ↗"
    : "创建并进入写作 →";
};
function referenceRows(rows, selectable = false) {
  return rows
    .map(
      (r) =>
        `<article class="reference-row"><div class="reference-title">${selectable ? `<input type="checkbox" data-reference="${E(r.id)}" aria-label="使用 ${E(r.name)}" ${state.selectedReferences.has(r.id) ? "checked" : ""}>` : ""}<strong>${E(r.name)}</strong><span class="badge">${(r.text_bytes / 1024).toFixed(1)} KB 文字</span>${!selectable ? `<button type="button" class="quiet" data-remove-reference="${E(r.id)}" aria-label="移除 ${E(r.name)}">×</button>` : ""}</div><details><summary>查看解析文字${r.warnings.length ? "与提示" : ""}</summary>${r.warnings.map((w) => `<p class="parse-warning">${E(w)}</p>`).join("")}<div class="prose reference-text">${E(r.text)}</div></details></article>`,
    )
    .join("");
}
function renderNewReferences() {
  $("#new-reference-list").innerHTML = referenceRows(state.newReferences);
  $$("[data-remove-reference]").forEach(
    (b) =>
      (b.onclick = () => {
        if (state.uploading) return;
        state.newReferences = state.newReferences.filter(
          (r) => r.id !== b.dataset.removeReference,
        );
        renderNewReferences();
      }),
  );
}
async function uploadFiles(files, isNew) {
  if (state.uploading) throw Error("正在解析上一批资料，请稍等。");
  const existing = isNew ? state.newReferences : state.doc.references;
  if (existing.length + files.length > 30)
    throw Error("每篇最多 30 份参考资料，请减少文件数量。");
  const docId = state.doc?.document_id;
  state.uploading = true;
  const errors = [];
  const added = [];
  const status = document.createElement("p");
  status.className = "upload-status";
  status.setAttribute("role", "status");
  (isNew ? $("#new-reference-list") : $("#reference-list")).before(status);
  try {
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      status.textContent = `正在本地解析 ${i + 1} / ${files.length}：${file.name}，请保持页面打开…`;
      try {
        if (file.size > 20 * 1024 * 1024)
          throw Error("超过 20 MB，请拆分后上传");
        const response = await fetch(
          "/api/references?name=" + encodeURIComponent(file.name),
          {
            method: "POST",
            headers: {
              "X-Workbench-Token":
                sessionStorage.getItem("workbench-token") || "",
              "Content-Type": "application/octet-stream",
            },
            body: file,
          },
        );
        const row = await response.json();
        if (!response.ok)
          throw Error(
            typeof row.detail === "string" ? row.detail : "文件解析失败",
          );
        added.push(row);
        if (isNew && !state.newReferences.some((r) => r.id === row.id)) {
          state.newReferences.push(row);
          renderNewReferences();
        }
      } catch (e) {
        errors.push(file.name + "：" + e.message);
      }
    }
    if (!isNew && added.length) {
      await api("/documents/" + docId + "/references", {
        ids: [...new Set(added.map((r) => r.id))],
      });
      added.forEach((r) => state.selectedReferences.add(r.id));
      await reloadDocument();
    }
    if (errors.length)
      notice(
        `已解析 ${added.length} 份；以下文件未导入：\n${errors.join("\n")}`,
      );
    else if (added.length)
      notice(`已解析 ${added.length} 份资料。请预览文字，确认后再生成。`, true);
  } finally {
    status.remove();
    state.uploading = false;
  }
}
function bindDrop(zone, isNew) {
  zone.ondragover = (e) => {
    e.preventDefault();
    zone.classList.add("dragging");
  };
  zone.ondragleave = () => zone.classList.remove("dragging");
  zone.ondrop = action(async (e) => {
    e.preventDefault();
    zone.classList.remove("dragging");
    await uploadFiles([...e.dataTransfer.files], isNew);
  });
}
$("#new-references").onchange = action(async (e) => {
  await uploadFiles([...e.target.files], true);
  e.target.value = "";
});
bindDrop($("#new-drop"), true);
$("#new-dialog").addEventListener("cancel", (e) => {
  if (state.uploading) e.preventDefault();
});
function referencePanel() {
  const rows = state.doc.references || [];
  return `<section class="reference-panel"><div class="composer-heading"><div><div class="eyebrow">01 / SOURCES</div><h3>本篇参考资料 <span class="badge">${rows.length}</span></h3></div><label class="upload-button">＋ 添加资料<input id="document-references" type="file" multiple accept=".pdf,.ppt,.pptx,.docx,.md,.txt,.png,.jpg,.jpeg,.webp" aria-label="添加本篇参考资料"></label></div><div class="drop-zone compact-drop" id="document-drop">拖入多份 PDF、PPT / PPTX、Word、文本或图片 · 单份 ≤ 20 MB</div><div id="reference-list">${rows.length ? referenceRows(rows, true) : '<p class="muted">还没有参考资料。可以直接从零起草，也可以先上传资料，再提出写作要求。</p>'}</div><p class="muted" id="reference-budget"></p></section>`;
}
function updateReferenceBudget() {
  const size = (state.doc.references || [])
    .filter((r) => state.selectedReferences.has(r.id))
    .reduce((n, r) => n + r.text_bytes, 0);
  $("#reference-budget").textContent =
    `本轮选择 ${state.selectedReferences.size} 份 · 文字 ${(size / 1024).toFixed(1)} KB。完整上下文预算 ${Math.floor(state.doc.max_context_bytes / 1024)} KB，还包括正文、要求和习惯。超出时会停止生成，不会悄悄截断资料。`;
}
function bindReferencePanel() {
  $("#document-references").onchange = action(async (e) => {
    await uploadFiles([...e.target.files], false);
  });
  bindDrop($("#document-drop"), false);
  $$("[data-reference]").forEach(
    (input) =>
      (input.onchange = () => {
        if (input.checked)
          state.selectedReferences.add(input.dataset.reference);
        else state.selectedReferences.delete(input.dataset.reference);
        updateReferenceBudget();
      }),
  );
  updateReferenceBudget();
}
async function submitDraft() {
  const d = state.doc,
    v = d.versions.at(-1);
  if (state.uploading) throw Error("资料正在解析，请完成后再生成。");
  if (
    state.jobs.some(
      (j) =>
        j.document_id === d.document_id &&
        j.kind === "draft" &&
        ["queued", "running"].includes(j.state),
    )
  )
    throw Error("本篇已有生成任务，请先等它完成。");
  if (state.dirty) throw Error("请先保存或还原手工输入，再生成草稿。");
  const instruction = $("#instruction").value;
  if (!instruction.trim()) throw Error("请先写出本轮要求。");
  await api("/documents/" + d.document_id + "/drafts", {
    request_id: requestId(),
    instruction,
    expected_hash: v.content_hash,
    keep_requirement: $("#keep-requirement").checked,
    reference_ids: [...state.selectedReferences],
  });
  notice("已提交生成任务。结果将出现在 AI 草稿中，检查后即可采用。", true);
  await pollJobs();
}

async function openDocument(id) {
  state.doc = await api("/documents/" + id);
  state.selectedReferences = new Set(state.doc.reference_ids || []);
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
    `<div class="page-heading"><div><div class="eyebrow">YOUR DOCUMENT / ${E(v.id)}</div><h1>${E(d.title)}</h1><p class="muted">${E(d.document_type)} · 写给${E(d.audience)}${d.purpose ? " · " + E(d.purpose) : ""}</p></div><button class="secondary" id="export-document">导出 Markdown ↓</button></div><div class="tabs"><button class="tab ${state.tab === "write" ? "active" : ""}" data-tab="write">写作与修改</button><button class="tab ${state.tab === "history" ? "active" : ""}" data-tab="history">版本与原始指令 <span class="badge">${d.versions.length}</span></button><button class="tab ${state.tab === "context" ? "active" : ""}" data-tab="context">本篇要求与记忆 <span class="badge">${d.applicable_rules.length}</span></button></div>${d.recovery_error || d.external_change ? `<div class="hint">${E(d.recovery_error || "检测到 current.md 被外部编辑。为避免覆盖，请先使用原有 record 命令登记，再刷新。")}</div>` : ""}<div id="document-body"></div>`;
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
    return '<div class="empty-draft"><div class="empty-symbol">稿</div><h3>下一稿，从你的要求开始</h3><p>在上方添加资料，写出起草或修改要求。生成的草稿会出现在这里，采用前不会覆盖当前稿。</p></div>';
  return `<div class="panel-body"><div class="draft-meta"><span class="badge pending">${draft.stale ? "基于旧版 · 不能直接采用" : "待你采用"}</span><span class="muted">基于 ${E(draft.base_version)}</span></div><p class="muted">修改要求：${E(draft.instruction)}</p><details open><summary>前后差异</summary>${diffHtml(draft.diff)}</details><details><summary>查看完整草稿</summary><div class="prose">${E(draft.content)}</div></details><details><summary>本次实际使用的资料与习惯</summary><p class="muted">参考资料：${E((draft.context.references || []).map((r) => r.name).join("、") || "未使用")}</p>${loadedHTML(draft.context.loaded_rules)}<p class="muted">${E(draft.context.model)} · 保留 ${draft.context.history.length} 条历史要求</p></details><div class="panel-actions"><button class="primary" id="adopt-draft" ${draft.stale ? "disabled" : ""}>采用这一稿</button><button class="secondary" id="reject-draft">不采用</button></div><p class="muted">采用后会再调用一次模型提炼候选偏好；不会自动启用。</p></div>`;
}
function renderWriting() {
  const d = state.doc,
    v = d.versions.at(-1),
    draft = [...(d.draft_proposals || [])]
      .reverse()
      .find((x) => x.state === "pending");
  $("#document-body").innerHTML =
    `${referencePanel()}<section class="instruction-panel"><div class="composer-heading"><label for="instruction">${v.content ? "追加要求，让这一稿更进一步" : "你希望写一篇什么内容？"}</label><span class="badge">输出 .md</span></div><textarea id="instruction" rows="4" maxlength="20000" placeholder="请根据所选参考资料，生成一篇……。写给谁、多少字、什么结构、需要强调什么，都可以告诉我。也可以不上传资料，直接从零起草。"></textarea><div class="instruction-actions"><label><input type="checkbox" id="keep-requirement" checked> 采用后，本篇继续沿用这条要求</label><button class="primary" id="generate">${v.content ? "生成修改草稿" : "生成第一稿"} ↗</button></div><p class="muted">取消勾选可作为本轮临时要求。生成时会发送所选资料文字、正文、有效历史要求与适用习惯给配置的模型。</p></section><div class="workspace-grid"><section class="panel"><div class="panel-head"><h3>当前正式稿</h3><span class="badge">${E(v.id)} · 已保存</span></div><div class="panel-body"><textarea class="document-editor" id="editor" aria-label="当前正文" placeholder="目前没有正文。在上方输入要求，让 AI 起草第一版。">${E(v.content)}</textarea><input id="manual-reason" placeholder="手工修改说明（可选，帮助准确理解修改）" aria-label="手工修改说明"><div class="panel-actions"><button class="secondary" id="save-manual">保存手工修改</button><button class="quiet" id="discard-edit">还原输入</button><span class="muted" id="edit-status">${state.dirty ? "有未保存的输入" : "在这里也可直接编辑正文"}</span></div></div></section><section class="panel"><div class="panel-head"><h3>AI 草稿</h3><span class="badge pending">比较后再采用</span></div>${draftHTML(draft)}</section></div>`;
  bindReferencePanel();
  const composer = state.composers.get(d.document_id);
  if (composer) {
    $("#instruction").value = composer.instruction;
    $("#keep-requirement").checked = composer.keep;
  }
  const rememberComposer = () =>
    state.composers.set(d.document_id, {
      instruction: $("#instruction").value,
      keep: $("#keep-requirement").checked,
    });
  $("#instruction").oninput = rememberComposer;
  $("#keep-requirement").onchange = rememberComposer;
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
  $("#generate").onclick = action(submitDraft);
  if (draft) {
    $("#adopt-draft").onclick = action(async () => {
      if (state.dirty) throw Error("请先处理尚未保存的手工输入。");
      await api(
        "/documents/" + d.document_id + "/drafts/" + draft.id + "/adopt",
        { actor: actor() },
      );
      await refreshList();
      await reloadDocument();
      notice("已采用。AI 正在提炼候选偏好，请稍后到「写作习惯」确认。", true);
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
  $("#breadcrumb").textContent = "写作习惯";
  const candidates = m.candidates.filter(
    (c) => !["approve", "reject"].includes(c.personal_decision?.decision),
  );
  const active = m.rules.filter((r) => r.state === "active");
  $("#content").innerHTML =
    `<div class="page-heading"><div><div class="eyebrow">PERSONAL WRITING MEMORY</div><h1>把好习惯，留给下一篇。</h1><p class="muted">候选只是 AI 的建议。你确认后，才会成为个人规则。</p></div><button class="secondary" id="import-memory">导入同事的规则</button></div><div class="section-title"><h2>等你判断 <span class="badge pending">${candidates.length}</span></h2></div><div class="rule-grid">${candidates.map((c) => `<article class="rule-card"><div class="rule-top"><span class="badge pending">${c.personal_decision?.decision === "defer" ? "已暂缓" : "候选偏好"}</span><small class="muted">${date(c.created_at)}</small></div><p class="rule-text">${E(c.content)}</p><div class="scope">${E(scopeText(c.scope))}</div><p class="muted">${E(c.rationale)}</p><button class="secondary" data-candidate="${E(c.id)}">查看依据与处理 →</button></article>`).join("") || '<p class="empty-list">还没有待确认候选。采用一稿后会自动提炼，也可能因证据不足返回零条。</p>'}</div><div class="section-title"><h2>已经确认 <span class="badge">${active.length}</span></h2><button class="secondary" id="export-memory">导出选中习惯 ↓</button></div><div class="rule-grid">${active.map((r) => `<article class="rule-card"><div class="rule-top"><label class="check-label"><input type="checkbox" data-share-rule="${E(r.id)}">选择导出</label><span class="badge">r${r.revision} · 生效中</span></div><p class="rule-text">${E(r.content)}</p><div class="scope">${E(scopeText(r.scope))}</div><p class="muted">${E(r.confirmed_by)} 确认 · ${date(r.confirmed_at)}</p><div class="panel-actions"><button class="secondary" data-edit-rule="${E(r.id)}">编辑规则</button><button class="quiet" data-revoke="${E(r.id)}">撤销</button></div></article>`).join("") || '<p class="empty-list">尚无生效规则。不会把一次临时要求擅自当作长期习惯。</p>'}</div><details><summary>已拒绝、已撤销的记录</summary>${m.candidates
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
async function cleanedExport() {
  return api("/memory/export-agent", JSON.parse($("#share-text").value));
}
$("#download-rules").onclick = action(async () => {
  const result = await cleanedExport();
  download(
    "RSI-写作习惯.json",
    JSON.stringify(result.bundle, null, 2),
    "application/json",
  );
  notice("JSON 已导出。在另一个 RSI 工作台导入后，需要逐条确认。", true);
});
$("#download-agent").onclick = action(async () => {
  const result = await cleanedExport();
  download(result.filename, result.content, "text/markdown;charset=utf-8");
  notice(
    "Markdown 已导出，文件内附使用说明和可复制的开场指令。将它交给 Agent，并明确要求先读取再写作。",
    true,
  );
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
    if (changed && !state.uploading) {
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
  $("#breadcrumb").textContent = "使用指南";
  $("#content").innerHTML =
    `<div class="guide"><div class="eyebrow">WORKSPACE GUIDE / 使用指南</div><h1>从第一次修改，到真正用上记忆。</h1><p class="muted">以下说明涵盖写作、版本管理和习惯复用。示例内容均为虚构；完整使用指南见项目 docs/GETTING_STARTED.md。</p><article><h2>01 / 工作原理与分工</h2><p>DeepSeek 负责写；RSIH 根据 Genome（工作说明书）调用它；工作台负责版本和记忆。这里改变的是模型每次收到的规则，不是训练模型权重。</p></article><article><h2>02 / 配置一次，以后直接启动</h2><p>终端进入项目目录，先安装，再配置你自己的 API 密钥：</p><pre><code>./scripts/install-macos.sh\n.venv/bin/writing-memory-rsih setup\n.venv/bin/writing-memory-rsih web</code></pre><p>如果已安装引擎，可用 setup --rsih 引擎完整路径。输入密钥时不显示字符是正常现象。doctor 只检查本机文件，第一次生成才会真正验证网络与额度。</p></article><article><h2>03 / 从资料生成，或做第一轮修改</h2><p>首页选择「根据资料生成」，可一次上传 PDF、PPTX、DOCX、文本和图片。解析在本机进行，预览文字后填写写作要求，即可从空白生成 Markdown。旧 .ppt 需本机 LibreOffice，或先另存为 .pptx。图片和扫描页识别的是文字，图表含义仍需补充说明。每轮可追加资料和要求，并勾选本次要用的资料。</p><p>新建材料，文种填写「经营分析」、读者填写「部门负责人」。在首页选择「使用虚构示例」，提出：</p><div class="hint">摘要改成一句总体判断，具体数据留在正文，不编造原因和成果。</div><p>检查草稿中的增删，满意后填写右上角确认人并采用。采用后才成为正式版本；自动提炼会再调用一次模型。</p></article><article><h2>04 / 找到每次修改的来处</h2><p>「版本与原始指令」保存每份正文、原始要求及草稿；恢复旧稿会生成新版本。「本篇要求与记忆」可停止沿用某条旧要求，原记录仍在。</p></article><article><h2>05 / 验证偏好真的生效</h2><p>进入「写作习惯」，核对来源后确认候选。新建同文种、同读者的第二篇材料，在「本篇要求与记忆」检查匹配情况。再生成一稿，查看调用快照和实际正文。加载规则与遵守规则是两个不同的验证点。</p><p>候选为零条可能表示证据不足；不要为了看到“学会了”强行制造规则。</p></article><article><h2>06 / 理解 RSI，并分享你的方法</h2><p>确认偏好 → 生成派生 Genome → 下一次调用应用 → 检查效果 → 修改或撤销，这就是本工作台的改进循环。原版 <code>rsih</code> 是 Agent 入口，<code>gee</code> 用于根据历史辅助生成 Genome，它不是后台自动训练器。</p><p>在「写作习惯」勾选已确认规则，导出 Markdown 给其他 Agent 阅读，或导出 JSON 给另一个 RSI 工作台导入。Markdown 内附开场指令：先读取文件，说明文种和读者，列出适用规则，再写作。导出不会自动改变其他 Agent 的记忆；规则更新后需重新导出。分享前核对规则文字中的业务信息。</p></article><article><h2>遇到问题怎么办</h2><p>401：密钥不正确或失效；余额/限流：检查 DeepSeek 开放平台；回复截断：缩小材料或核实输出上限；本地上下文预算不足：拆分材料，历史不会被自动裁掉。</p><p>任务失败先看下方记录。点击重试可能再次计费；重启服务不会偷偷重新发送未明确完成的请求。个人数据保存在 <code>~/.local/share/rsih-writing-lab/</code>，更新代码不会搬走它。</p></article></div>`;
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
