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

const initialParams = new URLSearchParams(window.location.search);
const FEATURE_TYPES = new Set(["WEB_DIGEST", "CSV_ANALYSIS", "REPO_CHECK", "WEB_ACCEPTANCE"]);
const requestedType = initialParams.get("type") || "";
const pageType = FEATURE_TYPES.has(requestedType) ? requestedType : "";

const FEATURE_META = {
  WEB_DIGEST: {
    title: "网页资料整理",
    note: "抓取正文、整页截图，列表页会额外抽取结构化条目。",
    create: "新建网页资料整理任务",
  },
  CSV_ANALYSIS: {
    title: "CSV 数据分析",
    note: "上传 CSV，生成列概览、缺失值和完整度报告。",
    create: "新建 CSV 数据分析任务",
  },
  REPO_CHECK: {
    title: "代码仓库体检",
    note: "克隆仓库，准备独立环境并运行仓库自带的测试。",
    create: "新建代码仓库体检任务",
  },
  WEB_ACCEPTANCE: {
    title: "网页应用验收",
    note: "核对页面标题与状态码，并留存整页截图。",
    create: "新建网页应用验收任务",
  },
};

const state = {
  q: initialParams.get("q") || "",
  status: "",
  type: pageType,
  page: 1,
  pageSize: 20,
  pages: 1,
  total: 0,
  items: [],
  selected: new Set(),
  detailTab: "overview",
};

let selectedTaskId = initialParams.get("task");
let currentTask = null;
let refreshTimer = null;
let searchTimer = null;
let globalTotalLoaded = false;

const elements = {
  list: document.querySelector("#task-list"),
  empty: document.querySelector("#empty-state"),
  health: document.querySelector("#health"),
  refresh: document.querySelector("#refresh"),
  search: document.querySelector("#task-search"),
  pageSize: document.querySelector("#page-size"),
  detail: document.querySelector("#detail-panel"),
  lastUpdated: document.querySelector("#page-subtitle"),
  pageTitle: document.querySelector("#page-title"),
  pageSubtitle: document.querySelector("#page-subtitle"),
  crumb: document.querySelector("#crumb-current"),
  today: document.querySelector("#today"),
  navTotal: document.querySelector("#nav-total"),
  navAll: document.querySelector("#nav-all"),
  featureLinks: [...document.querySelectorAll(".feature-link")],
  tabs: [...document.querySelectorAll(".tab")],
  selectionBar: document.querySelector("#selection-bar"),
  selectionCount: document.querySelector("#selection-count"),
  selectPage: document.querySelector("#select-page"),
  downloadSelected: document.querySelector("#download-selected"),
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
  createOpen: document.querySelector("#create-open"),
  createDialog: document.querySelector("#create-dialog"),
  createForm: document.querySelector("#create-form"),
  createTitle: document.querySelector("#create-title"),
  createFields: document.querySelector("#create-fields"),
  createError: document.querySelector("#create-error"),
  createClose: document.querySelector("#create-close"),
  createCancel: document.querySelector("#create-cancel"),
  detailClose: document.querySelector("#detail-close"),
  currentUser: document.querySelector("#current-user"),
  currentRole: document.querySelector("#current-role"),
  userAvatar: document.querySelector("#user-avatar"),
  userCard: document.querySelector("#user-card"),
  usersLink: document.querySelector("#users-link"),
  usersRail: document.querySelector("#users-rail"),
  logout: document.querySelector("#logout-rail"),
  counts: {
    all: document.querySelector("#count-all"),
    running: document.querySelector("#count-running"),
    success: document.querySelector("#count-success"),
    failed: document.querySelector("#count-failed"),
    cancelled: document.querySelector("#count-cancelled"),
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

function formatShortTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function formatSize(bytes) {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function shortId(taskId) {
  return `#${taskId.slice(0, 8)}`;
}

// 与后端 naming.py 保持一致：列表和详情都显示“类型 · 目标”，短 ID 退到副标题。
function targetLabel(task) {
  const params = task.params || {};
  if (task.task_type === "WEB_DIGEST" || task.task_type === "WEB_ACCEPTANCE") {
    const raw = String(params.url || "");
    try {
      return new URL(raw.includes("//") ? raw : `https://${raw}`).host || raw;
    } catch {
      return raw || "网页";
    }
  }
  if (task.task_type === "REPO_CHECK") {
    const repo = String(params.repo_url || "").replace(/\/+$/, "");
    return repo.split("/").pop()?.replace(/\.git$/, "") || "代码仓库";
  }
  if (task.task_type === "CSV_ANALYSIS") return params.filename || "数据文件";
  return "任务";
}

function batchName(task) {
  return String(task.params?._batch_name || "").trim();
}

async function api(url, options = {}) {
  // 所有接口错误优先读取后端结构化 detail，保证页面显示可理解的信息。
  const response = await fetch(url, {
    headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}) },
    ...options,
  });
  if (response.status === 401) {
    // 会话过期时直接回登录页，避免页面停在“加载中”。
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.replace(`/ui/login.html?next=${next}`);
    throw new Error("登录已过期");
  }
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
  if (state.type) params.set("task_type", state.type);
  params.set("page", String(state.page));
  params.set("page_size", String(state.pageSize));
  return `/tasks?${params.toString()}`;
}

