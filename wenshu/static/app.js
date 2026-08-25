const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const overviewEl = $("#overview-cards");
const healthEl = $("#health-grid");
const workflowEl = $("#workflow-steps");
const tableListEl = $("#table-list");
const columnListEl = $("#column-list");
const columnHintEl = $("#column-hint");
const tableCountEl = $("#table-count");
const stagingStatsEl = $("#staging-stats");
const stagingPendingOverviewEl = $("#staging-pending-overview");
const searchResultEl = $("#search-result");
let stagingLlmProgress = null;
const jobsListEl = $("#jobs-list");
const vectorLogEl = $("#vector-log");

const META_STEP_IDS = ["init", "scan", "review", "sync", "index"];
const META_STEP_DESC = {
  init: "建系统表",
  scan: "读源库结构",
  review: "补全注释",
  sync: "写入元数据库",
  index: "Embedding 入库",
};
const PIPELINE_STEPS = {
  init: ["提交任务", "创建系统表", "刷新状态"],
  scan: ["提交任务", "扫描表结构", "刷新状态"],
  sync: ["提交任务", "写入元数据库", "刷新状态"],
  index: ["提交任务", "加载对象", "Embedding", "写入 Qdrant", "刷新状态"],
};
const NEXT_TAB_AFTER = {
  index: "search",
};

let currentStagingTableId = null;
let currentStagingTable = null;
let stagingTableCache = [];
let rawTableScopeCache = [];
let vectorTableScopeCache = [];
let lastWorkflow = null;

/** 扫描：暂存区有数据视为已完成；本会话刚扫描也标完成 */
let metaScanDone = false;

function resetMetaScanSession() {
  metaScanDone = false;
}

function getMetadataCycleState(wf) {
  const staging = wf.staging || {};
  const l1Count = wf.health?.meta_db?.table_count || 0;
  const hasStaging = (staging.staging_table_count || 0) > 0;
  const reviewComplete = !!staging.review_complete;
  const syncedIdle = l1Count > 0 && !hasStaging;
  return {
    l1Count,
    hasStaging,
    reviewComplete,
    syncedIdle,
    reviewDone: reviewComplete || syncedIdle,
    syncDone: l1Count > 0 && (reviewComplete || !hasStaging),
  };
}

function formatSyncClearMessage(result) {
  if (!result?.staging_cleared) return "";
  const t = result.staging_cleared_tables ?? 0;
  const c = result.staging_cleared_columns ?? 0;
  return `暂存区已清空（${t} 表 / ${c} 字段）；再次维护请重新扫描。`;
}

function getMetaWorkflowSteps(wf) {
  const staging = wf.staging || {};
  const health = wf.health || {};
  const cycle = getMetadataCycleState(wf);
  const connReady =
    health.connections?.ok && health.raw_db?.ok && health.meta_db?.ok;
  const metaReady = !!health.meta_db?.metadata_ready;

  return META_STEP_IDS.map((id) => {
    const base = wf.steps.find((s) => s.id === id) || { id, title: id, desc: "" };
    const step = { ...base };
    if (id === "init") {
      step.done = metaReady;
      step.ready = !!connReady && !metaReady;
    } else if (id === "scan") {
      step.done = metaScanDone || cycle.hasStaging;
      step.ready = metaReady;
    } else if (id === "review") {
      step.done = cycle.reviewDone;
      step.ready = cycle.hasStaging;
    } else if (id === "sync") {
      step.done = cycle.syncDone;
      step.ready = cycle.hasStaging && cycle.reviewComplete;
    } else if (id === "index") {
      const qdrant = wf.health?.qdrant || {};
      const hasVectors = !!qdrant.ok && (qdrant.points || 0) > 0;
      step.done = cycle.syncDone && hasVectors;
      step.ready = cycle.syncDone && !!wf.health?.embedding?.ok;
    }
    return step;
  });
}

function getMetaCurrentStepId(steps) {
  for (const s of steps) {
    if (!s.done) return s.id;
  }
  return steps[steps.length - 1]?.id;
}

let wizardSteps = [];
let connRoleIndex = 0;
let connCurrentRole = null;
let connCurrentEngine = null;
let connValuesCache = {};

const REFRESH_STEPS = [
  "加载连接配置",
  "获取概览统计",
  "检测环境与工作流",
  "加载元数据暂存",
  "加载任务历史",
];

let loadingDepth = 0;
let loadingAbortController = null;
let loadingDismissResolver = null;

function resetLoadingResultMode() {
  const dialog = document.querySelector(".loading-dialog");
  dialog?.classList.remove(
    "loading-result",
    "loading-result-ok",
    "loading-result-error",
    "loading-result-warn"
  );
  const spinner = document.querySelector(".loading-spinner");
  if (spinner) spinner.style.display = "";
  const dismiss = $("#loading-dismiss");
  if (dismiss) dismiss.style.display = "none";
}

function formatErrorMessage(message) {
  if (!message) return "";
  const text = String(message).trim();
  if (text.length <= 500 && text.split("\n").length <= 10) return text;
  const lines = text.split("\n");
  const summary =
    [...lines]
      .reverse()
      .find((line) =>
        /(?:Error|Exception|IntegrityError|OperationalError|Cannot|foreign key|Duplicate|Data too long|ValueError)/i.test(
          line
        )
      ) ||
    lines.find((line) => line.trim() && !line.trim().startsWith("File ")) ||
    lines[lines.length - 1];
  return `【摘要】${(summary || "未知错误").trim()}\n\n【完整错误】\n${text}`;
}

function setLoadingResultMode(message, variant = "ok", title) {
  const dialog = document.querySelector(".loading-dialog");
  if (dialog) {
    dialog.classList.add("loading-result", `loading-result-${variant}`);
  }
  const spinner = document.querySelector(".loading-spinner");
  if (spinner) spinner.style.display = "none";
  if (title) {
    const titleEl = $("#loading-title");
    if (titleEl) titleEl.textContent = title;
  }
  const displayMessage =
    variant === "error" && !String(message).includes("【完整错误】")
      ? formatErrorMessage(message)
      : message;
  updateLoading({ message: displayMessage, progress: 100 });
  const wrap = document.querySelector(".loading-message-wrap");
  if (wrap) wrap.scrollTop = 0;
  const stepsEl = $("#loading-steps");
  if (stepsEl) {
    stepsEl.querySelectorAll("li").forEach((li) => {
      li.classList.remove("active");
      li.classList.add("done");
    });
  }
  const cancelBtn = $("#loading-cancel");
  if (cancelBtn) cancelBtn.style.display = "none";
  const dismissBtn = $("#loading-dismiss");
  if (dismissBtn) dismissBtn.style.display = "inline-block";
}

function waitForLoadingDismiss() {
  return new Promise((resolve) => {
    loadingDismissResolver = resolve;
  });
}

async function presentLoadingResult({ message, variant = "ok", title } = {}) {
  if (!message) return;
  setLoadingResultMode(message, variant, title);
  await waitForLoadingDismiss();
}

async function notifyUser(message, { variant = "ok", title } = {}) {
  if (!message) return;
  showLoading(title || (variant === "error" ? "操作失败" : "完成"), []);
  try {
    await presentLoadingResult({ message, variant, title });
  } finally {
    hideLoading();
    resetLoadingResultMode();
  }
}

function notifyError(err) {
  if (isLoadingCancelled(err) || err?.loadingHandled) return;
  return notifyUser(formatErrorMessage(err?.message || String(err)), {
    variant: "error",
    title: "操作失败",
  });
}

window.notifyUser = notifyUser;
window.notifyError = notifyError;

class LoadingCancelledError extends Error {
  constructor() {
    super("操作已取消");
    this.name = "LoadingCancelledError";
  }
}

function isLoadingCancelled(err) {
  return err instanceof LoadingCancelledError || err?.name === "AbortError";
}

function getLoadingSignal() {
  return loadingAbortController?.signal;
}

function throwIfCancelled() {
  if (loadingAbortController?.signal.aborted) throw new LoadingCancelledError();
}

function sleep(ms) {
  const signal = getLoadingSignal();
  if (!signal) return new Promise((r) => setTimeout(r, ms));
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new LoadingCancelledError());
      return;
    }
    const t = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(t);
        reject(new LoadingCancelledError());
      },
      { once: true }
    );
  });
}

function showLoading(title, steps = [], { cancellable = false } = {}) {
  loadingDepth += 1;
  loadingAbortController = cancellable ? new AbortController() : null;
  resetLoadingResultMode();
  const overlay = $("#loading-overlay");
  if (!overlay) return;
  overlay.classList.add("visible");
  overlay.setAttribute("aria-hidden", "false");
  document.body.classList.add("loading-open");
  const titleEl = $("#loading-title");
  if (titleEl) titleEl.textContent = title || "请稍候";
  updateLoading({ message: "正在准备…", progress: 0 });

  const stepsEl = $("#loading-steps");
  if (stepsEl) {
    stepsEl.innerHTML = steps
      .map((label, i) => `<li data-step="${i}">${label}</li>`)
      .join("");
  }

  const cancelBtn = $("#loading-cancel");
  if (cancelBtn) {
    cancelBtn.style.display = cancellable ? "inline-block" : "none";
    cancelBtn.disabled = false;
  }
}

function cancelLoading() {
  if (loadingAbortController && !loadingAbortController.signal.aborted) {
    updateLoading({ message: "正在取消…" });
    loadingAbortController.abort();
  }
  const cancelBtn = $("#loading-cancel");
  if (cancelBtn) cancelBtn.disabled = true;
}

function updateLoading({ message, progress, step, stepStatus } = {}) {
  if (message != null) {
    const el = $("#loading-message");
    if (el) el.textContent = message;
  }
  if (progress != null) {
    const fill = $("#loading-bar-fill");
    if (fill) fill.style.width = `${Math.max(0, Math.min(100, progress))}%`;
  }
  if (step != null && stepStatus) {
    const stepsEl = $("#loading-steps");
    if (!stepsEl) return;
    stepsEl.querySelectorAll("li").forEach((li, i) => {
      li.classList.remove("active", "done");
      if (stepStatus === "done") {
        if (i <= step) li.classList.add("done");
      } else if (stepStatus === "active") {
        if (i < step) li.classList.add("done");
        else if (i === step) li.classList.add("active");
      }
    });
  }
}

function hideLoading() {
  loadingDepth = Math.max(0, loadingDepth - 1);
  if (loadingDepth > 0) return;
  const overlay = $("#loading-overlay");
  if (!overlay) return;
  overlay.classList.remove("visible");
  overlay.setAttribute("aria-hidden", "true");
  document.body.classList.remove("loading-open");
  updateLoading({ progress: 0 });
  loadingAbortController = null;
  loadingDismissResolver = null;
  const cancelBtn = $("#loading-cancel");
  if (cancelBtn) {
    cancelBtn.style.display = "none";
    cancelBtn.disabled = false;
  }
  resetLoadingResultMode();
}

async function withLoading(title, steps, fn, { cancellable = false } = {}) {
  let pendingResult = null;
  let caughtError = null;
  const update = (opts = {}) => {
    if (opts.result) {
      pendingResult =
        typeof opts.result === "string"
          ? { message: opts.result, variant: "ok" }
          : opts.result;
      return;
    }
    updateLoading(opts);
  };
  showLoading(title, steps, { cancellable });
  try {
    return await fn(update);
  } catch (e) {
    if (isLoadingCancelled(e)) throw new LoadingCancelledError();
    caughtError = e;
    pendingResult = {
      message: formatErrorMessage(e.message),
      variant: "error",
      title: "操作失败",
    };
  } finally {
    if (pendingResult) {
      try {
        await presentLoadingResult(pendingResult);
      } catch (_) {
        /* ignore */
      }
    }
    hideLoading();
    resetLoadingResultMode();
  }
  if (caughtError) {
    const err = new Error(caughtError.message);
    err.loadingHandled = true;
    throw err;
  }
}

function appendLog(msg, target = null) {
  if (!target) return;
  const ts = new Date().toLocaleTimeString("zh-CN");
  target.textContent += `[${ts}] ${msg}\n`;
  target.scrollTop = target.scrollHeight;
}

