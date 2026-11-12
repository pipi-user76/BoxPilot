/*
 * 文件职责：任务工作台的前端逻辑，负责服务端搜索/分页、批量清理、取消与重试、
 * 产物预览以及定时轮询。所有业务文本都通过 textContent 写入，避免任务参数被当作 HTML 注入。
 */

const taskTypeLabels = {
  MOCK: "开发验证",
  CSV_ANALYSIS: "CSV 数据分析",
  WEB_DIGEST: "网页资料整理",
  REPO_CHECK: "代码仓库体检",
  WEB_ACCEPTANCE: "网页应用验收",
};

const statusLabels = {
  PENDING: "等待中",
  RUNNING: "运行中",
  SUCCESS: "成功",
  FAILED: "失败",
  TIMEOUT: "超时",
  CANCELLED: "已取消",
};

// 只有失败、超时和已取消的任务可以重试；成功任务重新执行没有业务意义。
const retryableStatuses = new Set(["FAILED", "TIMEOUT", "CANCELLED"]);

// 兼容已经存在的任务记录：旧数据中的内部英文步骤名也显示为中文。
const stepDisplayNames = {
  prepare_workspace: "准备任务目录",
  sandbox_echo: "执行沙箱验证",
  archive_result: "归档验证结果",
  validate_acceptance_input: "校验验收参数",
  open_target_page: "打开目标页面",
  capture_acceptance_screenshot: "截取整页截图",
  write_acceptance_report: "生成验收报告",
  validate_web_url: "校验网页地址",
  open_web_page: "打开网页",
  save_page_text: "提取正文并去噪",
  extract_article: "提取正文并去噪",
  capture_page_screenshot: "截取整页截图",
  write_web_digest_report: "生成整理报告",
  validate_csv_upload: "校验 CSV 文件",
  upload_csv_to_sandbox: "上传分析文件",
  run_csv_analyzer: "执行数据分析",
  save_csv_summary: "生成分析报告",
  validate_repository: "校验仓库地址",
  clone_repository: "获取仓库代码",
  detect_toolchain: "识别项目类型",
  run_repository_tests: "运行项目测试",
  write_check_report: "生成检查报告",
};

const state = {
  q: "",
  status: "",
  page: 1,
  pageSize: 20,
  pages: 1,
  total: 0,
  items: [],
  selected: new Set(),
};

let selectedTaskId = new URLSearchParams(window.location.search).get("task");
let refreshTimer = null;
let searchTimer = null;

const elements = {
  list: document.querySelector("#task-list"),
  empty: document.querySelector("#empty-state"),
  health: document.querySelector("#health"),
  refresh: document.querySelector("#refresh"),
  search: document.querySelector("#task-search"),
  filter: document.querySelector("#status-filter"),
  pageSize: document.querySelector("#page-size"),
  detail: document.querySelector("#detail-panel"),
  lastUpdated: document.querySelector("#last-updated"),
  selectionBar: document.querySelector("#selection-bar"),
  selectionCount: document.querySelector("#selection-count"),
  selectPage: document.querySelector("#select-page"),
  batchDelete: document.querySelector("#batch-delete"),
  cleanup: document.querySelector("#cleanup"),
  pagePrev: document.querySelector("#page-prev"),
  pageNext: document.querySelector("#page-next"),
  pageInfo: document.querySelector("#page-info"),
  previewDialog: document.querySelector("#preview-dialog"),
  previewTitle: document.querySelector("#preview-title"),
  previewBody: document.querySelector("#preview-body"),
  previewMeta: document.querySelector("#preview-meta"),
  previewDownload: document.querySelector("#preview-download"),
  previewClose: document.querySelector("#preview-close"),
  counts: {
    total: document.querySelector("#task-count"),
    running: document.querySelector("#running-count"),
    success: document.querySelector("#success-count"),
    failed: document.querySelector("#failed-count"),
  },
};

function element(tag, className, text) {
  // 统一使用 DOM API 创建节点，而不是拼接 innerHTML，降低 XSS 风险。
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function formatSize(bytes) {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function shortId(taskId) {
  return `#${taskId.slice(0, 8)}`;
}

async function api(url, options = {}) {
  // 所有接口错误优先读取后端结构化 detail，保证页面显示可理解的信息。
  const response = await fetch(url, {
    headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}) },
    ...options,
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail;
    const message = detail?.message || detail || `请求失败 (${response.status})`;
    throw new Error(message);
  }
  return body;
}

function buildListUrl() {
  const params = new URLSearchParams();
  if (state.q) params.set("q", state.q);
  if (state.status) params.set("status", state.status);
  params.set("page", String(state.page));
  params.set("page_size", String(state.pageSize));
  return `/tasks?${params.toString()}`;
}