function renderCounts(counts = {}) {
  // counts 由后端按全量任务统计，不受搜索、状态和类型筛选影响。
  const total = counts.total ?? 0;
  elements.counts.all.textContent = String(total);
  elements.counts.running.textContent = String((counts.PENDING ?? 0) + (counts.RUNNING ?? 0));
  elements.counts.success.textContent = String(counts.SUCCESS ?? 0);
  elements.counts.failed.textContent = String((counts.FAILED ?? 0) + (counts.TIMEOUT ?? 0));
  elements.counts.cancelled.textContent = String(counts.CANCELLED ?? 0);
  // 侧栏总数始终是全局任务数；功能页的 counts 只统计当前类型。
  if (!pageType) elements.navTotal.textContent = String(total);
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

const TYPE_SHORT = {
  WEB_DIGEST: "网页整理",
  CSV_ANALYSIS: "CSV 分析",
  REPO_CHECK: "仓库体检",
  WEB_ACCEPTANCE: "网页验收",
  MOCK: "开发验证",
};

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
  const nameLine = element("div", "task-title");
  const typeLabel = taskTypeLabels[task.task_type] || task.task_type;
  // 功能页里每一行都是同一类型，标题只显示目标，减少重复前缀。
  nameLine.append(
    element("strong", "", pageType === task.task_type ? targetLabel(task) : `${typeLabel} · ${targetLabel(task)}`),
  );
  if (batchName(task)) nameLine.append(element("span", "batch-chip", batchName(task)));
  name.append(
    nameLine,
    element("span", "task-meta", `${shortId(task.id)}${attemptText}`),
  );

  const badge = element("span", "status-badge", statusLabels[task.status] || task.status);
  badge.dataset.status = task.status;

  main.append(name);
  main.addEventListener("click", () => selectTask(task.id));

  const typeCell = element("span", "row-type", TYPE_SHORT[task.task_type] || task.task_type);
  const stepsCell = element("span", "row-steps", `${task.steps.length}`);
  const timeCell = element("span", "row-time", formatShortTime(task.created_at));

  const actions = element("div", "row-actions");
  if (task.status === "PENDING" || task.status === "RUNNING") {
    actions.append(actionButton("取消", "danger", () => requestCancel(task)));
  } else if (retryableStatuses.has(task.status)) {
    actions.append(actionButton("重试", "secondary", () => requestRetry(task)));
  }

  const open = element("span", "row-open");
  open.setAttribute("aria-hidden", "true");
  open.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>';

  row.append(check, main, typeCell, stepsCell, timeCell, badge, actions.childElementCount ? actions : open);
  // 整行可点；勾选框和行内按钮保留自己的行为。
  row.addEventListener("click", (event) => {
    if (event.target.closest("input, button, a")) return;
    selectTask(task.id);
  });
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

  elements.selectionCount.textContent = `已选 ${state.selected.size} 项`;
  elements.selectPage.checked = pageIds.length > 0 && selectedOnPage === pageIds.length;
  elements.selectPage.indeterminate = selectedOnPage > 0 && selectedOnPage < pageIds.length;
  elements.selectPage.disabled = pageIds.length === 0;
  // 批量按钮随选中数量启用，避免空选时点击后只得到一条报错。
  elements.batchDelete.disabled = state.selected.size === 0;
  elements.downloadSelected.disabled = state.selected.size === 0;
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
    const extractionLabels = {
      structured: "结构化条目抽取",
      text: "纯文本回退",
      readability: "Readability 去噪",
    };
    grid.append(
      resultGridItem("页面标题", summary.title || "未获取"),
      resultGridItem("正文提取", extractionLabels[summary.extraction] || "Readability 去噪"),
      resultGridItem("正文词数", `${summary.words ?? 0} 词`),
      resultGridItem("预计阅读", `${summary.reading_time_minutes ?? 1} 分钟`),
      resultGridItem("页面状态", summary.http_status ? `HTTP ${summary.http_status}` : "未获取"),
    );
    if (summary.item_count) {
      grid.append(resultGridItem("识别条目", `${summary.item_count} 条`));
    }
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

function artifactIcon(filename) {
  const ext = String(filename).split(".").pop().toLowerCase();
  if (["png", "jpg", "jpeg", "webp"].includes(ext)) return "IMG";
  if (ext === "json") return "{}";
  if (["md", "txt", "log", "csv"].includes(ext)) return "TXT";
  return "FILE";
}

function renderArtifact(artifact) {
  const card = element("button", "artifact-card");
  card.type = "button";
  card.append(
    element("span", "artifact-kind", artifactIcon(artifact.filename)),
    element("span", "artifact-name", artifact.filename),
    element("span", "artifact-size", formatSize(artifact.size_bytes)),
  );
  card.addEventListener("click", () => openPreview(artifact));
  return card;
}

function renderDetail(task) {
  currentTask = task;
  const tab = state.detailTab || "overview";
  elements.detail.replaceChildren();
  const close = element("button", "icon-button detail-close", "×");
  close.type = "button";
  close.id = "detail-close";
  close.setAttribute("aria-label", "关闭详情");
  elements.detail.append(close);

  const content = element("div", "detail-content");
  const heading = element("div", "detail-heading");
  const headingText = element("div");
  headingText.append(
    element("p", "eyebrow", taskTypeLabels[task.task_type] || task.task_type),
    element("h2", "", `${taskTypeLabels[task.task_type] || task.task_type} · ${targetLabel(task)}`),
    element("p", "detail-id", `任务 ${shortId(task.id)}`),
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
  if (batchName(task)) meta.append(element("span", "", `批次：${batchName(task)}`));

  const detailActions = element("div", "detail-actions");
  if (task.status === "PENDING" || task.status === "RUNNING") {
    detailActions.append(actionButton("取消任务", "danger", () => requestCancel(task)));
  } else if (retryableStatuses.has(task.status)) {
    detailActions.append(actionButton("重试任务", "secondary", () => requestRetry(task)));
  }
  if (task.params?._batch_id) {
    detailActions.append(actionButton("重跑本批", "secondary", () => rerunBatch(task)));
  }

  const tabs = element("div", "detail-tabs");
  const tabDefs = [
    ["overview", "任务结论"],
    ["steps", `执行步骤 ${task.steps.length}`],
    ["artifacts", `任务产物 ${task.artifacts.length}`],
  ];
  tabDefs.forEach(([key, label]) => {
    const button = element("button", "detail-tab", label);
    button.type = "button";
    button.classList.toggle("is-active", key === tab);
    button.addEventListener("click", () => {
      state.detailTab = key;
      renderDetail(currentTask);
    });
    tabs.append(button);
  });

  content.append(heading, meta, detailActions, tabs);

  const pane = element("div", "detail-pane");
  if (tab === "overview") {
    if (task.status === "RUNNING") {
      const startedAt = task.started_at ? new Date(task.started_at).getTime() : Date.now();
      const seconds = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
      const text = seconds >= 60 ? `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` : `${seconds} 秒`;
      pane.append(element("p", "running-note", `已运行 ${text}；步骤长时间无响应会自动超时结束。`));
    }
    const resultSummary = renderResultSummary(task);
    if (resultSummary) pane.append(resultSummary);
    if (task.error_message) {
      const error = element("section", "detail-section");
      error.append(element("h3", "", "错误信息"), element("p", "error-text", task.error_message));
      if (task.error_code) error.append(element("small", "error-code", `错误码：${task.error_code}`));
      const failedStep = task.steps?.find((step) => step.error_message && step.output);
      if (failedStep) error.append(technicalDetails(failedStep.output));
      pane.append(error);
    }
    if (!resultSummary && !task.error_message && task.status !== "RUNNING") {
      pane.append(element("p", "detail-note", "任务没有额外结论，切换到执行步骤查看过程。"));
    }
  } else if (tab === "steps") {
    const steps = element("ol", "step-list");
    task.steps.forEach((step) => steps.append(renderStep(step)));
    pane.append(steps);
  } else {
    const header = element("div", "section-header");
    header.append(element("h3", "", "任务产物"));
    if (task.artifacts.length) {
      const downloadAll = element("a", "button secondary small", "下载全部");
      downloadAll.href = `/tasks/${encodeURIComponent(task.id)}/artifacts/archive`;
      downloadAll.setAttribute("download", "");
      header.append(downloadAll);
    }
    pane.append(header);
    const grid = element("div", "artifact-grid");
    task.artifacts.forEach((artifact) => grid.append(renderArtifact(artifact)));
    if (!task.artifacts.length) grid.append(element("p", "detail-note", "暂无产物"));
    pane.append(grid);
  }

  content.append(pane);
  elements.detail.append(content);
}

async function selectTask(taskId) {
  if (taskId !== selectedTaskId) state.detailTab = "overview";
  selectedTaskId = taskId;
  document.body.classList.add("detail-open");
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
  const filtered = state.q || state.status || state.type;
  elements.lastUpdated.textContent = `${state.total} 个任务${filtered ? " · 已筛选" : ""} · 更新于 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;

  // 功能页只拉一次全局总数，避免每次轮询都多一个请求。
  if (pageType && !globalTotalLoaded) {
    globalTotalLoaded = true;
    api("/tasks?page_size=1")
      .then((payload) => {
        elements.navTotal.textContent = String(payload.total);
      })
      .catch(() => {
        globalTotalLoaded = false;
      });
  }
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

async function rerunBatch(task) {
  const name = batchName(task) || "当前批次";
  if (!window.confirm(`重跑“${name}”？\n会按原地址重新创建一批任务，历史记录保留。`)) return;
  try {
    const params = new URLSearchParams({
      q: String(task.params._batch_id || ""),
      page_size: "100",
    });
    const listed = await api(`/tasks?${params.toString()}`);
    const targets = [
      ...new Set(
        listed.items
          .map((item) => item.params?.repo_url || item.params?.url)
          .filter(Boolean),
      ),
    ];
    if (!targets.length) throw new Error("批次里没有可重跑的地址");
    const created = await api("/tasks/batch", {
      method: "POST",
      body: JSON.stringify({
        task_type: task.task_type,
        targets,
        batch_name: batchName(task),
      }),
    });
    window.location.assign(`task_list.html?q=${encodeURIComponent(created.batch_id)}`);
  } catch (error) {
    elements.lastUpdated.textContent = error.message;
  }
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

function field(labelText, control, hint) {
  const wrapper = element("label", "field");
  wrapper.append(element("span", "field-label", labelText), control);
  if (hint) wrapper.append(element("small", "field-hint", hint));
  return wrapper;
}

function input(placeholder, type = "text") {
  const node = document.createElement("input");
  node.type = type;
  node.placeholder = placeholder;
  node.autocomplete = "off";
  return node;
}

// 创建弹窗按功能渲染不同字段；CSV 走 multipart，其余走 JSON。
function renderCreateFields(type) {
  elements.createFields.replaceChildren();
  const config = FEATURE_META[type];
  elements.createTitle.textContent = config ? config.create : "新建任务";

  if (!pageType) {
    const select = document.createElement("select");
    select.id = "create-type";
    Object.entries(FEATURE_META).forEach(([value, meta]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = meta.title;
      select.append(option);
    });
    select.value = type;
    select.addEventListener("change", () => renderCreateFields(select.value));
    elements.createFields.append(field("任务类型", select));
  }

  if (type === "CSV_ANALYSIS") {
    const file = input("", "file");
    file.id = "create-file";
    file.accept = ".csv,text/csv";
    elements.createFields.append(field("CSV 文件", file, "最大 5 MB，首行作为表头。"));
    return;
  }

  if (type === "REPO_CHECK") {
    const repo = input("https://github.com/owner/repo");
    repo.id = "create-repo-url";
    elements.createFields.append(field("仓库地址", repo, "只允许可信的代码托管域名。"));
  } else {
    const url = input("https://example.com");
    url.id = "create-url";
    elements.createFields.append(field("网页地址", url));
  }

  if (type === "WEB_ACCEPTANCE") {
    const title = input("页面标题应包含的文字");
    title.id = "create-expected-title";
    elements.createFields.append(field("预期标题", title, "为空时只记录页面标题和状态码。"));
    return;
  }

  const batch = document.createElement("textarea");
  batch.id = "create-batch-targets";
  batch.rows = 3;
  batch.placeholder = "https://example.com/a\nhttps://example.com/b";
  elements.createFields.append(field("批量地址", batch, "每行一个，留空则只创建上面的单个任务。"));

  const batchName = input("例如：每周巡检");
  batchName.id = "create-batch-name";
  elements.createFields.append(field("批次名称", batchName, "可选，用于筛选和重跑。"));
}

function openCreate(defaultType) {
  const type = pageType || defaultType || "WEB_DIGEST";
  elements.createError.hidden = true;
  elements.createError.textContent = "";
  renderCreateFields(type);
  elements.createDialog.showModal();
  // 把焦点放到第一个字段，避免关闭按钮一打开就套上焦点框。
  elements.createFields.querySelector("input, select, textarea")?.focus();
}

async function submitCreate(event) {
  event.preventDefault();
  const type = pageType || document.querySelector("#create-type")?.value || "";
  if (!type) return;
  elements.createError.hidden = true;

  const fail = (message) => {
    elements.createError.textContent = message;
    elements.createError.hidden = false;
  };

  try {
    if (type === "CSV_ANALYSIS") {
      const file = document.querySelector("#create-file")?.files?.[0];
      if (!file) return fail("请选择要分析的 CSV 文件。");
      const body = new FormData();
      body.append("file", file);
      const response = await fetch("/tasks/csv", { method: "POST", body });
      if (response.status === 401) return fail("登录已过期，请重新登录。");
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        const detail = payload?.detail;
        return fail(detail?.message || detail || `提交失败 (${response.status})`);
      }
      elements.createDialog.close();
      window.location.assign(`task_list.html?type=CSV_ANALYSIS&task=${payload.id}`);
      return;
    }

    const url = document.querySelector("#create-url")?.value.trim() || "";
    const repo = document.querySelector("#create-repo-url")?.value.trim() || "";
    const expected = document.querySelector("#create-expected-title")?.value.trim() || "";
    const batchText = document.querySelector("#create-batch-targets")?.value || "";
    const batchName = document.querySelector("#create-batch-name")?.value.trim() || "";
    const targets = batchText.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);

    const primary = type === "REPO_CHECK" ? repo : url;
    if (!primary && !targets.length) {
      return fail(type === "REPO_CHECK" ? "请填写仓库地址。" : "请填写网页地址。");
    }

    if (targets.length) {
      const created = await api("/tasks/batch", {
        method: "POST",
        body: JSON.stringify({
          task_type: type,
          targets: primary && !targets.includes(primary) ? [primary, ...targets] : targets,
          batch_name: batchName,
        }),
      });
      elements.createDialog.close();
      window.location.assign(`task_list.html?type=${type}&q=${encodeURIComponent(created.batch_id)}`);
      return;
    }

    const params = type === "REPO_CHECK" ? { repo_url: repo } : { url };
    if (type === "WEB_ACCEPTANCE" && expected) params.expected_title = expected;
    const task = await api("/tasks", {
      method: "POST",
      body: JSON.stringify({ task_type: type, params }),
    });
    elements.createDialog.close();
    window.location.assign(`task_list.html?type=${type}&task=${task.id}`);
  } catch (error) {
    fail(error.message);
  }
}

// 功能页：标题、面包屑和侧栏高亮随 ?type= 切换。
function applyPageMode() {
  if (!pageType) {
    elements.navAll.classList.add("is-active");
    return;
  }
  const meta = FEATURE_META[pageType];
  elements.pageTitle.textContent = meta.title;
  elements.pageTitle.dataset.type = pageType;
  elements.crumb.textContent = meta.title;
  document.title = `${meta.title} · BoxPilot`;
  elements.navAll.classList.remove("is-active");
  elements.featureLinks.forEach((link) => {
    link.classList.toggle("is-active", link.dataset.type === pageType);
  });
}

elements.refresh.addEventListener("click", () => refresh());
// 从批量创建页跳转过来时，搜索框直接带入批次号，用户能看到刚提交的一批任务。
elements.search.value = state.q;
elements.search.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    state.q = elements.search.value.trim();
    reloadFromFirstPage();
  }, 300);
});
elements.tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    state.status = tab.dataset.status || "";
    elements.tabs.forEach((item) => item.classList.toggle("is-active", item === tab));
    reloadFromFirstPage();
  });
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
elements.downloadSelected.addEventListener("click", () => {
  const ids = [...state.selected];
  if (!ids.length) return;
  window.location.href = `/tasks/artifacts/archive?task_ids=${encodeURIComponent(ids.join(","))}`;
});
elements.cleanup.addEventListener("click", cleanupHistory);
elements.createOpen.addEventListener("click", () => openCreate());
elements.createClose.addEventListener("click", () => elements.createDialog.close());
elements.createCancel.addEventListener("click", () => elements.createDialog.close());
elements.createForm.addEventListener("submit", submitCreate);
elements.createDialog.addEventListener("click", (event) => {
  if (event.target === elements.createDialog) elements.createDialog.close();
});
elements.previewClose.addEventListener("click", () => elements.previewDialog.close());
document.addEventListener("click", (event) => {
  if (event.target.closest("#detail-close")) document.body.classList.remove("detail-open");
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") document.body.classList.remove("detail-open");
});
elements.previewDialog.addEventListener("click", (event) => {
  if (event.target === elements.previewDialog) elements.previewDialog.close();
});
elements.logout.addEventListener("click", async () => {
  await fetch("/auth/logout", { method: "POST" });
  window.location.replace("/ui/login.html");
});

async function loadCurrentUser() {
  try {
    const user = await api("/auth/me");
    elements.currentUser.textContent = user.username;
    elements.currentRole.textContent = user.is_admin ? "管理员" : "成员";
    elements.userAvatar.textContent = String(user.username || "B").slice(0, 1).toUpperCase();
    elements.userCard.hidden = false;
    elements.logout.hidden = false;
    if (user.is_admin) {
      elements.usersLink.hidden = false;
    }
  } catch {
    // api() 已在 401 时跳转，这里保持安静，避免重复提示。
  }
}

elements.today.textContent = new Date().toLocaleDateString("zh-CN", {
  year: "numeric",
  month: "long",
  day: "numeric",
  weekday: "long",
});

applyPageMode();
loadCurrentUser();
refreshHealth();
refresh();