async function api(path, options = {}) {
  const signal = options.signal || getLoadingSignal();
  const { signal: _ignored, ...rest } = options;
  try {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...rest,
      ...(signal ? { signal } : {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    return data;
  } catch (e) {
    if (isLoadingCancelled(e)) throw new LoadingCancelledError();
    if (e instanceof TypeError && String(e.message).includes("fetch")) {
      throw new Error("无法连接问数agent（扫描进行中请勿重启服务）。若持续失败，请运行 python scripts/run_platform.py 后重试。");
    }
    throw e;
  }
}

function setConnResult(msg, ok) {
  const el = $("#conn-test-result");
  el.textContent = msg || "";
  el.className = "conn-result " + (ok === true ? "status-ok" : ok === false ? "status-bad" : "");
}

function collectConnFormValues() {
  const values = {};
  $("#conn-form")
    .querySelectorAll("[data-key]")
    .forEach((input) => {
      values[input.dataset.key] = input.value;
    });
  return values;
}

function fillConnForm(values) {
  Object.entries(values || {}).forEach(([key, val]) => {
    const input = $(`#conn-form [data-key="${key}"]`);
    if (input && val != null) input.value = val;
  });
}

function currentStep() {
  return wizardSteps[connRoleIndex] || null;
}

function currentEngineDef() {
  const step = currentStep();
  if (!step) return null;
  return (step.engines || []).find((e) => e.id === connCurrentEngine) || null;
}

function renderWizardNav() {
  const nav = $("#conn-wizard-nav");
  if (!nav) return;
  nav.innerHTML = wizardSteps
    .map((s, i) => {
      const saved = connValuesCache[s.id];
      const done = saved?.source === "platform";
      const cls = [i === connRoleIndex ? "active" : "", done ? "done" : ""].filter(Boolean).join(" ");
      return `
      <button type="button" class="wizard-step-btn ${cls}" data-step-index="${i}">
        <span class="num">${done ? "✓" : s.step}</span>
        <strong>${s.label}</strong>
        <div class="sub" style="margin-top:0.2rem;font-size:0.72rem;opacity:0.85">${
          saved?.engine ? `已选 ${saved.engine}` : "待配置"
        }</div>
      </button>`;
    })
    .join("");

  nav.querySelectorAll("[data-step-index]").forEach((btn) => {
    btn.addEventListener("click", () => {
      connRoleIndex = Number(btn.dataset.stepIndex);
      renderWizardStep();
    });
  });

  const prev = $("#btn-conn-prev");
  if (prev) prev.disabled = connRoleIndex <= 0;
  const saveBtn = $("#btn-conn-save");
  if (saveBtn) {
    saveBtn.textContent = connRoleIndex >= wizardSteps.length - 1 ? "保存配置" : "保存并继续";
  }
}

async function fetchConnDetail(role, engineId, { reveal = true } = {}) {
  const qs = new URLSearchParams({ engine: engineId });
  if (reveal) qs.set("reveal", "1");
  const detail = await api(`/api/connections/${role}?${qs}`);
  connValuesCache[`${role}:${engineId}`] = detail;
  return detail;
}

async function selectEngine(engineId) {
  const step = currentStep();
  if (!step) return;
  connCurrentRole = step.id;
  connCurrentEngine = engineId;
  try {
    await fetchConnDetail(step.id, engineId, { reveal: true });
  } catch (_) {}
  await renderConnForm();
}

async function renderConnForm() {
  const step = currentStep();
  const eng = currentEngineDef();
  if (!step || !eng) return;

  $("#conn-form-title").textContent = `${step.label} · ${eng.label}`;
  $("#conn-form-desc").textContent = eng.description || step.description || "";
  $("#btn-conn-test").disabled = false;
  $("#btn-conn-save").disabled = false;
  setConnResult("", null);

  $$(".conn-type-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.engine === eng.id);
  });

  const cached =
    connValuesCache[`${step.id}:${eng.id}`] ||
    (connValuesCache[step.id]?.engine === eng.id ? connValuesCache[step.id] : null) ||
    { values: {}, source: "env" };

  if (
    cached.values &&
    Object.values(cached.values).some((v) => v === "********") &&
    step.id &&
    eng.id
  ) {
    try {
      await fetchConnDetail(step.id, eng.id, { reveal: true });
    } catch (_) {}
  }

  const detail =
    connValuesCache[`${step.id}:${eng.id}`] ||
    (connValuesCache[step.id]?.engine === eng.id ? connValuesCache[step.id] : null) ||
    cached;

  const tag = $("#conn-source-tag");
  tag.textContent = detail.source === "platform" ? "平台已保存" : "来自 .env / 默认";
  tag.className = "tag " + (detail.source === "platform" ? "ok" : "wait");

  $("#conn-form").innerHTML = eng.fields
    .map((f) => {
      const val = detail.values?.[f.key] ?? f.default ?? "";
      const ph = f.placeholder || f.default || (f.secret ? "输入或修改密码" : "");
      const req = f.required ? '<span class="req">*</span>' : "";
      const inputType = f.secret ? "password" : f.type || "text";
      const inputHtml = f.secret
        ? `
        <div class="secret-input-wrap">
          <input id="conn-${f.key}" data-key="${f.key}" type="${inputType}"
            value="${String(val).replace(/"/g, "&quot;")}"
            placeholder="${String(ph).replace(/"/g, "&quot;")}"
            autocomplete="new-password" />
          <button type="button" class="secret-toggle" aria-label="显示${f.label}" title="显示/隐藏">
            <svg class="icon-eye-open" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path fill="currentColor" d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zm0 12.5a5 5 0 1 1 0-10 5 5 0 0 1 0 10zm0-2.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z"/>
            </svg>
            <svg class="icon-eye-closed hidden" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path fill="currentColor" d="M12 7c2.76 0 5 2.24 5 5 0 .65-.13 1.26-.36 1.83l2.92 2.92a1 1 0 0 0 1.41-1.41l-2.2-2.2C20.9 13.5 22 12.82 23 12c-1.73-4.39-6-7.5-11-7.5-1.4 0-2.74.25-3.98.7l1.46 1.46A5.02 5.02 0 0 1 12 7zM2 4.27 3.28 3 21 20.72 19.73 22l-3.08-3.08A11.8 11.8 0 0 1 12 19.5C7 19.5 2.73 16.39 1 12a11.8 11.8 0 0 1 3.17-4.53L2 4.27zM12 9.5a2.5 2.5 0 0 0-2.45 2.02l3.43 3.43A2.5 2.5 0 0 0 12 9.5z"/>
            </svg>
          </button>
        </div>`
        : `
        <input id="conn-${f.key}" data-key="${f.key}" type="${inputType}"
          value="${String(val).replace(/"/g, "&quot;")}"
          placeholder="${String(ph).replace(/"/g, "&quot;")}"
          autocomplete="off" />`;
      return `
      <div class="form-field${f.secret ? " secret-field" : ""}">
        <label for="conn-${f.key}">${f.label}${req}</label>
        ${inputHtml}
      </div>`;
    })
    .join("");
  bindSecretToggles();
}

function bindSecretToggles() {
  $$(".secret-toggle").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const wrap = btn.closest(".secret-input-wrap");
      const input = wrap?.querySelector("input");
      if (!input) return;
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.classList.toggle("visible", show);
      const label = input.id?.replace("conn-", "") || "密码";
      btn.setAttribute("aria-label", show ? `隐藏${label}` : `显示${label}`);
      btn.setAttribute("title", show ? "隐藏" : "显示");
    });
  });
}

function renderWizardStep() {
  const step = currentStep();
  if (!step) return;
  connCurrentRole = step.id;

  const saved = connValuesCache[step.id];
  const preferred =
    connCurrentEngine && step.engines.some((e) => e.id === connCurrentEngine)
      ? connCurrentEngine
      : saved?.engine || step.engines[0]?.id;
  connCurrentEngine = preferred;

  $("#conn-engine-heading").textContent = `① 选择${step.label}类型`;
  const listEl = $("#conn-engine-list");
  listEl.innerHTML = step.engines
    .map(
      (e) => `
    <button type="button" class="conn-type-item ${e.id === connCurrentEngine ? "active" : ""}" data-engine="${e.id}">
      <div>${e.label}</div>
      <div class="sub">${e.description || ""}</div>
    </button>`
    )
    .join("");

  listEl.querySelectorAll(".conn-type-item").forEach((btn) => {
    btn.addEventListener("click", () => selectEngine(btn.dataset.engine));
  });

  renderWizardNav();
  selectEngine(connCurrentEngine);
}

async function loadConnections() {
  const [typesRes, valsRes] = await Promise.all([
    api("/api/connections/types"),
    api("/api/connections"),
  ]);
  wizardSteps = typesRes.steps || [];
  connValuesCache = {};
  (valsRes.items || []).forEach((item) => {
    connValuesCache[item.role] = item;
  });
  if (connRoleIndex >= wizardSteps.length) connRoleIndex = 0;
  renderWizardStep();
}

function indexJobStepFromProgress(job) {
  const pct = job.progress_pct ?? 0;
  const msg = job.progress_message || "";
  if (pct >= 95 || msg.includes("完成")) return 4;
  if (pct >= 10 || msg.includes("向量化") || msg.includes("Embedding") || msg.includes("模型")) return 2;
  if (pct >= 3 || msg.includes("加载") || msg.includes("Qdrant")) return 1;
  return 1;
}

async function pollJob(jobId, targetLog = null, onProgress = null, options = {}) {
  const { unlimited = false, intervalMs = 2000, maxWaitSec = 360, onJob = null } = options;
  const maxIterations = unlimited ? Number.POSITIVE_INFINITY : Math.ceil((maxWaitSec * 1000) / intervalMs);
  appendLog(`任务 ${jobId} 已提交，等待完成...`, targetLog);
  let i = 0;
  while (i < maxIterations) {
    throwIfCancelled();
    await sleep(intervalMs);
    throwIfCancelled();
    const job = await api(`/api/jobs/${jobId}`);
    if (onJob) onJob(job);
    const hasServerProgress =
      job.progress_message ||
      (job.progress_pct != null && job.progress_pct > 0) ||
      (job.progress_total != null && job.progress_total > 0);
    if (onProgress) {
      if (hasServerProgress) {
        const pct = job.progress_pct != null ? job.progress_pct : Math.min(95, 15 + i * 0.3);
        const detail =
          job.progress_total > 0
            ? `（${job.progress_done || 0}/${job.progress_total}）`
            : "";
        const step = unlimited ? indexJobStepFromProgress(job) : 1;
        onProgress({
          message: `${job.progress_message || "执行中…"}${detail}`,
          progress: Math.min(98, pct),
          step,
          stepStatus: "active",
        });
      } else if (!unlimited) {
        onProgress({
          message: `后台任务执行中…（已等待 ${i * (intervalMs / 1000)} 秒）`,
          progress: Math.min(88, 12 + i * 1.2),
          step: 1,
          stepStatus: "active",
        });
      } else {
        onProgress({
          message: `等待任务启动…（已等待 ${i * (intervalMs / 1000)} 秒）`,
          progress: Math.min(8, 3 + i * 0.15),
          step: 1,
          stepStatus: "active",
        });
      }
    }
    if (job.status === "running") {
      i += 1;
      continue;
    }
    if (job.status === "success") {
      if (onProgress) {
        onProgress({
          message: job.progress_message || "任务完成",
          progress: 100,
          step: unlimited ? 3 : 1,
          stepStatus: "done",
        });
      }
      appendLog(`任务完成:\n${JSON.stringify(job.result, null, 2)}`, targetLog);
      return job;
    }
    appendLog(`任务失败:\n${job.error || "unknown"}`, targetLog);
    throw new Error(job.error || "任务失败");
  }
  throw new Error("任务超时");
}