function renderCounts(counts = {}) {
  // counts 由后端按全量任务统计，不受当前搜索和状态筛选影响。
  elements.counts.total.textContent = String(counts.total ?? 0);
  elements.counts.running.textContent = String((counts.PENDING ?? 0) + (counts.RUNNING ?? 0));
  elements.counts.success.textContent = String(counts.SUCCESS ?? 0);
  elements.counts.failed.textContent = String((counts.FAILED ?? 0) + (counts.TIMEOUT ?? 0));
}

function actionButton(label, variant, handler) {
  const button = element("button", `button small ${variant}`, label);
  button.type = "button";
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    handler();
  });
  return button;
}

function renderRow(task) {
  const row = element("div", "task-row");
  row.dataset.taskId = task.id;
  row.setAttribute("aria-current", String(task.id === selectedTaskId));

  const check = document.createElement("input");
  check.type = "checkbox";
  check.className = "row-select";
  check.checked = state.selected.has(task.id);
  check.setAttribute("aria-label", `选择任务 ${shortId(task.id)}`);
  check.addEventListener("change", () => {
    if (check.checked) state.selected.add(task.id);
    else state.selected.delete(task.id);
    updateSelectionBar();
  });

  const main = element("button", "task-main");
  main.type = "button";
  const name = element("div", "task-name");
  const attemptText = task.attempt > 1 ? ` · 第 ${task.attempt} 次尝试` : "";
  name.append(
    element("strong", "", taskTypeLabels[task.task_type] || task.task_type),
    element("span", "task-meta", `${shortId(task.id)} · ${task.steps.length} 个步骤${attemptText}`),
  );

  const badge = element("span", "status-badge", statusLabels[task.status] || task.status);
  badge.dataset.status = task.status;

  main.append(name, badge, element("span", "task-time", formatTime(task.created_at)));
  main.addEventListener("click", () => selectTask(task.id));

  const actions = element("div", "row-actions");
  if (task.status === "PENDING" || task.status === "RUNNING") {
    actions.append(actionButton("取消", "danger", () => requestCancel(task)));
  } else if (retryableStatuses.has(task.status)) {
    actions.append(actionButton("重试", "secondary", () => requestRetry(task)));
  }

  row.append(check, main, actions);
  return row;
}

function renderTaskList() {
  elements.list.replaceChildren();
  state.items.forEach((task) => elements.list.append(renderRow(task)));

  const empty = state.items.length === 0;
  elements.empty.hidden = !empty;
  if (empty) {
    const filtered = state.q || state.status;
    elements.empty.querySelector("strong").textContent = filtered ? "没有匹配的任务" : "暂无任务";
    elements.empty.querySelector("span").textContent = filtered
      ? "换一个关键词或状态再试。"
      : "创建任务后将在这里显示状态和产物。";
  }
  updateSelectionBar();
}

function updateSelectionBar() {
  const pageIds = state.items.map((task) => task.id);
  const selectedOnPage = pageIds.filter((id) => state.selected.has(id)).length;

  elements.selectionBar.hidden = state.selected.size === 0 && selectedOnPage === 0;
  elements.selectionCount.textContent = `已选 ${state.selected.size} 项`;
  elements.selectPage.checked = pageIds.length > 0 && selectedOnPage === pageIds.length;
  elements.selectPage.indeterminate = selectedOnPage > 0 && selectedOnPage < pageIds.length;
}

function renderPagination() {
  elements.pageInfo.textContent = `第 ${state.page} / ${Math.max(1, state.pages)} 页 · 共 ${state.total} 项`;
  elements.pagePrev.disabled = state.page <= 1;
  elements.pageNext.disabled = state.page >= state.pages;
}

function technicalDetails(text) {
  const details = element("details", "technical-details");
  details.append(element("summary", "", "技术详情"), element("pre", "", text));
  return details;
}

function renderStep(step) {
  const item = element("li", "step-item");
  const content = element("div");
  const stepName = stepDisplayNames[step.name] || step.name;
  content.append(element("strong", "", `${step.step_index}. ${stepName}`));
  if (step.error_message) {
    // 失败步骤先给用户看得懂的说明，原始命令输出收进折叠区。
    content.append(element("small", "error-text", step.error_message));
    if (step.output) content.append(technicalDetails(step.output));
  } else if (step.output) {
    content.append(element("small", "", step.output));
  }
  const badge = element("span", "status-badge", statusLabels[step.status] || step.status);
  badge.dataset.status = step.status;
  item.append(content, badge);
  return item;
}