async function runPipeline(action, body = {}, targetLog = null, onProgress = null, pollOptions = {}) {
  const endpoints = {
    init: "/api/pipeline/init",
    scan: "/api/pipeline/scan",
    sync: "/api/pipeline/sync",
    index: "/api/pipeline/index",
    "run-all": "/api/pipeline/run-all",
  };
  if (onProgress) {
    onProgress({ message: "正在提交任务…", progress: 8, step: 0, stepStatus: "active" });
  }
  const res = await api(endpoints[action], {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (onProgress) {
    onProgress({ message: "任务已提交", progress: 12, step: 0, stepStatus: "done" });
    onProgress({ message: "等待任务完成…", progress: 15, step: 1, stepStatus: "active" });
  }
  let jobResult = res.result;
  if (res.async && res.job_id) {
    const pollOpts =
      action === "index"
        ? { unlimited: true, ...pollOptions }
        : pollOptions;
    const job = await pollJob(res.job_id, targetLog, onProgress, pollOpts);
    jobResult = job?.result;
  } else if (res.result) {
    if (onProgress) onProgress({ message: "任务完成", progress: 90, step: 1, stepStatus: "done" });
    appendLog(`${action} 完成:\n${JSON.stringify(res.result, null, 2)}`, targetLog);
  }
  return { ...res, jobResult };
}

function getStagingSyncScope() {
  /** 同步始终提交当前暂存区全部内容（不支持逐表同步，避免 is_enabled 误伤）。 */
  return { scope: "full" };
}

function getMetaScope() {
  const mode = document.querySelector('input[name="meta-scope"]:checked')?.value || "full";
  if (mode === "full") return { scope: "full" };
  const sel = $("#meta-scope-table");
  const opt = sel?.selectedOptions?.[0];
  if (!opt?.value) throw new Error("请选择要更新的表");
  return {
    scope: "table",
    table_ids: [opt.value],
    include_tables: [opt.dataset.tableName],
  };
}

function getVectorScope() {
  const mode = document.querySelector('input[name="vector-scope"]:checked')?.value || "full";
  if (mode === "full") {
    return { scope: "full", types: "table,column,metric,join,doc_chunk" };
  }
  if (mode === "schema") {
    return { scope: "schema", types: "table,column" };
  }
  if (mode === "join") {
    return { scope: "join", types: "join" };
  }
  if (mode === "metric") {
    return { scope: "metric", types: "metric" };
  }
  if (mode === "doc_chunk") {
    return { scope: "doc_chunk", types: "doc_chunk" };
  }
  if (mode === "table") {
    const tid = $("#vector-scope-table")?.value;
    if (!tid) throw new Error("请选择表");
    return { scope: "table", table_ids: [tid], types: "table,column" };
  }
  const cid = $("#vector-scope-column")?.value;
  if (!cid) throw new Error("请选择字段");
  return { scope: "column", column_ids: [cid], types: "column" };
}

function buildSyncBody(scope) {
  const body = { update_vectors: false };
  if (scope.table_ids) body.table_ids = scope.table_ids;
  if (scope.column_ids) body.column_ids = scope.column_ids;
  const isFull = !scope.table_ids && !scope.column_ids;
  if (isFull) {
    if ($("#meta-purge-missing")?.checked || $("#metadata-purge-missing")?.checked) {
      body.purge_missing = true;
    }
    if ($("#meta-disable-absent")?.checked || $("#metadata-disable-absent")?.checked) {
      body.disable_absent = true;
    }
  }
  return body;
}

function buildScanBody(scope) {
  const body = { apply_ddl: false, apply_llm: false };
  if (scope.include_tables) body.include_tables = scope.include_tables;
  return body;
}

function isLlmEmptyOnly() {
  const mode = document.querySelector('input[name="llm-fill-mode"]:checked')?.value || "empty";
  return mode === "empty";
}

function buildLlmBody(extra = {}) {
  const body = { ...extra };
  if (isLlmEmptyOnly()) {
    body.empty_only = true;
    body.overwrite = false;
  } else {
    body.empty_only = false;
    body.overwrite = true;
  }
  return body;
}

function setStagingLlmProgress(progress) {
  stagingLlmProgress = progress;
  if (lastWorkflow?.staging) renderStagingStats(lastWorkflow.staging);
}

function clearStagingLlmProgress() {
  stagingLlmProgress = null;
  if (lastWorkflow?.staging) renderStagingStats(lastWorkflow.staging);
}

function syncStagingProgressFromJob(job, onProgress) {
  const hasServerProgress =
    job?.progress_message ||
    (job?.progress_pct != null && job.progress_pct > 0) ||
    (job?.progress_total != null && job.progress_total > 0);
  if (!hasServerProgress) return;
  const done = job.progress_done || 0;
  const total = job.progress_total || 0;
  const pct = job.progress_pct != null ? job.progress_pct : total > 0 ? (100 * done) / total : 0;
  const progressPayload = {
    done,
    total,
    pct,
    message: job.progress_message || "",
  };
  setStagingLlmProgress(progressPayload);
  if (onProgress) {
    onProgress({
      message: job.progress_message || "执行中…",
      progress: Math.min(98, pct),
      step: 1,
      stepStatus: "active",
    });
  }
}

async function fetchLlmPending(body) {
  try {
    return await api("/api/staging/llm-pending", {
      method: "POST",
      body: JSON.stringify(body),
    });
  } catch (e) {
    const msg = String(e.message || "");
    if (msg === "Not Found" || msg.includes("404")) {
      return {
        pending_total: 1,
        message: "正在调用 AI（兼容模式：请重启 python scripts/run_platform.py 以启用待补统计与进度条）",
        fallback: true,
      };
    }
    throw e;
  }
}

async function ensureLlmPending(body) {
  const pending = await fetchLlmPending(body);
  if (pending.pending_total > 0) {
    setStagingLlmProgress({
      done: 0,
      total: pending.pending_total,
      pct: 0,
      message: pending.message || `待补全 ${pending.pending_total} 项`,
    });
    return pending;
  }
  clearStagingLlmProgress();
  await notifyUser(pending.message || "无需补全", { variant: "info", title: "AI 补全" });
  return null;
}

function summarizeLlmResult(result, pending) {
  if (!result) return null;
  if (result.skipped) {
    return {
      message: result.message || pending?.message || "无需补全",
      variant: "info",
    };
  }
  if (result.errors?.length) {
    const ok = result.tables_processed ?? 0;
    return {
      message: `已处理 ${ok} 张表，写入 ${result.filled ?? 0} 条。\n${result.errors.length} 张表失败：\n${result.errors.slice(0, 5).join("\n")}`,
      variant: "warn",
    };
  }
  if ((result.filled ?? 0) === 0 && (result.tables_processed ?? 0) === 0) {
    return {
      message: result.message || "未写入任何注释（可能已全部有说明，或模型未返回有效内容）",
      variant: "warn",
    };
  }
  if ((result.filled ?? 0) === 0 && (result.tables_processed ?? 0) > 0) {
    return {
      message:
        result.message ||
        "AI 未返回可写入的说明。若字段注释已齐全，请点「AI 补全表注释」；仍失败请重启平台后重试。",
      variant: "warn",
    };
  }
  if (result.message) return { message: result.message, variant: "ok" };
  if ((result.filled ?? 0) > 0) {
    return { message: `完成：共写入 ${result.filled} 条说明`, variant: "ok" };
  }
  return null;
}

async function runStagingLlm(body, title = "AI 补全注释") {
  let pending;
  try {
    pending = await ensureLlmPending(body);
  } catch (e) {
    await notifyError(e);
    return;
  }
  if (!pending) return;

  await withLoading(title, ["统计待补", "AI 生成", "刷新列表"], async (update) => {
    try {
    update({ message: pending.message || "正在调用 AI…", step: 1, progress: 10 });
    const res = await api("/api/staging/generate-llm", {
      method: "POST",
      body: JSON.stringify(body),
    });
    let result = res.result;
    if (res.job_id) {
      const job = await pollJob(res.job_id, null, null, {
        onJob: (j) => syncStagingProgressFromJob(j, update),
      });
      result = job?.result;
    } else if (result) {
      appendLog(`AI 补全: ${JSON.stringify(result)}`);
    }
    const resultNotice = summarizeLlmResult(result, pending);
    update({ message: "刷新列表…", step: 2, progress: 90 });
    await loadStagingTables($("#table-filter")?.value || "");
    if (currentStagingTableId) {
      const data = await api("/api/staging/tables");
      const t = (data.items || []).find((x) => x.table_id === currentStagingTableId);
      const el = tableListEl.querySelector(`[data-id="${currentStagingTableId}"]`);
      await loadStagingColumns(currentStagingTableId, el, t);
    }
    if (lastWorkflow?.staging) renderStagingStats(lastWorkflow.staging);
    try {
      const wf = await api("/api/workflow");
      renderWorkflow(wf);
    } catch (_) {
      /* ignore */
    }
    update({ message: "完成", step: 2, stepStatus: "done", progress: 100 });
    if (resultNotice) update({ result: resultNotice });
    } finally {
      clearStagingLlmProgress();
    }
  }, { cancellable: true });
}

function formatLlmAllColumnsResult(result) {
  if (!result) return "未收到任务结果";
  const lines = [
    result.message || "任务完成",
    `共 ${result.total_tables ?? 0} 张表 · 成功 ${result.success_count ?? 0} · 失败 ${result.fail_count ?? 0} · 写入 ${result.filled ?? 0} 条字段说明`,
  ];
  const failed = result.failed_tables || [];
  if (failed.length) {
    lines.push("", "补全失败的表：");
    failed.forEach((t) => {
      lines.push(`  · ${t.table_name}：${t.error}`);
    });
  }
  return lines.join("\n");
}

async function runStagingLlmAllColumns() {
  const body = buildLlmBody({});
  let pending;
  try {
    pending = await ensureLlmPending({ ...body, columns_only: true });
  } catch (e) {
    await notifyError(e);
    return;
  }
  if (!pending) return;

  await withLoading(
    "AI 补全全部字段注释",
    ["统计待补", "逐表补全字段", "完成"],
    async (update) => {
      try {
      update({ message: pending.message || "正在提交任务…", step: 0, progress: 5 });
      const res = await api("/api/staging/generate-llm-all-columns", {
        method: "POST",
        body: JSON.stringify(body),
      });
      let result = res.result;
      if (res.job_id) {
        update({ message: "任务已提交，逐表执行中…", step: 1, progress: 10 });
        const job = await pollJob(res.job_id, null, update, {
          unlimited: true,
          onJob: (j) => syncStagingProgressFromJob(j, update),
        });
        result = job?.result;
      }
      update({ message: "刷新列表…", step: 2, progress: 95 });
      await loadStagingTables($("#table-filter")?.value || "");
      if (currentStagingTableId) {
        const data = await api("/api/staging/tables");
        const t = (data.items || []).find((x) => x.table_id === currentStagingTableId);
        const el = tableListEl.querySelector(`[data-id="${currentStagingTableId}"]`);
        await loadStagingColumns(currentStagingTableId, el, t);
      }
      if (lastWorkflow?.staging) renderStagingStats(lastWorkflow.staging);
      try {
        const wf = await api("/api/workflow");
        renderWorkflow(wf);
      } catch (_) {
        /* ignore */
      }
      update({ message: "完成", step: 2, stepStatus: "done", progress: 100 });
      if (result?.skipped) {
        update({
          result: {
            message: result.message || pending.message || "无需补全",
            variant: "info",
          },
        });
      } else if (result) {
        update({
          result: {
            message: formatLlmAllColumnsResult(result),
            variant: (result.fail_count || 0) > 0 ? "warn" : "ok",
          },
        });
      }
      } finally {
        clearStagingLlmProgress();
      }
    },
    { cancellable: true }
  );
}

function buildIndexBody(scope, full = false) {
  const body = { full };
  if (scope.types) body.types = scope.types;
  if (scope.table_ids) body.table_ids = scope.table_ids;
  if (scope.column_ids) body.column_ids = scope.column_ids;
  return body;
}

function populateMetaScopeTables(items) {
  rawTableScopeCache = items || [];
  const el = document.getElementById("meta-scope-table");
  if (!el) return;
  el.innerHTML =
    rawTableScopeCache.length === 0
      ? '<option value="">— 请先配置业务库连接 —</option>'
      : `<option value="">— 选择表 —</option>${rawTableScopeCache
          .map(
            (t) =>
              `<option value="${t.table_id}" data-table-name="${t.table_name}">${t.table_name}</option>`
          )
          .join("")}`;
  updateScopeTableSelects();
}

function populateStagingScopeTables(items) {
  stagingTableCache = items || [];
  updateScopeTableSelects();
}

function populateVectorScopeTables(items) {
  vectorTableScopeCache = items || [];
  const el = document.getElementById("vector-scope-table");
  if (!el) return;
  el.innerHTML =
    vectorTableScopeCache.length === 0
      ? '<option value="">— 元数据库暂无已启用表 —</option>'
      : `<option value="">— 选择表 —</option>${vectorTableScopeCache
          .map(
            (t) =>
              `<option value="${t.table_id}" data-table-name="${t.table_name}">${t.table_name}</option>`
          )
          .join("")}`;
  updateScopeTableSelects();
}

async function loadVectorScopeTables() {
  try {
    const data = await api("/api/l1/tables");
    populateVectorScopeTables(data.items || []);
  } catch (e) {
    populateVectorScopeTables([]);
    console.warn("loadVectorScopeTables", e);
  }
}

function populateScopeTables(items) {
  populateStagingScopeTables(items);
}

async function loadRawScopeTables() {
  try {
    const data = await api("/api/raw/tables");
    populateMetaScopeTables(data.items || []);
  } catch (e) {
    populateMetaScopeTables([]);
    console.warn("loadRawScopeTables", e);
  }
}

function updateScopeTableSelects() {
  const metaMode = document.querySelector('input[name="meta-scope"]:checked')?.value;
  const metaSel = $("#meta-scope-table");
  if (metaSel) metaSel.disabled = metaMode !== "table" || rawTableScopeCache.length === 0;

  const vecMode = document.querySelector('input[name="vector-scope"]:checked')?.value;
  const vecPickers = $("#vector-scope-pickers");
  const vecTable = $("#vector-scope-table");
  const vecCol = $("#vector-scope-column");
  const needsTablePick = vecMode === "table" || vecMode === "column";
  if (vecPickers) vecPickers.style.display = needsTablePick ? "flex" : "none";
  if (vecTable) {
    vecTable.disabled = !needsTablePick || vectorTableScopeCache.length === 0;
    vecTable.style.display = needsTablePick ? "inline-block" : "none";
  }
  if (vecCol) {
    vecCol.style.display = vecMode === "column" ? "inline-block" : "none";
    vecCol.disabled = vecMode !== "column";
  }

  const purgeWrap = $("#meta-purge-wrap");
  if (purgeWrap) purgeWrap.style.display = metaMode === "full" ? "block" : "none";

  const disableWrap = $("#meta-disable-wrap");
  if (disableWrap) disableWrap.style.display = metaMode === "full" ? "block" : "none";
}

async function loadVectorColumns(tableId) {
  const sel = $("#vector-scope-column");
  if (!sel || !tableId) return;
  try {
    const data = await api(`/api/l1/tables/${tableId}/columns`);
    const items = data.items || [];
    sel.innerHTML =
      items.length === 0
        ? '<option value="">— 无字段 —</option>'
        : `<option value="">— 选择字段 —</option>${items
            .map((c) => `<option value="${c.column_id}">${c.column_name}</option>`)
            .join("")}`;
  } catch (_) {
    sel.innerHTML = '<option value="">— 加载失败 —</option>';
  }
}

function friendlyDbError(msg) {
  const s = String(msg || "");
  if (s.includes("1045") && s.includes("Access denied")) return "用户名或密码错误";
  if (s.includes("1040") && s.includes("Too many connections")) return "连接数已满，请释放 DB 连接";
  if (s.includes("2013") && s.includes("Lost connection")) return "连接被服务器断开";
  return s.split("\n")[0].slice(0, 120);
}

function renderOverview(data) {
  if (!overviewEl) return;
  const errHint =
    (data.connection_errors || [])
      .map((e) => `${e.role === "meta" ? "元数据库" : "业务库"}: ${friendlyDbError(e.message)}`)
      .join(" · ") || "";
  overviewEl.innerHTML = `
    <div class="card"><div class="label">业务库</div><div class="value small">${data.raw_db_name || "-"}</div></div>
    <div class="card"><div class="label">元数据库</div><div class="value small">${data.meta_db_name || "-"}</div></div>
    <div class="card"><div class="label">原始表</div><div class="value">${data.raw_table_count ?? 0}</div></div>
    <div class="card"><div class="label">已同步表</div><div class="value">${data.table_meta_count ?? 0}</div></div>
    <div class="card"><div class="label">已同步字段</div><div class="value">${data.column_meta_count ?? 0}</div></div>
    <div class="card"><div class="label">向量条目</div><div class="value">${data.qdrant_points ?? 0}</div></div>
    ${errHint ? `<div class="overview-error hint">${errHint}</div>` : ""}
  `;
  const vc = $("#vector-cards");
  if (vc) {
    vc.innerHTML = `
      <div class="card"><div class="label">Qdrant 向量</div><div class="value">${data.qdrant_points ?? 0}</div></div>
      <div class="card"><div class="label">已同步表</div><div class="value">${data.table_meta_count}</div></div>
      <div class="card"><div class="label">已同步字段</div><div class="value">${data.column_meta_count}</div></div>
    `;
  }
}

function renderHealth(wf) {
  const h = wf.health;
  const items = [
    { title: "连接配置", data: h.connections, okText: h.connections.message },
    { title: "业务库 MySQL", data: h.raw_db, okText: `${h.raw_db.db_name} · ${h.raw_db.table_count} 表` },
    { title: "元数据库 MySQL", data: h.meta_db, okText: `${h.meta_db.db_name} · ${h.meta_db.table_count || 0} 表已同步` },
    { title: "Qdrant 向量库", data: h.qdrant, okText: `${h.qdrant.points || 0} 条向量` },
    { title: "Embedding", data: h.embedding, okText: h.embedding.message },
    { title: "LLM", data: h.llm || { ok: false, message: "未检测" }, okText: (h.llm && h.llm.message) || "—" },
  ];
  healthEl.innerHTML = items
    .map(
      (it) => `
    <div class="health-item">
      <div class="title">${it.title}
        <span class="tag ${it.data.ok ? "ok" : "wait"}">${it.data.ok ? "正常" : "异常"}</span>
      </div>
      <div class="detail">${it.data.ok ? it.okText : it.data.message}</div>
    </div>`
    )
    .join("");

  const vh = $("#vector-health");
  if (vh) {
    const vecItems = [
      { title: "Qdrant", data: h.qdrant, okText: `${h.qdrant.points || 0} 条 · ${h.qdrant.collection || ""}` },
      { title: "Embedding", data: h.embedding, okText: h.embedding.message },
      { title: "LLM", data: h.llm || { ok: false, message: "未检测" }, okText: (h.llm && h.llm.message) || "—" },
    ];
    vh.innerHTML = vecItems
      .map(
        (it) => `
      <div class="health-item">
        <div class="title">${it.title}
          <span class="tag ${it.data.ok ? "ok" : "wait"}">${it.data.ok ? "正常" : "异常"}</span>
        </div>
        <div class="detail">${it.data.ok ? it.okText : it.data.message}</div>
      </div>`
      )
      .join("");
  }
}

function stagingDisplayDesc(item) {
  return ((item?.description || item?.hive_comment || "") + "").trim();
}

function patchStagingTableListItem(tableId, tableInfo) {
  const el = tableListEl?.querySelector(`.table-item[data-id="${tableId}"]`);
  if (!el || !tableInfo) return;
  const miss = tableInfo.missing_table_comment || (tableInfo.missing_column_comments || 0) > 0;
  el.classList.toggle("missing", !!miss);
  const nameEl = el.querySelector(".name");
  if (nameEl) {
    nameEl.innerHTML = `${tableInfo.table_name || el.dataset.name || ""}${
      miss ? ' <span class="tag wait">缺注释</span>' : ""
    }`;
  }
  const metaEl = el.querySelector(".meta");
  if (metaEl) {
    metaEl.textContent = `${tableInfo.db_name || ""} · ${tableInfo.column_count ?? ""} 字段 · ${
      stagingDisplayDesc(tableInfo) || "（未填）"
    } · ${sourceLabel(tableInfo.comment_source)}`;
  }
}

function sourceLabel(src) {
  const m = {
    schema: "库注释",
    ddl: "建表DDL",
    llm: "LLM",
    manual: "手工",
    l1: "L1继承",
    l1_mapped: "L1映射",
  };
  return m[src] || src || "-";
}

function matchStatusLabel(status) {
  const m = {
    inherited: "继承",
    new: "新增",
    manual: "手工",
    l1_mapped: "已映射",
  };
  return m[status] || status || "-";
}

function matchStatusTag(status) {
  if (!status) return "";
  const cls =
    status === "new" ? "wait" : status === "inherited" || status === "l1_mapped" ? "ok" : "";
  return `<span class="tag ${cls}">${matchStatusLabel(status)}</span>`;
}

function renderStagingStats(data) {
  if (!stagingStatsEl) return;
  const cycle = lastWorkflow ? getMetadataCycleState(lastWorkflow) : {};
  const ok = data.review_complete;
  const syncedIdle = cycle.syncedIdle;
  const tagLabel = ok ? "可同步" : syncedIdle ? "已同步" : "未完成";
  const tagCls = ok ? "ok" : syncedIdle ? "ok" : "wait";
  let progressHtml = "";
  if (stagingLlmProgress && stagingLlmProgress.total > 0) {
    const done = stagingLlmProgress.done || 0;
    const total = stagingLlmProgress.total;
    const pct = Math.max(
      0,
      Math.min(
        100,
        stagingLlmProgress.pct != null ? stagingLlmProgress.pct : Math.round((100 * done) / total)
      )
    );
    progressHtml = `
      <div class="staging-llm-progress">
        <div class="staging-llm-bar"><div class="staging-llm-fill" style="width:${pct}%"></div></div>
        <div class="staging-llm-msg">${stagingLlmProgress.message || `${done}/${total}`}</div>
      </div>`;
  }
  stagingStatsEl.innerHTML = `
    <div class="health-item">
      <div class="title">编辑进度
        <span class="tag ${tagCls}">${tagLabel}</span>
      </div>
      <div class="detail">
        暂存 ${data.staging_table_count || 0} 表 / ${data.staging_column_count || 0} 字段 ·
        缺表说明 ${data.missing_table_comments || 0} · 缺字段说明 ${data.missing_column_comments || 0}
        ${syncedIdle ? " · 暂存区已空，重新扫描后可再次编辑" : ""}
      </div>
      ${progressHtml}
    </div>`;
  const syncBtn = $("#btn-staging-goto-sync");
  if (syncBtn) syncBtn.disabled = !ok;
  renderStagingPendingOverview(data);
}

function renderStagingPendingOverview(data) {
  if (!stagingPendingOverviewEl) return;
  const po = data.pending_overview;
  if (!po || !po.items || po.items.length === 0) {
    stagingPendingOverviewEl.classList.add("hidden");
    stagingPendingOverviewEl.innerHTML = "";
    return;
  }
  stagingPendingOverviewEl.classList.remove("hidden");
  const rows = po.items
    .map((t) => {
      const tableOk = t.table_comment_filled;
      const manualTag = t.has_manual_edit
        ? '<span class="tag ok">有手工编辑</span>'
        : '<span class="tag">仅扫描/继承</span>';
      const tableStatus = tableOk
        ? `<span class="tag ok">已填</span> ${sourceLabel(t.table_comment_source)}`
        : '<span class="tag wait">缺表说明</span>';
      const colStatus =
        t.column_total > 0
          ? `${t.column_filled}/${t.column_total}${t.column_manual_count ? ` · 手工${t.column_manual_count}` : ""}`
          : "0";
      return `
        <tr class="staging-pending-row" data-table-id="${t.table_id}">
          <td class="name-cell">${t.table_name}</td>
          <td>${tableStatus}</td>
          <td>${colStatus}</td>
          <td>${manualTag}</td>
          <td><button type="button" class="btn secondary btn-xs staging-pending-goto" data-table-id="${t.table_id}">编辑</button></td>
        </tr>`;
    })
    .join("");
  stagingPendingOverviewEl.innerHTML = `
    <div class="health-item">
      <div class="title">暂存待同步清单
        <span class="tag wait">待写入 L1</span>
      </div>
      <div class="detail">${po.summary || ""} · 点击行或「编辑」可跳转到左侧表列表</div>
    </div>
    <div class="staging-pending-table-wrap">
      <table class="staging-pending-table">
        <thead>
          <tr>
            <th>表名</th>
            <th>表说明</th>
            <th>字段（已填/总数）</th>
            <th>来源</th>
            <th></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;

  stagingPendingOverviewEl.querySelectorAll(".staging-pending-row").forEach((row) => {
    const goto = (tid) => {
      if (!tid) return;
      const item = tableListEl?.querySelector(`.table-item[data-id="${tid}"]`);
      if (item) {
        item.scrollIntoView({ block: "nearest" });
        item.click();
      } else {
        loadStagingColumns(tid).catch((e) => console.warn(e));
      }
    };
    row.addEventListener("click", () => goto(row.dataset.tableId));
    row.querySelector(".staging-pending-goto")?.addEventListener("click", (ev) => {
      ev.stopPropagation();
      goto(row.dataset.tableId);
    });
  });
}

function workflowStepStatus(s) {
  if (s.done) return '<span class="status-ok">已完成</span>';
  if (s.ready) return '<span class="status-warn">待执行</span>';
  return '<span class="status-bad">前置条件未满足</span>';
}

function renderWorkflowProgress(steps, currentId) {
  const el = $("#workflow-progress");
  if (!el) return;
  const doneCount = steps.filter((s) => s.done).length;
  const cols = steps
    .map((s, i) => {
      let state = "pending";
      if (s.done) state = "done";
      else if (s.id === currentId) state = "current";
      else if (s.ready) state = "ready";
      const desc = META_STEP_DESC[s.id] || s.desc || s.title;
      return `
        <div class="wf-progress-col ${state}" title="${s.title}">
          <div class="wf-seg"></div>
          <div class="wf-col-num">${s.done ? "✓" : i + 1}</div>
          <div class="wf-col-desc">${desc}</div>
        </div>`;
    })
    .join("");
  el.innerHTML = `
    <div class="wf-progress-head">
      <span class="wf-progress-meta">${doneCount} / ${steps.length} 步已完成</span>
    </div>
    <div class="wf-progress-grid" style="--wf-cols:${steps.length}">${cols}</div>`;
}

function renderWorkflow(wf) {
  lastWorkflow = wf;
  const steps = getMetaWorkflowSteps(wf);
  const currentId = getMetaCurrentStepId(steps);
  renderWorkflowProgress(steps, currentId);
  const actionMap = {
    init: "init",
    scan: "scan",
    review: "goto-review",
    sync: "sync",
    index: "goto-vector",
  };
  workflowEl.innerHTML = steps
    .map((s, i) => {
      const cls = [s.done ? "done" : "", s.id === currentId ? "current" : ""]
        .filter(Boolean)
        .join(" ");
      const status = workflowStepStatus(s);
      const action = actionMap[s.id];
      const btnLabel =
        action === "goto-review"
          ? "去编辑"
          : s.id === "init" && s.done
            ? "重建系统表"
            : s.id === "index"
              ? "去构建"
              : s.done
                ? "重新扫描"
                : "执行";
      const btnTitle =
        s.id === "init" && s.done
          ? ' title="仅在系统表异常时使用，可能影响已有元数据"'
          : s.id === "scan" && s.done
            ? ' title="重新扫描并覆盖暂存区"'
            : "";
      const btn =
        action === "goto-review" || action === "goto-vector"
          ? `<button class="btn ${s.id === "index" ? "primary" : "secondary"}" data-goto="${action === "goto-vector" ? "vector" : "metadata"}" ${!s.ready ? "disabled" : ""}>${btnLabel}</button>`
          : `<button class="btn ${s.id === "sync" ? "primary" : s.id === "init" && s.done ? "secondary" : ""}" data-action="${action}"
                  ${!s.ready && !(s.id === "init" && s.done) ? "disabled" : ""}${btnTitle}>${btnLabel}</button>`;
      return `
      <div class="wf-step ${cls}">
        <div class="wf-num">${s.done ? "✓" : i + 1}</div>
        <div>
          <strong>${s.title}</strong>
          <p>${s.desc}</p>
          <div class="wf-status">${status}</div>
        </div>
        ${btn}
      </div>`;
    })
    .join("");

  workflowEl.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", () => handleAction(btn.dataset.action));
  });
  workflowEl.querySelectorAll("[data-goto]").forEach((btn) => {
    btn.addEventListener("click", () => gotoTab(btn.dataset.goto));
  });

  if (wf.staging) renderStagingStats(wf.staging);
}

async function loadWorkflowCore() {
  const wf = await api("/api/workflow");
  renderHealth(wf);
  renderWorkflow(wf);
  return wf;
}

async function loadWorkflow() {
  const overlayVisible = $("#loading-overlay")?.classList.contains("visible");
  if (!overlayVisible && workflowEl) {
    workflowEl.innerHTML = '<div class="hint" style="padding:0.75rem">正在检测环境…</div>';
  }
  return loadWorkflowCore();
}

function gotoTab(tab) {
  $$(".nav-item").forEach((b) => b.classList.remove("active"));
  $$(".tab").forEach((t) => t.classList.remove("active"));
  $(`.nav-item[data-tab="${tab}"]`)?.classList.add("active");
  $(`#tab-${tab}`)?.classList.add("active");
  if (tab === "metadata") {
    loadStagingTables($("#table-filter")?.value || "");
    window.L1Edit?.onTabShow("metadata");
  }
  if (tab === "knowledge") window.L1Edit?.onTabShow("knowledge");
  if (tab === "jobs") loadJobs();
  if (tab === "connections") loadConnections();
  if (tab === "meta-sync") loadWorkflow();
  if (tab === "vector") {
    loadModelSettings();
    loadWorkflow();
    loadVectorScopeTables().catch((e) => console.warn(e));
  }
  if (tab === "search") loadSearchScope();
}

async function loadStagingTables(filter = "") {
  const url = filter ? `/api/staging/tables?q=${encodeURIComponent(filter)}` : "/api/staging/tables";
  let data;
  try {
    data = await api(url);
  } catch (e) {
    tableListEl.innerHTML = `<div class="hint" style="padding:1rem">${e.message}</div>`;
    return;
  }
  renderStagingStats(data);
  const items = data.items || [];
  populateStagingScopeTables(items);
  tableCountEl.textContent = items.length;
  tableListEl.innerHTML =
    items.length === 0
      ? '<div class="hint" style="padding:1rem">暂无暂存数据，请先在「元数据同步」执行「扫描原始表」</div>'
      : items
          .map((t) => {
            const miss = t.missing_table_comment || t.missing_column_comments > 0;
            const matchTag = matchStatusTag(t.match_status);
            const colHint =
              t.inherited_column_count || t.new_column_count
                ? ` · 继承${t.inherited_column_count || 0}/新增${t.new_column_count || 0}字段`
                : "";
            return `
      <div class="table-item ${miss ? "missing" : ""}" data-id="${t.table_id}">
        <div class="name">${t.table_name}${matchTag}${miss ? ' <span class="tag wait">缺注释</span>' : ""}</div>
        <div class="meta">${t.db_name} · ${t.column_count} 字段${colHint} · ${stagingDisplayDesc(t) || "（未填）"} · ${sourceLabel(t.comment_source)}</div>
      </div>`;
          })
          .join("");

  tableListEl.querySelectorAll(".table-item").forEach((el) => {
    el.addEventListener("click", () =>
      loadStagingColumns(el.dataset.id, el, items.find((x) => x.table_id === el.dataset.id))
    );
  });
}

async function mergeStagingFromL1(tableIds = null) {
  const body = tableIds ? { table_ids: tableIds } : {};
  return api("/api/staging/merge-l1", { method: "POST", body: JSON.stringify(body) });
}

async function clearStaging(tableIds = null) {
  const body = tableIds ? { table_ids: tableIds } : {};
  return api("/api/staging/clear", { method: "POST", body: JSON.stringify(body) });
}

function renderL1OrphansPanel(orphans, stagingItems, tableId) {
  const panel = $("#l1-orphans-panel");
  const list = $("#l1-orphans-list");
  if (!panel || !list) return;
  if (!orphans.length) {
    panel.classList.add("hidden");
    list.innerHTML = "";
    return;
  }
  panel.classList.remove("hidden");
  const newCols = stagingItems.filter((c) => c.match_status === "new");
  const options =
    newCols.length > 0
      ? newCols
          .map(
            (c) =>
              `<option value="${c.column_id}">${c.column_name}${c.missing_comment ? "（待填说明）" : ""}</option>`
          )
          .join("")
      : `<option value="">— 无新增字段可映射 —</option>`;
  list.innerHTML = orphans
    .map(
      (o) => `
    <div class="l1-orphan-row" style="display:flex;flex-wrap:wrap;gap:0.35rem 0.75rem;align-items:center;margin-bottom:0.5rem;padding:0.35rem 0;border-bottom:1px solid var(--border,#eee)">
      <span><strong>${o.column_name}</strong> <span class="hint">${(o.description || "无说明").slice(0, 48)}</span></span>
      <select class="scope-select orphan-target" data-l1-id="${o.column_id}" ${newCols.length ? "" : "disabled"}>
        ${options}
      </select>
      <button type="button" class="btn secondary btn-inherit-l1" data-l1-id="${o.column_id}" ${newCols.length ? "" : "disabled"}>继承到所选字段</button>
    </div>`
    )
    .join("");
  list.querySelectorAll(".btn-inherit-l1").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const l1Id = btn.dataset.l1Id;
      const sel = list.querySelector(`.orphan-target[data-l1-id="${l1Id}"]`);
      const targetId = sel?.value;
      if (!targetId) {
        await notifyUser("请选择要映射到的暂存字段", { variant: "wait", title: "L1 映射" });
        return;
      }
      try {
        const res = await api(`/api/staging/columns/${targetId}/inherit-l1`, {
          method: "POST",
          body: JSON.stringify({ l1_column_id: l1Id }),
        });
        await notifyUser(res.message || "已继承", { variant: "ok", title: "L1 映射" });
        const el = tableListEl.querySelector(`[data-id="${tableId}"]`);
        await loadStagingColumns(tableId, el, currentStagingTable);
      } catch (e) {
        await notifyError(e);
      }
    });
  });
}

async function loadStagingColumns(tableId, el, tableMeta) {
  currentStagingTableId = tableId;
  currentStagingTable = tableMeta;
  $$(".table-item").forEach((n) => n.classList.remove("active"));
  if (el) el.classList.add("active");
  columnHintEl.textContent = "";
  const descBox = $("#table-desc-edit");
  if (descBox) descBox.style.display = "block";
  const descInput = $("#table-desc-input");
  const srcTag = $("#table-source-tag");
  const hiveHint = $("#table-hive-comment-hint");
  if (hiveHint) {
    hiveHint.textContent = "";
    hiveHint.style.display = "none";
  }

  const data = await api(`/api/staging/tables/${tableId}/columns`);
  const items = data.items || [];
  const orphans = data.l1_orphans || [];
  const tableInfo = data.table || tableMeta;
  if (descInput && tableInfo) {
    descInput.value = stagingDisplayDesc(tableInfo);
  }
  if (srcTag && tableInfo) {
    srcTag.textContent = `${sourceLabel(tableInfo.comment_source)} ${matchStatusLabel(tableInfo.match_status)}`.trim();
    srcTag.className = "tag " + (stagingDisplayDesc(tableInfo) ? "ok" : "wait");
  }
  if (tableInfo) {
    currentStagingTable = { ...(tableMeta || {}), ...tableInfo };
    patchStagingTableListItem(tableId, tableInfo);
  }
  columnListEl.innerHTML = `
    <div class="col-row header col-row-edit"><span>字段</span><span>类型</span><span>说明（可编辑）</span></div>
    ${items
      .map(
        (c) => `
      <div class="col-row col-row-edit ${c.missing_comment ? "missing" : ""}" data-col-id="${c.column_id}">
        <span>${c.column_name} ${matchStatusTag(c.match_status)}<br><small class="hint">${sourceLabel(c.comment_source)}</small></span>
        <span>${c.data_type}</span>
        <span><input class="col-desc-input" data-col-id="${c.column_id}" value="${stagingDisplayDesc(c).replace(/"/g, "&quot;")}" placeholder="填写元数据说明" />
        <button class="btn secondary btn-save-col" data-col-id="${c.column_id}">保存</button>
        <button class="btn secondary btn-llm-col" data-col-id="${c.column_id}" title="LLM 生成此字段说明">LLM</button></span>
      </div>`
      )
      .join("")}
  `;
  columnListEl.querySelectorAll(".btn-save-col").forEach((btn) => {
    btn.addEventListener("click", () => saveColumnDesc(btn.dataset.colId));
  });
  columnListEl.querySelectorAll(".btn-llm-col").forEach((btn) => {
    btn.addEventListener("click", () =>
      runStagingLlm(
        buildLlmBody({ column_ids: [btn.dataset.colId], columns_only: true }),
        "LLM 生成字段说明"
      ).catch(notifyError)
    );
  });

  renderL1OrphansPanel(orphans, items, tableId);

  if ($("#vector-scope-table")?.value === tableId) {
    loadVectorColumns(tableId);
  }
}