function resultGridItem(label, value) {
  const item = element("div", "result-grid-item");
  item.append(element("dt", "", label), element("dd", "", value ?? "-"));
  return item;
}

function renderResultSummary(task) {
  const summary = task.result?.summary;
  if (!summary || !summary.kind) return null;

  let outcome = "neutral";
  if (summary.kind === "web_acceptance") outcome = summary.passed ? "passed" : "failed";
  if (summary.kind === "repo_check") {
    outcome = summary.test_status === "passed" ? "passed" : summary.test_status === "not_run" ? "neutral" : "failed";
  }

  const section = element("section", "detail-section");
  section.append(element("h3", "", "任务结论"));
  const panel = element("div", "result-summary");
  panel.dataset.outcome = outcome;
  panel.append(element("div", "result-summary-title", summary.message || "任务已完成"));
  const grid = element("dl", "result-grid");

  if (summary.kind === "web_acceptance") {
    grid.append(
      resultGridItem("预期标题", summary.expected_title),
      resultGridItem("实际标题", summary.actual_title),
      resultGridItem("页面状态", summary.http_status ? `HTTP ${summary.http_status}` : "未获取"),
    );
  } else if (summary.kind === "csv_analysis") {
    grid.append(
      resultGridItem("数据行数", summary.rows),
      resultGridItem("数据列数", summary.columns),
      resultGridItem("重复行", summary.duplicate_rows),
      resultGridItem("缺失值", summary.missing_values),
    );
  } else if (summary.kind === "web_digest") {
    grid.append(
      resultGridItem("页面标题", summary.title || "未获取"),
      resultGridItem("正文提取", summary.extraction === "text" ? "纯文本回退" : "Readability 去噪"),
      resultGridItem("正文词数", `${summary.words ?? 0} 词`),
      resultGridItem("预计阅读", `${summary.reading_time_minutes ?? 1} 分钟`),
      resultGridItem("页面状态", summary.http_status ? `HTTP ${summary.http_status}` : "未获取"),
    );
  } else if (summary.kind === "repo_check") {
    const toolchainLabels = {
      python: "Python 项目",
      "python-unittest": "Python 标准库测试项目",
      node: "Node.js 项目",
      unknown: "未识别",
    };
    grid.append(
      resultGridItem("项目类型", toolchainLabels[summary.toolchain] || summary.toolchain),
      resultGridItem("退出码", summary.exit_code ?? "未运行"),
    );
  } else if (summary.kind === "mock") {
    grid.append(resultGridItem("命令输出", summary.command_output || "无输出"));
  }

  panel.append(grid);
  section.append(panel);
  return section;
}

function renderArtifact(artifact) {
  const item = element("li", "artifact-item");
  const content = element("div");
  content.append(
    element("strong", "", artifact.filename),
    element("small", "", formatSize(artifact.size_bytes)),
  );
  const actions = element("div", "artifact-actions");
  actions.append(
    actionButton("预览", "secondary", () => openPreview(artifact)),
  );
  const link = element("a", "button small secondary", "下载");
  link.href = `/artifacts/${encodeURIComponent(artifact.id)}/download`;
  link.setAttribute("download", artifact.filename);
  actions.append(link);
  item.append(content, actions);
  return item;
}