async function saveAllStagingCommentsFallback(update) {
  if (currentStagingTableId) {
    const tableDesc = $("#table-desc-input")?.value?.trim();
    if (tableDesc) {
      await api(`/api/staging/tables/${currentStagingTableId}`, {
        method: "PUT",
        body: JSON.stringify({ description: tableDesc }),
      });
    }
    const inputs = columnListEl.querySelectorAll(".col-desc-input");
    const columns = Array.from(inputs)
      .map((input) => ({
        column_id: input.dataset.colId,
        description: input.value.trim(),
      }))
      .filter((c) => c.column_id && c.description);
    if (columns.length) {
      try {
        await api(`/api/staging/tables/${currentStagingTableId}/columns/batch`, {
          method: "POST",
          body: JSON.stringify({ columns }),
        });
      } catch {
        await saveAllColumnDescsOneByOne(columns);
      }
    }
  }

  update({ message: "全库批量固化非空注释…", step: 1, progress: 55 });
  // 旧服务无 save-all 接口时：拉取暂存表列表会触发服务端 materialize（批量 SQL）
  const data = await api("/api/staging/tables");
  const tables = data.items || [];
  const savedTables = tables.filter((t) => stagingDisplayDesc(t)).length;
  const nonemptyColsGuess = tables.reduce(
    (n, t) => n + ((t.column_count || 0) - (t.missing_column_comments || 0)),
    0
  );
  return {
    saved_tables: savedTables,
    saved_columns: nonemptyColsGuess,
    fallback: true,
    message: `已固化非空注释（兼容模式：${savedTables} 表约有说明）。重启 python scripts/run_platform.py 后可一键批量保存`,
  };
}

async function saveAllStagingComments() {
  await withLoading("一键保存注释", ["收集编辑", "全库保存", "刷新列表"], async (update) => {
    update({ message: "正在保存…", step: 0, progress: 15 });
    const body = {};
    if (currentStagingTableId) {
      body.current_table_id = currentStagingTableId;
      const tableDesc = $("#table-desc-input")?.value?.trim();
      if (tableDesc) body.table_description = tableDesc;
      const inputs = columnListEl.querySelectorAll(".col-desc-input");
      const columns = Array.from(inputs)
        .map((input) => ({
          column_id: input.dataset.colId,
          description: input.value.trim(),
        }))
        .filter((c) => c.column_id && c.description);
      if (columns.length) body.columns = columns;
    }

    let res;
    try {
      res = await api("/api/staging/save-all-comments", {
        method: "POST",
        body: JSON.stringify(body),
      });
    } catch (e) {
      const msg = String(e.message || "");
      if (msg === "Not Found" || msg.includes("404")) {
        res = await saveAllStagingCommentsFallback(update);
      } else {
        throw e;
      }
    }

    update({ message: "刷新列表…", step: 2, progress: 85 });
    await loadStagingTables($("#table-filter")?.value || "");
    if (currentStagingTableId) {
      const data = await api("/api/staging/tables");
      const t = (data.items || []).find((x) => x.table_id === currentStagingTableId);
      const el = tableListEl.querySelector(`[data-id="${currentStagingTableId}"]`);
      await loadStagingColumns(currentStagingTableId, el, t);
    }
    try {
      const wf = await api("/api/workflow");
      renderWorkflow(wf);
    } catch (_) {
      /* ignore */
    }
    const skipped = (res.skipped_columns || 0) + (res.skipped_tables || 0);
    const suffix = res.fallback
      ? "\n\n（服务未加载新接口，已走兼容模式。重启 python scripts/run_platform.py 后可更快完成）"
      : "";
    update({
      message: "完成",
      step: 2,
      stepStatus: "done",
      progress: 100,
      result: {
        message:
          (res.message ||
            `已保存 ${res.saved_tables ?? 0} 张表、${res.saved_columns ?? 0} 个字段的非空注释`) +
          (skipped ? `\n跳过空说明 ${skipped} 项` : "") +
          suffix,
        variant: "ok",
      },
    });
  });
}

async function saveAllColumnDescsOneByOne(columns) {
  let saved = 0;
  let skipped = 0;
  for (const col of columns) {
    if (!col.description) {
      skipped += 1;
      continue;
    }
    await api(`/api/staging/columns/${col.column_id}`, {
      method: "PUT",
      body: JSON.stringify({ description: col.description }),
    });
    saved += 1;
  }
  if (saved === 0) {
    throw new Error("没有可保存的字段注释（说明不能为空）");
  }
  return { saved, skipped, fallback: true };
}

async function saveAllColumnDescs() {
  if (!currentStagingTableId) return;
  const inputs = columnListEl.querySelectorAll(".col-desc-input");
  const columns = Array.from(inputs)
    .map((input) => ({
      column_id: input.dataset.colId,
      description: input.value.trim(),
    }))
    .filter((c) => c.column_id);
  if (!columns.length) {
    await notifyUser("当前表没有可保存的字段", { variant: "warn", title: "保存字段" });
    return;
  }
  let res;
  try {
    res = await api(`/api/staging/tables/${currentStagingTableId}/columns/batch`, {
      method: "POST",
      body: JSON.stringify({ columns }),
    });
  } catch (e) {
    const msg = String(e.message || "");
    if (msg === "Not Found" || msg.includes("404")) {
      res = await saveAllColumnDescsOneByOne(columns);
    } else {
      throw e;
    }
  }
  await loadStagingColumns(
    currentStagingTableId,
    tableListEl.querySelector(`.table-item[data-id="${currentStagingTableId}"]`),
    currentStagingTable
  );
  await loadStagingTables($("#table-filter")?.value || "");
  const skipped = res.skipped || 0;
  const suffix = res.fallback ? "（兼容模式：逐条保存）" : "";
  let message = null;
  if (skipped > 0) {
    message = `已保存 ${res.saved} 个字段注释，跳过 ${skipped} 个（空说明或不属于本表）${suffix}`;
  } else if (res.fallback || res.saved > 0) {
    message = `已保存 ${res.saved} 个字段注释${suffix}`;
  }
  if (message) await notifyUser(message, { variant: "ok", title: "保存字段" });
}

async function saveColumnDesc(columnId) {
  const input = columnListEl.querySelector(`input[data-col-id="${columnId}"]`);
  if (!input) return;
  try {
    await api(`/api/staging/columns/${columnId}`, {
      method: "PUT",
      body: JSON.stringify({ description: input.value.trim() }),
    });
    await loadStagingTables($("#table-filter")?.value || "");
    if (currentStagingTableId) {
      const el = tableListEl.querySelector(`[data-id="${currentStagingTableId}"]`);
      const data = await api("/api/staging/tables");
      const t = (data.items || []).find((x) => x.table_id === currentStagingTableId);
      loadStagingColumns(currentStagingTableId, el, t);
    }
  } catch (e) {
    await notifyError(e);
  }
}

async function loadJobs() {
  const data = await api("/api/jobs");
  const items = data.items || [];
  jobsListEl.innerHTML =
    items.length === 0
      ? '<div class="hint" style="padding:1rem">暂无任务记录</div>'
      : items
          .map(
            (j) => `
      <div class="job-item">
        <div><strong>${j.name}</strong>
          <span class="tag ${j.status === "success" ? "ok" : j.status === "failed" ? "wait" : ""}">${j.status}</span>
        </div>
        <div class="meta">${j.job_id} · ${j.finished_at || j.created_at}</div>
      </div>`
          )
          .join("");
}

async function handleAction(action, extraBody = {}) {
  const titles = {
    init: "初始化元数据表",
    scan: "扫描原始表",
    sync: "同步到元数据库",
    index: "构建向量索引",
  };

  $$("[data-action], #btn-staging-goto-sync").forEach((b) => (b.disabled = true));
  try {
    await withLoading(titles[action] || "正在执行", PIPELINE_STEPS[action] || ["提交任务", "执行任务", "刷新状态"], async (update) => {
      let scope = { scope: "full" };
      if (action === "scan" || action === "sync") scope = getMetaScope();
      let syncClearMsg = "";

      if (action === "scan") {
        await runPipeline("scan", { ...buildScanBody(scope), ...extraBody }, null, update);
      } else if (action === "sync") {
        const res = await runPipeline("sync", buildSyncBody(scope), null, update);
        syncClearMsg = formatSyncClearMessage(res.jobResult);
      } else if (action === "index") {
        const indexScope = scope.scope === "table" && scope.table_ids
          ? { table_ids: scope.table_ids }
          : {};
        await runPipeline("index", buildIndexBody(indexScope, false), vectorLogEl, update);
      } else {
        await runPipeline(action, extraBody, null, update);
      }
      if (action === "scan") {
        metaScanDone = true;
      } else if (action === "init") {
        metaScanDone = false;
      }
      update({ message: "正在刷新平台状态…", step: 2, stepStatus: "active", progress: 92 });
      await refreshAllCore(update);
      update({ message: "完成", step: 2, stepStatus: "done", progress: 100 });
      if (syncClearMsg) update({ result: { message: syncClearMsg, variant: "ok" } });
    }, { cancellable: true });
    if (NEXT_TAB_AFTER[action]) gotoTab(NEXT_TAB_AFTER[action]);
  } catch (e) {
    if (isLoadingCancelled(e)) return;
    await notifyError(e);
  } finally {
    $$("[data-action], #btn-staging-goto-sync").forEach((b) => (b.disabled = false));
  }
}

async function refreshAllCore(update = null) {
  const u = update || (() => {});
  throwIfCancelled();
  u({ message: "加载连接配置…", step: 0, stepStatus: "active", progress: 10 });
  await loadConnections().catch((e) => console.warn(e));
  throwIfCancelled();
  u({ step: 0, stepStatus: "done", progress: 25 });
  u({ message: "连接远程 MySQL 与 Qdrant…", step: 1, stepStatus: "active", progress: 30 });
  try {
    const overview = await api("/api/overview");
    throwIfCancelled();
    renderOverview(overview);
    u({ step: 1, stepStatus: "done", progress: 50 });
    await loadRawScopeTables().catch((e) => console.warn(e));
    await loadVectorScopeTables().catch((e) => console.warn(e));
    throwIfCancelled();
    u({ message: "检测工作流与环境…", step: 2, stepStatus: "active", progress: 55 });
    await loadWorkflowCore();
    throwIfCancelled();
    u({ step: 2, stepStatus: "done", progress: 72 });
    u({ message: "加载元数据暂存…", step: 3, stepStatus: "active", progress: 75 });
    await loadStagingTables($("#table-filter")?.value || "").catch((e) => console.warn(e));
    throwIfCancelled();
    if (window.L1Edit?.onTabShow && document.querySelector("#tab-metadata.active")) {
      await window.L1Edit.onTabShow("metadata").catch(() => {});
    }
    throwIfCancelled();
    u({ step: 3, stepStatus: "done", progress: 88 });
    u({ message: "加载任务历史…", step: 4, stepStatus: "active", progress: 90 });
    await loadJobs();
    u({ message: "完成", step: 4, stepStatus: "done", progress: 100 });
  } catch (e) {
    if (isLoadingCancelled(e)) throw new LoadingCancelledError();
    console.warn("部分状态加载失败（请先完成连接配置）:", e.message);
    u({ message: `部分加载失败: ${e.message}`, progress: 100 });
  }
}

async function refreshAll() {
  return withLoading("正在加载平台状态", REFRESH_STEPS, refreshAllCore, { cancellable: true });
}

/** 首次打开页面：静默加载，不弹进度条；连接未配全时失败可忽略 */
async function initApp() {
  try {
    await loadConnections();
  } catch (e) {
    console.warn("加载连接向导失败:", e.message);
  }
  try {
    await refreshAllCore();
  } catch (e) {
    if (!isLoadingCancelled(e)) {
      console.warn("初始状态加载跳过（请先完成连接配置）:", e.message);
    }
  }
}

$$(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => gotoTab(btn.dataset.tab));
});

document.querySelectorAll('input[name="meta-scope"]').forEach((r) => {
  r.addEventListener("change", updateScopeTableSelects);
});
document.querySelectorAll('input[name="vector-scope"]').forEach((r) => {
  r.addEventListener("change", () => {
    updateScopeTableSelects();
    const mode = document.querySelector('input[name="vector-scope"]:checked')?.value;
    if (mode === "column") {
      const tid = $("#vector-scope-table")?.value;
      if (tid) loadVectorColumns(tid);
    }
  });
});
$("#meta-scope-table")?.addEventListener("change", () => {});
$("#vector-scope-table")?.addEventListener("change", (e) => {
  if (document.querySelector('input[name="vector-scope"]:checked')?.value === "column") {
    loadVectorColumns(e.target.value);
  }
});

async function disposeDbPools({ connFeedback = false } = {}) {
  let res;
  try {
    res = await api("/api/connections/dispose-pools", { method: "POST" });
  } catch (e) {
    if (String(e.message).includes("Method Not Allowed") || String(e.message).includes("405")) {
      throw new Error("释放连接接口未生效，请先重启问数agent：python scripts/run_platform.py");
    }
    throw e;
  }
  const msg = res.message || "已释放 MySQL 连接池，可重新试连接";
  if (connFeedback) setConnResult(msg, true);
  else appendLog(msg);
  return res;
}

$("#btn-refresh").addEventListener("click", () => refreshAll().catch((e) => {
  if (!isLoadingCancelled(e)) appendLog(e.message);
}));

$("#btn-dispose-db")?.addEventListener("click", async () => {
  const btn = $("#btn-dispose-db");
  if (btn) btn.disabled = true;
  try {
    await disposeDbPools();
  } catch (e) {
    appendLog(`释放连接失败: ${e.message}`);
    await notifyError(e);
  } finally {
    if (btn) btn.disabled = false;
  }
});

$("#btn-conn-dispose")?.addEventListener("click", async () => {
  const btn = $("#btn-conn-dispose");
  if (btn) btn.disabled = true;
  try {
    await disposeDbPools({ connFeedback: true });
  } catch (e) {
    setConnResult(e.message, false);
  } finally {
    if (btn) btn.disabled = false;
  }
});

$("#loading-cancel")?.addEventListener("click", () => cancelLoading());

$("#loading-dismiss")?.addEventListener("click", () => {
  if (loadingDismissResolver) {
    loadingDismissResolver();
    loadingDismissResolver = null;
  }
});

$("#btn-staging-save-all")?.addEventListener("click", () => {
  saveAllStagingComments().catch((e) => {
    if (!isLoadingCancelled(e)) notifyError(e);
  });
});

$("#btn-staging-llm-tables")?.addEventListener("click", () => {
  runStagingLlm(buildLlmBody({ table_only: true }), "AI 补全全部表注释").catch((e) => {
    if (!isLoadingCancelled(e)) notifyError(e);
  });
});

$("#btn-staging-llm-all-columns")?.addEventListener("click", () => {
  runStagingLlmAllColumns().catch((e) => {
    if (!isLoadingCancelled(e)) notifyError(e);
  });
});