function renderDetail(task) {
  elements.detail.replaceChildren();
  const content = element("div", "detail-content");
  const heading = element("div", "detail-heading");
  const headingText = element("div");
  headingText.append(
    element("p", "product-mark", taskTypeLabels[task.task_type] || task.task_type),
    element("h2", "", `任务 ${task.id.slice(0, 8)}`),
  );
  const badge = element("span", "status-badge", statusLabels[task.status] || task.status);
  badge.dataset.status = task.status;
  heading.append(headingText, badge);

  const meta = element("div", "detail-meta");
  meta.append(
    element("span", "", `创建：${formatTime(task.created_at)}`),
    element("span", "", `开始：${formatTime(task.started_at)}`),
    element("span", "", `结束：${formatTime(task.finished_at)}`),
    element("span", "", `步骤：${task.steps.length}`),
    element("span", "", `尝试次数：${task.attempt ?? 1}`),
  );
  if (task.retry_of) meta.append(element("span", "", `重试自：${shortId(task.retry_of)}`));

  const detailActions = element("div", "detail-actions");
  if (task.status === "PENDING" || task.status === "RUNNING") {
    detailActions.append(actionButton("取消任务", "danger", () => requestCancel(task)));
  } else if (retryableStatuses.has(task.status)) {
    detailActions.append(actionButton("重试任务", "secondary", () => requestRetry(task)));
  }

  content.append(heading, meta, detailActions);

  if (task.status === "RUNNING") {
    const startedAt = task.started_at ? new Date(task.started_at).getTime() : Date.now();
    const seconds = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
    const text = seconds >= 60 ? `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` : `${seconds} 秒`;
    content.append(element("p", "running-note", `已运行 ${text}；步骤长时间无响应会自动超时结束。`));
  }

  const resultSummary = renderResultSummary(task);
  if (resultSummary) content.append(resultSummary);

  if (task.error_message) {
    const error = element("section", "detail-section");
    error.append(element("h3", "", "错误信息"), element("p", "error-text", task.error_message));
    if (task.error_code) error.append(element("small", "error-code", `错误码：${task.error_code}`));
    const failedStep = task.steps?.find((step) => step.error_message && step.output);
    if (failedStep) error.append(technicalDetails(failedStep.output));
    content.append(error);
  }

  const stepsSection = element("section", "detail-section");
  stepsSection.append(element("h3", "", "执行步骤"));
  const steps = element("ol", "step-list");
  task.steps.forEach((step) => steps.append(renderStep(step)));
  stepsSection.append(steps);
  content.append(stepsSection);

  const artifactsSection = element("section", "detail-section");
  artifactsSection.append(element("h3", "", "任务产物"));
  const artifacts = element("ul", "artifact-list");
  task.artifacts.forEach((artifact) => artifacts.append(renderArtifact(artifact)));
  if (!task.artifacts.length) artifacts.append(element("li", "step-item", "暂无产物"));
  artifactsSection.append(artifacts);
  content.append(artifactsSection);
  elements.detail.append(content);
}

async function selectTask(taskId) {
  selectedTaskId = taskId;
  document.querySelectorAll(".task-row").forEach((row) => {
    row.setAttribute("aria-current", String(row.dataset.taskId === selectedTaskId));
  });
  try {
    renderDetail(await api(`/tasks/${encodeURIComponent(taskId)}`));
  } catch (error) {
    elements.detail.replaceChildren(element("div", "detail-placeholder", error.message));
  }
}

async function loadTasks() {
  const payload = await api(buildListUrl());
  state.items = payload.items;
  state.total = payload.total;
  state.pages = payload.pages;
  state.page = payload.page;
  state.pageSize = payload.page_size;

  // 删除最后一页的最后一项后，回退到新的末页而不是显示空白。
  if (state.items.length === 0 && state.total > 0 && state.page > state.pages) {
    state.page = state.pages;
    await loadTasks();
    return;
  }

  renderCounts(payload.counts);
  renderTaskList();
  renderPagination();
  elements.lastUpdated.textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
}

async function refreshDetailIfNeeded() {
  if (!selectedTaskId) return;
  if (!state.items.some((task) => task.id === selectedTaskId)) return;
  await selectTask(selectedTaskId);
}

function scheduleRefresh() {
  window.clearTimeout(refreshTimer);
  refreshTimer = window.setTimeout(() => refresh(), 3000);
}

async function refresh() {
  // 定时轮询使用 setTimeout 串行调度，避免请求重叠导致旧数据覆盖新数据。
  window.clearTimeout(refreshTimer);
  elements.refresh.disabled = true;
  try {
    await loadTasks();
    await refreshDetailIfNeeded();
  } catch (error) {
    elements.list.replaceChildren();
    elements.empty.hidden = false;
    elements.empty.querySelector("strong").textContent = "无法加载任务";
    elements.empty.querySelector("span").textContent = error.message;
  } finally {
    elements.refresh.disabled = false;
    scheduleRefresh();
  }
}

async function reloadFromFirstPage() {
  state.page = 1;
  await refresh();
}

async function requestCancel(task) {
  const running = task.status === "RUNNING";
  const hint = running ? "运行中的任务会在当前步骤结束后停止。" : "任务尚未开始，将立即取消。";
  if (!window.confirm(`确定取消任务 ${shortId(task.id)}？\n${hint}`)) return;
  try {
    await api(`/tasks/${encodeURIComponent(task.id)}/cancel`, { method: "POST" });
    elements.lastUpdated.textContent = `已请求取消 ${shortId(task.id)}`;
  } catch (error) {
    elements.lastUpdated.textContent = error.message;
  }
  await refresh();
}

async function requestRetry(task) {
  if (!window.confirm(`确定重试任务 ${shortId(task.id)}？\n将创建一个新的执行记录，原记录保留。`)) return;
  try {
    const created = await api(`/tasks/${encodeURIComponent(task.id)}/retry`, { method: "POST" });
    elements.lastUpdated.textContent = `已创建重试任务 ${shortId(created.id)}`;
    selectedTaskId = created.id;
  } catch (error) {
    elements.lastUpdated.textContent = error.message;
  }
  await refresh();
}