$("#btn-staging-clear")?.addEventListener("click", async () => {
  if (!confirm("确定清空整个暂存区？\n\n未同步到元数据库的表/字段编辑将全部丢失，此操作不可恢复。")) {
    return;
  }
  try {
    const res = await clearStaging(null);
    currentStagingTableId = null;
    currentStagingTable = null;
    columnListEl.innerHTML = "";
    if ($("#table-desc-edit")) $("#table-desc-edit").style.display = "none";
    const orphansPanel = $("#l1-orphans-panel");
    if (orphansPanel) orphansPanel.classList.add("hidden");
    await loadStagingTables($("#table-filter")?.value || "");
    await loadWorkflow();
    await notifyUser(res.message || "已清空", { variant: "ok", title: "清空暂存" });
  } catch (e) {
    await notifyError(e);
  }
});

$("#btn-staging-merge-l1")?.addEventListener("click", async () => {
  try {
    await withLoading("一键匹配元数据库", ["匹配元数据库", "刷新列表"], async (update) => {
      update({ message: "正在从元数据库合并同名表/字段说明…", step: 0, stepStatus: "active", progress: 30 });
      const res = await mergeStagingFromL1(null);
      update({ message: "刷新暂存列表…", step: 1, stepStatus: "active", progress: 85 });
      await loadStagingTables($("#table-filter")?.value || "");
      if (currentStagingTableId) {
        const el = tableListEl.querySelector(`[data-id="${currentStagingTableId}"]`);
        const data = await api("/api/staging/tables");
        const t = (data.items || []).find((x) => x.table_id === currentStagingTableId);
        await loadStagingColumns(currentStagingTableId, el, t);
      }
      update({
        message: "完成",
        step: 1,
        stepStatus: "done",
        progress: 100,
        result: { message: res.message || "已合并", variant: "ok" },
      });
    });
  } catch (e) {
    if (!isLoadingCancelled(e)) await notifyError(e);
  }
});

$("#btn-llm-table")?.addEventListener("click", () => {
  if (!currentStagingTableId) return;
  runStagingLlm(
    buildLlmBody({ table_ids: [currentStagingTableId], columns_only: true }),
    "AI 补全字段注释"
  ).catch((e) => {
    if (!isLoadingCancelled(e)) notifyError(e);
  });
});

$("#btn-llm-table-desc")?.addEventListener("click", () => {
  if (!currentStagingTableId) return;
  runStagingLlm(
    buildLlmBody({
      table_ids: [currentStagingTableId],
      table_only: true,
    }),
    "AI 补全表注释"
  ).catch((e) => {
    if (!isLoadingCancelled(e)) notifyError(e);
  });
});

$("#btn-staging-goto-sync")?.addEventListener("click", async () => {
  try {
    const scope = getStagingSyncScope();
    await withLoading("同步暂存区到元数据库", PIPELINE_STEPS.sync, async (update) => {
      const res = await runPipeline("sync", buildSyncBody(scope), null, update);
      const clearMsg = formatSyncClearMessage(res.jobResult);
      update({ message: "正在刷新平台状态…", step: 2, stepStatus: "active", progress: 92 });
      await refreshAllCore(update);
      update({ message: "完成", step: 2, stepStatus: "done", progress: 100 });
      if (clearMsg) update({ result: { message: clearMsg, variant: "ok" } });
    }, { cancellable: true });
  } catch (e) {
    if (isLoadingCancelled(e)) return;
    await notifyError(e);
  }
});

$("#btn-save-table-desc")?.addEventListener("click", async () => {
  if (!currentStagingTableId) return;
  try {
    await api(`/api/staging/tables/${currentStagingTableId}`, {
      method: "PUT",
      body: JSON.stringify({ description: $("#table-desc-input").value.trim() }),
    });
    await loadStagingTables($("#table-filter")?.value || "");
  } catch (e) {
    await notifyError(e);
  }
});

$("#btn-save-all-cols")?.addEventListener("click", async () => {
  if (!currentStagingTableId) return;
  try {
    await saveAllColumnDescs();
  } catch (e) {
    await notifyError(e);
  }
});

$("#btn-vector-run")?.addEventListener("click", async () => {
  try {
    await withLoading("构建向量索引", PIPELINE_STEPS.index, async (update) => {
      const scope = getVectorScope();
      await runPipeline("index", buildIndexBody(scope, false), vectorLogEl, update);
      update({ message: "刷新状态…", step: 4, stepStatus: "active", progress: 92 });
      await refreshAllCore(update);
    }, { cancellable: true });
    gotoTab("search");
  } catch (e) {
    if (isLoadingCancelled(e)) {
      appendLog("已取消操作", vectorLogEl);
      return;
    }
    appendLog(e.message, vectorLogEl);
    await notifyError(e);
  }
});

$("#btn-vector-full")?.addEventListener("click", async () => {
  try {
    await withLoading("全量重建向量", PIPELINE_STEPS.index, async (update) => {
      const scope = getVectorScope();
      await runPipeline("index", buildIndexBody(scope, true), vectorLogEl, update);
      update({ message: "刷新状态…", step: 4, stepStatus: "active", progress: 92 });
      await refreshAllCore(update);
    }, { cancellable: true });
    gotoTab("search");
  } catch (e) {
    if (isLoadingCancelled(e)) {
      appendLog("已取消操作", vectorLogEl);
      return;
    }
    appendLog(e.message, vectorLogEl);
    await notifyError(e);
  }
});

$("#table-filter")?.addEventListener("input", (e) => loadStagingTables(e.target.value));

let searchScopeState = { currentRawDb: null, indexed: [] };

async function loadSearchScope() {
  const dbEl = $("#search-current-db");
  const hintEl = $("#search-scope-hint");
  try {
    const data = await api("/api/search/databases");
    searchScopeState.currentRawDb = data.current_raw_database || null;
    searchScopeState.indexed = data.indexed_databases || [];
    if (dbEl) {
      dbEl.textContent = searchScopeState.currentRawDb
        ? `（${searchScopeState.currentRawDb}）`
        : "（未配置）";
    }
    if (hintEl) {
      const listed = searchScopeState.indexed.length
        ? searchScopeState.indexed.join("、")
        : "暂无";
      hintEl.textContent = `已索引库：${listed}。默认仅搜当前原始库；勾选「全部已索引库」可跨库召回（表/字段按 db 过滤，指标/文档仍全局）。`;
    }
  } catch (e) {
    if (hintEl) hintEl.textContent = "无法加载库范围配置";
  }
}

function buildSearchBody(query, limit = 10, extra = {}) {
  const allDbs = $("#search-scope-all")?.checked;
  const evidence = $("#search-evidence")?.value?.trim() || "";
  return {
    query,
    limit,
    all_databases: !!allDbs,
    evidence,
    keyword_mode: "auto",
    column_select: true,
    ...extra,
  };
}

let searchClarifyState = {
  sessionId: null,
  query: "",
  evidence: "",
};

function formatSemanticGraphSummary(graph) {
  if (!graph || typeof graph !== "object") return "";
  const typeLabels = {
    attribute_lookup: "查属性",
    fact_filter: "条件筛选",
    aggregation: "统计汇总",
    event_detail: "查明细",
    multi_fact: "多指标",
    existence: "是否存在",
    unknown: "未判定",
  };
  const label = typeLabels[graph.query_type] || graph.query_type || "未判定";
  const action =
    graph.query_action && graph.query_action !== "unknown" ? ` · ${graph.query_action}` : "";
  const multi = graph.force_multi_table ? " · 跨表" : "";
  return `语义图：${label}${action}${multi}`;
}

function renderClarifyPanel(data) {
  const qs = data.clarify_questions || [];
  if (!qs.length) return "";
  const items = qs
    .map(
      (q, idx) => `
    <div class="form-field" style="margin-top:0.5rem">
      <label>${q.prompt || q.id}</label>
      <input class="clarify-answer" data-qid="${q.id || idx}" type="text"
        placeholder="${(q.options && q.options[0]) || "请输入补充说明"}" />
      ${
        Array.isArray(q.options) && q.options.length
          ? `<div class="hint" style="margin-top:0.25rem">可选：${q.options.join("、")}</div>`
          : ""
      }
    </div>`
    )
    .join("");
  return `<div class="card" style="margin-bottom:0.75rem;padding:0.75rem;border:1px solid var(--border)">
    <strong>需要澄清</strong>
    <div class="hint" style="margin:0.35rem 0">${formatSemanticGraphSummary(data.semantic_graph)}</div>
    ${items}
    <button id="btn-clarify-resume" class="btn" type="button" style="margin-top:0.75rem">补充后继续检索</button>
  </div>`;
}

function bindClarifyResumeHandler(data) {
  const btn = $("#btn-clarify-resume");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const answers = {};
    document.querySelectorAll(".clarify-answer").forEach((el) => {
      const qid = el.getAttribute("data-qid");
      const val = el.value.trim();
      if (qid && val) answers[qid] = val;
    });
    if (!Object.keys(answers).length) {
      searchResultEl.innerHTML = '<div class="hint status-bad">请至少填写一项澄清回答</div>';
      return;
    }
    searchResultEl.innerHTML = '<div class="hint">澄清已提交，继续检索…</div>';
    try {
      await runSearchRequest(data.query || searchClarifyState.query, {
        session_id: data.session_id || searchClarifyState.sessionId,
        clarify_answers: answers,
        evidence: data.evidence || searchClarifyState.evidence,
      });
    } catch (e) {
      searchResultEl.innerHTML = `<div class="hint status-bad">${e.message}</div>`;
    }
  });
}

async function runSearchRequest(query, extra = {}) {
  const data = await api("/api/search/test", {
    method: "POST",
    body: JSON.stringify(buildSearchBody(query, 10, extra)),
  });
  if (data.status === "need_clarify") {
    searchClarifyState = {
      sessionId: data.session_id,
      query: data.query,
      evidence: data.evidence || "",
    };
    searchResultEl.innerHTML = renderClarifyPanel(data);
    bindClarifyResumeHandler(data);
    return;
  }
  searchClarifyState = { sessionId: null, query: "", evidence: "" };
  const scopeLabel =
    data.filter_mode === "all"
      ? "全部已索引库"
      : data.filter_mode === "current_raw"
        ? `当前原始库 ${(data.filter_databases || [])[0] || ""}`
        : (data.filter_databases || []).join("、") || "未限定";
  const tables = (data.selected_tables || []).join("、") || "—";
  const expanded = (data.expanded_tables || []).join("、") || "—";
  const graphHint = data.semantic_graph
    ? `<div class="hint" style="margin-bottom:0.5rem">${formatSemanticGraphSummary(data.semantic_graph)}${
        data.clarify_rounds ? ` · 澄清 ${data.clarify_rounds} 轮` : ""
      }</div>`
    : "";
  const header = `<div class="hint" style="margin-bottom:0.5rem">检索范围：${scopeLabel} · 模式：${data.retrieval_mode || "xiyan"}</div>
    ${graphHint}
    ${formatRoleSummary(data.query_roles)}
    <div class="hint" style="margin-bottom:0.5rem">定表：${tables}<br/>扩展表：${expanded}</div>
    ${formatSelectedColumns(data.s1_columns, "S1 精选", (data.selection_meta || {}).s1_source)}
    ${formatSelectedColumns(data.s2_columns, "S2 精选", (data.selection_meta || {}).s2_source)}`;
  searchResultEl.innerHTML =
    header +
    (data.hits || [])
      .map(
        (h) => `
      <div class="hit-card">
        <span class="score">${(h.score * 100).toFixed(1)}%</span>
        <strong>${h.type || ""}</strong>
        ${h.table || ""}${h.column ? "." + h.column : ""}
        <div class="path">${h.db || ""}.${h.table || ""}${h.column ? " · " + h.column : ""}${h.source ? " · " + h.source : ""}</div>
      </div>`
      )
      .join("");
}

function formatRoleSummary(roles) {
  if (!roles || typeof roles !== "object") return "";
  const lines = [];
  const intent = roles.intent && typeof roles.intent === "object" ? roles.intent : {};
  const typeLabels = {
    attribute_lookup: "查属性",
    fact_filter: "条件筛选",
    aggregation: "统计汇总",
    event_detail: "查明细",
    multi_fact: "多指标",
    existence: "是否存在",
    unknown: "未判定",
  };
  if (intent.query_type) {
    const label = typeLabels[intent.query_type] || intent.query_type;
    const action = intent.query_action && intent.query_action !== "unknown" ? ` · ${intent.query_action}` : "";
    const multi = roles.force_multi_table ? " · 跨表" : "";
    lines.push(`意图：${label}${action}${multi}`);
  }
  const pairs = [
    ["表", roles.table_phrases],
    ["列", roles.column_phrases],
    ["过滤", roles.filter_phrases],
    ["关系", roles.join_phrases],
  ];
  for (const [label, arr] of pairs) {
    if (Array.isArray(arr) && arr.length) {
      lines.push(`${label}：${arr.join("、")}`);
    }
  }
  if (!lines.length && Array.isArray(roles.keywords) && roles.keywords.length) {
    lines.push(`关键词：${roles.keywords.join("、")}`);
  }
  const src = roles.source || "";
  return lines.length
    ? `<div class="hint" style="margin-bottom:0.5rem">分角色拆解${src ? `（${src}）` : ""}<br/>${lines.join("<br/>")}</div>`
    : "";
}

function formatSelectedColumns(cols, title, metaSource) {
  const src = metaSource ? ` · ${metaSource}` : "";
  if (!Array.isArray(cols) || !cols.length) {
    return `<div class="hint" style="margin-bottom:0.5rem">${title}：—${src}</div>`;
  }
  const names = cols
    .map((c) => `${c.table || ""}${c.column ? "." + c.column : ""}`)
    .join("、");
  return `<div class="hint" style="margin-bottom:0.5rem">${title}（${cols.length} 列）${src}<br/>${names}</div>`;
}

function syncSearchScopeCheckboxes(changed) {
  const cur = $("#search-scope-current");
  const all = $("#search-scope-all");
  if (!cur || !all) return;
  if (changed === cur && cur.checked) all.checked = false;
  if (changed === all && all.checked) cur.checked = false;
  if (!cur.checked && !all.checked) cur.checked = true;
}

$("#search-scope-current")?.addEventListener("change", (e) => syncSearchScopeCheckboxes(e.target));
$("#search-scope-all")?.addEventListener("change", (e) => syncSearchScopeCheckboxes(e.target));

$("#btn-search").addEventListener("click", async () => {
  const query = $("#search-input").value.trim();
  if (!query) return;
  const mschemaEl = $("#mschema-output");
  if (mschemaEl) {
    mschemaEl.classList.add("hidden");
    mschemaEl.textContent = "";
  }
  searchClarifyState = { sessionId: null, query: "", evidence: "" };
  searchResultEl.innerHTML = '<div class="hint">检索中...</div>';
  try {
    await runSearchRequest(query);
  } catch (e) {
    searchResultEl.innerHTML = `<div class="hint status-bad">${e.message}</div>`;
  }
});

$("#btn-mschema")?.addEventListener("click", async () => {
  const query = $("#search-input").value.trim();
  if (!query) return;
  const mschemaEl = $("#mschema-output");
  searchResultEl.innerHTML = '<div class="hint">正在生成 M-Schema…</div>';
  if (mschemaEl) {
    mschemaEl.classList.add("hidden");
    mschemaEl.textContent = "";
  }
  try {
    const scope = buildSearchBody(query, 30);
    const data = await api("/api/nl2sql/mschema", {
      method: "POST",
      body: JSON.stringify({
        question: query,
        evidence: scope.evidence || "",
        limit: scope.limit || 30,
        all_databases: scope.all_databases,
        keyword_mode: scope.keyword_mode,
        column_select: scope.column_select,
        schema_stage: "s2",
        include_relations: true,
        include_examples: true,
        example_num: 3,
      }),
    });
    const sel = data.selection || {};
    const meta = data.selection_meta || {};
    const s2n = (data.s2_columns || []).length;
    searchResultEl.innerHTML = `<div class="hint">库：${data.db_name || ""} · 阶段 ${data.schema_stage || "s2"} · S2 ${s2n} 列 · 表 ${
      (sel.selected_tables || []).join("、") || "—"
    }${meta.intent ? ` · ${meta.intent}` : ""}</div>`;
    if (mschemaEl) {
      mschemaEl.textContent = data.mschema || "";
      mschemaEl.classList.remove("hidden");
    }
  } catch (e) {
    searchResultEl.innerHTML = `<div class="hint status-bad">${e.message}</div>`;
  }
});

$("#btn-conn-test")?.addEventListener("click", async () => {
  if (!connCurrentRole || !connCurrentEngine) return;
  setConnResult("正在试连接...", null);
  $("#btn-conn-test").disabled = true;
  try {
    const res = await api("/api/connections/test", {
      method: "POST",
      body: JSON.stringify({
        role: connCurrentRole,
        engine: connCurrentEngine,
        values: collectConnFormValues(),
      }),
    });
    setConnResult(res.message || (res.ok ? "连接成功" : "连接失败"), !!res.ok);
  } catch (e) {
    setConnResult(e.message, false);
  } finally {
    $("#btn-conn-test").disabled = false;
  }
});

$("#btn-conn-save")?.addEventListener("click", async () => {
  if (!connCurrentRole || !connCurrentEngine) return;
  setConnResult("正在保存...", null);
  $("#btn-conn-save").disabled = true;
  try {
    const res = await api(`/api/connections/${connCurrentRole}`, {
      method: "PUT",
      body: JSON.stringify({
        engine: connCurrentEngine,
        values: collectConnFormValues(),
      }),
    });
    connValuesCache[connCurrentRole] = res;
    let saveMsg = res.message || "已保存";
    let saveOk = true;
    if (
      (connCurrentRole === "raw" || connCurrentRole === "meta") &&
      connCurrentEngine === "mysql"
    ) {
      const verify = await api("/api/connections/test", {
        method: "POST",
        body: JSON.stringify({ role: connCurrentRole, engine: connCurrentEngine, values: {} }),
      });
      if (!verify.ok) {
        saveMsg = `已写入配置，但用已保存凭证试连失败：${verify.message}`;
        saveOk = false;
      } else {
        saveMsg = `${saveMsg} · ${verify.message}`;
      }
    }
    setConnResult(saveMsg, saveOk);
    if (!saveOk) return;
    if (connRoleIndex < wizardSteps.length - 1) {
      connRoleIndex += 1;
      connCurrentEngine = null;
      await loadConnections();
      setConnResult("上一步已保存，请继续配置", true);
    } else {
      setConnResult("已保存，正在检测环境…", true);
      try {
        resetMetaScanSession();
        await withLoading("正在进入元数据同步", REFRESH_STEPS, refreshAllCore, { cancellable: true });
        gotoTab("meta-sync");
        let finishMsg = "配置完成，已进入元数据同步";
        let finishOk = true;
        try {
          const overview = await api("/api/overview");
          const metaErr = (overview.connection_errors || []).find((e) => e.role === "meta");
          if (metaErr) {
            finishMsg = `向量库已保存，但元数据库连不上：${friendlyDbError(metaErr.message)}。请回到第②步核对 metadata_vector 账号密码`;
            finishOk = false;
          }
        } catch (_) {}
        setConnResult(finishMsg, finishOk);
      } catch (e) {
        if (isLoadingCancelled(e)) {
          setConnResult("已取消加载。连接已保存，可稍后点击「刷新状态」或左侧进入「元数据同步」", true);
          appendLog("用户取消了加载");
        } else {
          setConnResult(`已保存，但加载失败: ${e.message}`, false);
        }
      }
    }
  } catch (e) {
    setConnResult(e.message, false);
  } finally {
    $("#btn-conn-save").disabled = false;
  }
});

$("#btn-conn-prev")?.addEventListener("click", () => {
  if (connRoleIndex <= 0) return;
  connRoleIndex -= 1;
  connCurrentEngine = null;
  renderWizardStep();
});

$("#btn-conn-parse")?.addEventListener("click", async () => {
  if (!connCurrentRole || !connCurrentEngine) {
    setConnResult("请先选择平台类型", false);
    return;
  }
  const text = $("#conn-paste")?.value?.trim();
  if (!text) {
    setConnResult("请先粘贴连接串或配置文本", false);
    return;
  }
  try {
    const res = await api("/api/connections/parse", {
      method: "POST",
      body: JSON.stringify({
        role: connCurrentRole,
        engine: connCurrentEngine,
        text,
      }),
    });
    if (!res.ok) {
      setConnResult(res.message || "解析失败", false);
      return;
    }
    fillConnForm(res.values);
    setConnResult(res.message || "已填入，请核对后试连接", true);
  } catch (e) {
    setConnResult(e.message, false);
  }
});

function setModelSettingsStatus(text, ok = true) {
  const el = $("#model-settings-status");
  if (!el) return;
  el.textContent = text || "";
  el.style.color = ok ? "var(--muted)" : "var(--danger, #c0392b)";
}

function fillModelSettingsForm(data) {
  const emb = data?.embedding || {};
  const llm = data?.llm || {};
  const set = (id, val) => {
    const el = $(id);
    if (el && val !== undefined && val !== null) el.value = val;
  };
  set("#ms-emb-provider", emb.provider || "auto");
  set("#ms-emb-local-model", emb.local_model || "");
  set("#ms-emb-local-device", emb.local_device || "auto");
  set("#ms-emb-model", emb.model || "");
  set("#ms-emb-api-base", emb.api_base || "");
  set("#ms-emb-dim", emb.dim || 1024);
  set("#ms-llm-provider", llm.provider || "auto");
  set("#ms-llm-ollama-url", llm.ollama_url || "");
  set("#ms-llm-ollama-model", llm.ollama_model || "");
  set("#ms-llm-model", llm.model || "");
  set("#ms-llm-metadata-model", llm.metadata_model || "");
  set("#ms-llm-api-base", llm.api_base || "");
  const embKey = $("#ms-emb-api-key");
  const llmKey = $("#ms-llm-api-key");
  if (embKey) embKey.value = "";
  if (llmKey) llmKey.value = "";
  if (embKey) embKey.placeholder = emb.api_key_set ? "已保存（留空不修改）" : "API Key";
  if (llmKey) llmKey.placeholder = llm.api_key_set ? "已保存（留空不修改）" : "API Key";
  setModelSettingsStatus(
    `当前：Embedding=${emb.resolved_provider || "—"} · LLM=${llm.resolved_provider || "—"}`,
    emb.ok !== false && llm.ok !== false
  );
}

function collectModelSettingsPayload() {
  const val = (id) => $(id)?.value?.trim() ?? "";
  const num = (id) => {
    const n = parseInt(val(id), 10);
    return Number.isFinite(n) ? n : 1024;
  };
  const payload = {
    embedding: {
      provider: val("#ms-emb-provider") || "auto",
      local_model: val("#ms-emb-local-model"),
      local_device: val("#ms-emb-local-device") || "auto",
      model: val("#ms-emb-model"),
      api_base: val("#ms-emb-api-base"),
      dim: num("#ms-emb-dim"),
    },
    llm: {
      provider: val("#ms-llm-provider") || "auto",
      ollama_url: val("#ms-llm-ollama-url"),
      ollama_model: val("#ms-llm-ollama-model"),
      model: val("#ms-llm-model"),
      metadata_model: val("#ms-llm-metadata-model"),
      api_base: val("#ms-llm-api-base"),
    },
  };
  const embKey = val("#ms-emb-api-key");
  const llmKey = val("#ms-llm-api-key");
  if (embKey) payload.embedding.api_key = embKey;
  if (llmKey) payload.llm.api_key = llmKey;
  return payload;
}

async function loadModelSettings() {
  try {
    const data = await api("/api/model-settings");
    fillModelSettingsForm(data);
  } catch (e) {
    setModelSettingsStatus(`加载模型配置失败: ${e.message}`, false);
  }
}

$("#btn-model-save")?.addEventListener("click", async () => {
  try {
    setModelSettingsStatus("正在保存…");
    const res = await api("/api/model-settings", {
      method: "PUT",
      body: JSON.stringify(collectModelSettingsPayload()),
    });
    fillModelSettingsForm(res);
    setModelSettingsStatus(res.message || "已保存", true);
    await loadWorkflowCore().catch(() => {});
  } catch (e) {
    setModelSettingsStatus(e.message, false);
  }
});

$("#btn-model-test-emb")?.addEventListener("click", async () => {
  try {
    setModelSettingsStatus("正在测试 Embedding（会先保存当前表单）…");
    await api("/api/model-settings", {
      method: "PUT",
      body: JSON.stringify(collectModelSettingsPayload()),
    });
    const res = await api("/api/model-settings/test-embedding", { method: "POST" });
    setModelSettingsStatus(res.message || (res.ok ? "Embedding 正常" : "Embedding 失败"), !!res.ok);
    await loadWorkflowCore().catch(() => {});
  } catch (e) {
    setModelSettingsStatus(e.message, false);
  }
});

$("#btn-model-test-llm")?.addEventListener("click", async () => {
  try {
    setModelSettingsStatus("正在测试 LLM（会先保存当前表单）…");
    await api("/api/model-settings", {
      method: "PUT",
      body: JSON.stringify(collectModelSettingsPayload()),
    });
    const res = await api("/api/model-settings/test-llm", { method: "POST" });
    setModelSettingsStatus(res.message || (res.ok ? "LLM 正常" : "LLM 失败"), !!res.ok);
    await loadWorkflowCore().catch(() => {});
  } catch (e) {
    setModelSettingsStatus(e.message, false);
  }
});

initApp();