async function batchDelete() {
  const ids = [...state.selected];
  if (!ids.length) return;
  if (!window.confirm(`确定删除选中的 ${ids.length} 个任务？\n等待中和运行中的任务会被跳过，产物目录一并删除。`)) return;
  try {
    const result = await api("/tasks/batch-delete", {
      method: "POST",
      body: JSON.stringify({ task_ids: ids }),
    });
    elements.lastUpdated.textContent = `已删除 ${result.deleted} 个任务，跳过 ${result.skipped} 个`;
    state.selected.clear();
    if (selectedTaskId && ids.includes(selectedTaskId) && result.deleted > 0) {
      selectedTaskId = null;
      elements.detail.replaceChildren(element("div", "detail-placeholder", "任务已删除，请选择其他任务。"));
    }
  } catch (error) {
    elements.lastUpdated.textContent = error.message;
  }
  await refresh();
}

async function cleanupHistory() {
  if (!window.confirm("确定清理 7 天前的已结束任务？\n该操作会同时删除任务产物，且不可撤销。")) return;
  try {
    const result = await api("/tasks/cleanup", {
      method: "POST",
      body: JSON.stringify({ older_than_days: 7 }),
    });
    elements.lastUpdated.textContent = `已清理 ${result.deleted} 个历史任务`;
    state.selected.clear();
  } catch (error) {
    elements.lastUpdated.textContent = error.message;
  }
  await refresh();
}

async function openPreview(artifact) {
  elements.previewTitle.textContent = artifact.filename;
  elements.previewBody.replaceChildren(element("p", "preview-note", "正在加载预览…"));
  elements.previewMeta.textContent = "";
  elements.previewDownload.href = `/artifacts/${encodeURIComponent(artifact.id)}/download`;
  elements.previewDownload.setAttribute("download", artifact.filename);
  elements.previewDialog.showModal();

  try {
    const preview = await api(`/artifacts/${encodeURIComponent(artifact.id)}/preview`);
    elements.previewBody.replaceChildren();
    if (preview.kind === "text") {
      const pre = element("pre", "preview-text", preview.text || "");
      elements.previewBody.append(pre);
    } else if (preview.kind === "image") {
      const img = element("img", "preview-image");
      img.src = preview.url;
      img.alt = preview.filename;
      elements.previewBody.append(img);
    } else {
      elements.previewBody.append(element("p", "preview-note", "该文件类型不支持在线预览，请下载后查看。"));
    }
    elements.previewMeta.textContent = `${formatSize(preview.size_bytes)}${preview.truncated ? " · 内容已截断" : ""}`;
  } catch (error) {
    elements.previewBody.replaceChildren(element("p", "error-text", error.message));
  }
}

async function refreshHealth() {
  // 健康检查失败不阻断任务列表；页面会以 degraded 状态提示沙箱不可用。
  try {
    const health = await api("/health");
    elements.health.textContent = health.status === "ok" ? "环境正常" : "环境降级";
    elements.health.dataset.state = health.status;
  } catch {
    elements.health.textContent = "服务不可用";
    elements.health.dataset.state = "degraded";
  }
}

elements.refresh.addEventListener("click", () => refresh());
elements.search.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    state.q = elements.search.value.trim();
    reloadFromFirstPage();
  }, 300);
});
elements.filter.addEventListener("change", () => {
  state.status = elements.filter.value;
  reloadFromFirstPage();
});
elements.pageSize.addEventListener("change", () => {
  state.pageSize = Number(elements.pageSize.value) || 20;
  reloadFromFirstPage();
});
elements.pagePrev.addEventListener("click", () => {
  if (state.page <= 1) return;
  state.page -= 1;
  refresh();
});
elements.pageNext.addEventListener("click", () => {
  if (state.page >= state.pages) return;
  state.page += 1;
  refresh();
});
elements.selectPage.addEventListener("change", () => {
  state.items.forEach((task) => {
    if (elements.selectPage.checked) state.selected.add(task.id);
    else state.selected.delete(task.id);
  });
  renderTaskList();
});
elements.batchDelete.addEventListener("click", batchDelete);
elements.cleanup.addEventListener("click", cleanupHistory);
elements.previewClose.addEventListener("click", () => elements.previewDialog.close());
elements.previewDialog.addEventListener("click", (event) => {
  if (event.target === elements.previewDialog) elements.previewDialog.close();
});

refreshHealth();
refresh();
