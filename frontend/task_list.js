/*
 * 文件职责：任务工作台的前端逻辑，负责服务端搜索/分页、批量清理、取消与重试、
 * 产物预览以及定时轮询。所有业务文本都通过 textContent 写入，避免任务参数被当作 HTML 注入。
 */

const taskTypeLabels = {
  MOCK: "开发验证",
  CSV_ANALYSIS: "CSV 数据分析",
  WEB_DIGEST: "网页资料整理",
  WEB_COMPARE: "网页对比",
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
    note: "抓取正文与截图，并按阅读目标做精读。",
    create: "新建网页资料整理任务",
  },
  WEB_COMPARE: {
    title: "网页对比",
    note: "抓取 2 到 10 个网页，按维度生成对比表并标出冲突。",
    create: "新建网页对比任务",
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
let initialTaskOpened = false;
let currentTask = null;
let refreshTimer = null;
let searchTimer = null;
let globalTotalLoaded = false;
let lastListSignature = "";
let lastDetailSignature = "";

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
const HOSTLIKE = /https?:\/\/|www\.|\b[\w-]+\.(?:com|cn|org|net|io|dev|edu|gov)\b/i;

function cleanTitle(value, limit = 24) {
  return String(value || "")
    .split("|")[0]
    .split("—")[0]
    .trim()
    .slice(0, limit);
}

function targetLabel(task) {
  const params = task.params || {};
  const summary = task.result?.summary || {};
  if (task.task_type === "WEB_DIGEST") {
    const title = cleanTitle(summary.title);
    return title && !HOSTLIKE.test(title) ? title : "网页资料整理";
  }
  if (task.task_type === "WEB_ACCEPTANCE") {
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
  if (task.task_type === "WEB_COMPARE") {
    const label = String(summary.label || "").trim();
    if (label && !HOSTLIKE.test(label)) return label.slice(0, 24);
    const sources = Array.isArray(summary.sources) ? summary.sources : [];
    const titles = sources
      .map((source) => cleanTitle(source?.title))
      .filter((title) => title && !HOSTLIKE.test(title) && !/未找到|找不到|404|not found|page not found|访问失败/i.test(title));
    if (titles.length === 2) return `${titles[0].slice(0, 16)} vs ${titles[1].slice(0, 16)}`;
    if (titles.length > 2) return `${titles[0].slice(0, 12)} / ${titles[1].slice(0, 12)} 等 ${titles.length} 站`;
    if (titles.length === 1) return titles[0].slice(0, 24);
    return "网页对比";
  }
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
  if (state.type === "WEB_DIGEST") params.set("task_type", "WEB_DIGEST,WEB_COMPARE");
  else if (state.type) params.set("task_type", state.type);
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
  WEB_COMPARE: "网页对比",
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
  const datasetCount = (task.result?.summary?.datasets || []).length;
  const target = task.task_type === "CSV_ANALYSIS" && datasetCount > 1 ? `${datasetCount} 个数据集` : targetLabel(task);
  const rowTitle = task.task_type === "CSV_ANALYSIS" && datasetCount > 1
    ? `${typeLabel} · ${target}`
    : target === typeLabel ? typeLabel : pageType === task.task_type ? target : `${typeLabel} · ${target}`;
  nameLine.append(element("strong", "", rowTitle));
  if (batchName(task)) nameLine.append(element("span", "batch-chip", batchName(task)));
  name.append(nameLine);
  if (Array.isArray(task.tags) && task.tags.length) {
    const tagRow = element("div", "task-tags");
    task.tags.slice(0, 4).forEach((tag) => {
      const chip = element("span", "tag-chip", tag);
      chip.dataset.color = String(tagColor(tag));
      tagRow.append(chip);
    });
    if (task.tags.length > 4) tagRow.append(element("span", "tag-chip tag-more", `+${task.tags.length - 4}`));
    name.append(tagRow);
  }
  name.append(element("span", "task-meta", `${shortId(task.id)}${attemptText}`));

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

function tagColor(text) {
  let hash = 0;
  for (const ch of String(text || "")) hash = (hash * 31 + ch.codePointAt(0)) % 8;
  return hash;
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
  if (summary.kind === "failure") outcome = "failed";

  const section = element("section", "detail-section");
  section.append(element("h3", "", "任务结论"));
  const panel = element("div", "result-summary");
  panel.dataset.outcome = outcome;
  panel.append(
    element(
      "div",
      "result-summary-title",
      summary.kind === "failure" ? summary.reason || "任务失败" : summary.message || "任务已完成",
    ),
  );
  const grid = element("dl", "result-grid");

  if (summary.kind === "failure") {
    grid.append(resultGridItem("错误码", summary.code || "UNKNOWN"));
  } else if (summary.kind === "web_acceptance") {
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
    if (summary.goal) {
      grid.append(resultGridItem("阅读目标", summary.goal));
    }
    if (summary.reading_words) {
      grid.append(resultGridItem("精读字数", `${summary.reading_words} 字`));
    }
    if (summary.field_count) {
      grid.append(resultGridItem("自定义字段", `${summary.field_count} 个 / ${summary.field_rows ?? 0} 行`));
    }
  } else if (summary.kind === "web_compare") {
    grid.append(
      resultGridItem("来源数量", `${summary.source_count ?? 0} 个`),
      resultGridItem("对比维度", (summary.dimensions || []).join("、") || "默认维度"),
    );
    if (summary.degraded) {
      panel.append(element("p", "detail-note", "模型分析暂不可用，以下为自动生成的来源摘要。"));
    }
    if (summary.conclusion) {
      panel.append(element("p", "result-conclusion", summary.conclusion));
    } else {
      panel.append(element("p", "detail-note", "结论暂缺，展开任务产物查看完整报告。"));
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


function artifactSection(task) {
  const wrap = element("section", "detail-section");
  const header = element("div", "section-header");
  header.append(element("h3", "", "任务产物"));
  if (task.artifacts.length) {
    const downloadAll = element("a", "button secondary small", "下载全部");
    downloadAll.href = `/tasks/${encodeURIComponent(task.id)}/artifacts/archive`;
    downloadAll.setAttribute("download", "");
    header.append(downloadAll);
  }
  wrap.append(header);
  const grid = element("div", "artifact-grid");
  task.artifacts.forEach((artifact) => grid.append(renderArtifact(artifact)));
  if (!task.artifacts.length) grid.append(element("p", "detail-note", "暂无产物"));
  wrap.append(grid);
  return wrap;
}

let chatTaskId = null;

function appendChatInline(container, text) {
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > cursor) container.append(document.createTextNode(text.slice(cursor, match.index)));
    const token = match[0];
    if (token.startsWith("**")) {
      container.append(element("strong", null, token.slice(2, -2)));
    } else {
      container.append(element("code", null, token.slice(1, -1)));
    }
    cursor = match.index + token.length;
  }
  if (cursor < text.length) container.append(document.createTextNode(text.slice(cursor)));
}

function renderChatMessage(message) {
  const row = element("div", `chat-message chat-${message.role}`);
  row.append(element("div", "chat-role", message.role === "user" ? "我" : "助手"));
  const body = element("div", "chat-body chat-bubble");
  let content = message.content || "";
  if (message.role === "assistant" && content.startsWith("这张表共")) {
    const match = content.match(/^这张表共 ([\d,]+) 行 × ([\d,]+) 列，识别为([^，。]+)/);
    content = match
      ? `${match[3]}，${match[1]} 行 × ${match[2]} 列。可以问趋势或列含义。`
      : "可以问趋势、列含义，或让我进一步分析。";
  } else if (message.role === "assistant" && content.startsWith("这次共上传")) {
    const match = content.match(/^这次共上传 (\d+) 个数据集，合计 ([\d,]+) 行/);
    content = match
      ? `${match[1]} 个数据集，共 ${match[2]} 行。可以问趋势或让我生成图表。`
      : "可以问某个数据集的趋势，或让我生成图表。";
  }
  if (message.role === "assistant") {
    appendChatInline(body, content);
  } else {
    body.textContent = content;
  }
  row.append(body);
  return row;
}

function chatSection(task, options = {}) {
  const wrap = element("section", "detail-section task-chat");
  const header = element("div", "section-header");
  header.append(element("h3", "", options.title || "就这份对比继续提问"));
  wrap.append(header);
  const list = element("div", "task-chat-list");
  list.id = "task-chat-list";
  list.append(element("p", "detail-note", "正在加载对话…"));
  const form = element("form", "task-chat-form");
  const input = element("input", "task-chat-input");
  input.id = "task-chat-input";
  input.placeholder = options.placeholder || "例如：这两页在价格上有什么差异？";
  input.autocomplete = "off";
  const send = element("button", "button primary small", "发送");
  send.type = "submit";
  form.append(input, send);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    sendChatMessage(task.id, input.value.trim(), input);
  });
  wrap.append(list, form);
  return wrap;
}

async function loadTaskChat(taskId) {
  chatTaskId = taskId;
  const list = document.getElementById("task-chat-list");
  if (!list) return;
  try {
    const data = await api(`/tasks/${encodeURIComponent(taskId)}/chat`);
    if (chatTaskId !== taskId || !document.getElementById("task-chat-list")) return;
    renderChatList(data.messages || []);
  } catch (error) {
    list.replaceChildren(element("p", "detail-note", `对话加载失败：${error.message}`));
  }
}

function renderChatList(messages) {
  const list = document.getElementById("task-chat-list");
  if (!list) return;
  list.replaceChildren();
  (messages || []).forEach((message) => list.append(renderChatMessage(message)));
  list.scrollTop = (messages || []).length > 1 ? list.scrollHeight : 0;
}

let chatBusy = false;

async function sendChatMessage(taskId, question, input) {
  if (!question || chatBusy) return;
  chatBusy = true;
  input.value = "";
  const list = document.getElementById("task-chat-list");
  if (list) {
    list.append(renderChatMessage({ role: "user", content: question }));
    const pending = element("p", "detail-note", "助手正在整理…");
    pending.id = "task-chat-pending";
    list.append(pending);
    list.scrollTop = list.scrollHeight;
  }
  try {
    const data = await api(`/tasks/${encodeURIComponent(taskId)}/chat`, {
      method: "POST",
      body: JSON.stringify({ question }),
    });
    if (chatTaskId !== taskId) return;
    document.getElementById("task-chat-pending")?.remove();
    if (data.messages) renderChatList(data.messages);
  } catch (error) {
    document.getElementById("task-chat-pending")?.remove();
    const listNode = document.getElementById("task-chat-list");
    if (listNode) listNode.append(element("p", "detail-note", `提问失败：${error.message}`));
  } finally {
    chatBusy = false;
  }
}

function failureCard(task) {
  const panel = element("div", "result-summary");
  panel.dataset.outcome = "failed";
  panel.append(element("h3", null, "任务失败"));
  panel.append(element("p", "detail-note", task.error_message || "任务执行失败，请重试。"));
  const grid = element("div", "result-grid");
  grid.append(resultGridItem("错误码", task.error_code || "UNKNOWN"));
  panel.append(grid);
  return panel;
}

let CSV_COLORS = ["#466F87", "#78B6C8", "#F2D98E", "#E48578", "#98AF1E"];
const SVG_NS = "http://www.w3.org/2000/svg";
let csvViewState = { taskId: "", view: "" };

function svgNode(tag, attrs = {}, text = "") {
  const node = document.createElementNS(SVG_NS, tag);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  if (text) node.textContent = text;
  return node;
}

function csvText(parent, x, y, text, options = {}) {
  const node = svgNode("text", {
    x,
    y,
    "font-size": options.size || 14,
    "font-family": '"Segoe UI", "Microsoft YaHei", sans-serif',
    "font-variant-numeric": "tabular-nums",
    fill: options.fill || "#475569",
    "text-anchor": options.anchor || "start",
    "font-weight": options.weight || 400,
  }, String(text));
  parent.append(node);
  return node;
}

function csvShort(text, limit = 14) {
  const value = String(text ?? "");
  return value.length > limit ? `${value.slice(0, limit - 1)}…` : value;
}

function csvChartFrame() {
  const wrap = element("div", "csv-chart-canvas");
  return wrap;
}

const CSV_CHART_WIDTH = 960;

function csvPalette(summary = {}) {
  const fallback = ["#466F87", "#78B6C8", "#F2D98E", "#E48578", "#98AF1E"];
  const raw = Array.isArray(summary.palette) ? summary.palette : [];
  return fallback.map((color, index) => (
    /^#[0-9a-f]{6}$/i.test(String(raw[index] || "")) ? raw[index] : color
  ));
}

function csvColorAlpha(hex, alpha) {
  const value = String(hex || "").replace("#", "");
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  return `rgba(${Number.isFinite(r) ? r : 70}, ${Number.isFinite(g) ? g : 111}, ${Number.isFinite(b) ? b : 135}, ${alpha})`;
}

function csvSafeChartName(text) {
  return String(text || "chart")
    .replace(/[^\w\u4e00-\u9fa5-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60) || "chart";
}

function csvSvgSize(svg) {
  const box = svg.viewBox && svg.viewBox.baseVal;
  if (box && box.width) return { width: box.width, height: box.height };
  const rect = svg.getBoundingClientRect();
  const width = Number(svg.getAttribute("width")) || rect.width || 960;
  const height = Number(svg.getAttribute("height")) || rect.height || 380;
  return { width, height };
}

function csvSerializeSvg(svg) {
  const clone = svg.cloneNode(true);
  const { width, height } = csvSvgSize(svg);
  if (!clone.getAttribute("viewBox")) clone.setAttribute("viewBox", `0 0 ${width} ${height}`);
  clone.setAttribute("xmlns", SVG_NS);
  clone.setAttribute("width", String(width));
  clone.setAttribute("height", String(height));
  clone.style.width = `${width}px`;
  clone.style.height = `${height}px`;
  return new XMLSerializer().serializeToString(clone);
}

function csvDownloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

function downloadCsvChartSvg(svg, filename) {
  const name = `${csvSafeChartName(filename)}.svg`;
  csvDownloadBlob(new Blob([csvSerializeSvg(svg)], { type: "image/svg+xml;charset=utf-8" }), name);
}

function downloadCsvChartPng(svg, filename) {
  const box = svg.viewBox && svg.viewBox.baseVal;
  const scale = 3;
  const width = (box && box.width ? box.width : 960) * scale;
  const height = (box && box.height ? box.height : 380) * scale;
  const markup = csvSerializeSvg(svg);
  const blob = new Blob([markup], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const image = new Image();
  image.onload = () => {
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = "high";
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, width, height);
    context.drawImage(image, 0, 0, width, height);
    canvas.toBlob((png) => {
      if (png) csvDownloadBlob(png, `${csvSafeChartName(filename)}.png`);
    }, "image/png");
    URL.revokeObjectURL(url);
  };
  image.onerror = () => URL.revokeObjectURL(url);
  image.src = url;
}

let csvChartLightbox = null;

function ensureCsvChartLightbox() {
  if (csvChartLightbox) return csvChartLightbox;
  const dialog = element("dialog", "csv-chart-lightbox");
  const head = element("div", "csv-chart-lightbox-head");
  const copy = element("div");
  const title = element("strong", "");
  copy.append(element("small", "", "图表预览"), title);
  const actions = element("div", "csv-chart-lightbox-actions");
  const zoomOut = element("button", "", "−");
  const zoomLabel = element("span", "csv-chart-lightbox-zoom", "100%");
  const zoomIn = element("button", "", "＋");
  const reset = element("button", "", "复位");
  const svgButton = element("button", "", "下载 SVG");
  const pngButton = element("button", "", "下载 PNG");
  const close = element("button", "", "×");
  [zoomOut, zoomIn, reset, svgButton, pngButton, close].forEach((button) => { button.type = "button"; });
  actions.append(zoomOut, zoomLabel, zoomIn, reset, svgButton, pngButton, close);
  head.append(copy, actions);
  const body = element("div", "csv-chart-lightbox-body");
  const stage = element("div", "csv-chart-lightbox-stage");
  body.append(stage);
  dialog.append(head, body);
  document.body.append(dialog);

  const state = { dialog, title, stage, zoomLabel, svg: null, filename: "chart", zoom: 1 };
  const apply = () => {
    if (!state.svg) return;
    const { width } = csvSvgSize(state.svg);
    state.stage.style.width = `${width * state.zoom}px`;
    state.zoomLabel.textContent = `${Math.round(state.zoom * 100)}%`;
  };
  const setZoom = (value) => {
    state.zoom = Math.min(3, Math.max(0.5, value));
    apply();
  };
  zoomOut.addEventListener("click", () => setZoom(state.zoom - 0.2));
  zoomIn.addEventListener("click", () => setZoom(state.zoom + 0.2));
  reset.addEventListener("click", () => setZoom(1));
  svgButton.addEventListener("click", () => state.svg && downloadCsvChartSvg(state.svg, state.filename));
  pngButton.addEventListener("click", () => state.svg && downloadCsvChartPng(state.svg, state.filename));
  close.addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener("close", () => { state.svg = null; });
  csvChartLightbox = { state, apply, setZoom };
  return csvChartLightbox;
}

function openCsvChartLightbox(svg, titleText, filename) {
  const box = ensureCsvChartLightbox();
  const clone = svg.cloneNode(true);
  clone.style.width = "100%";
  clone.style.height = "auto";
  box.state.stage.replaceChildren(clone);
  box.state.stage.style.width = "";
  box.state.svg = clone;
  box.state.filename = filename || "chart";
  box.state.title.textContent = titleText || "图表预览";
  box.state.zoom = 1.2;
  box.apply();
  box.state.dialog.showModal();
}

function csvEchartsTree(node) {
  const label = node.value ? `${node.name}：${node.value}` : node.name;
  return {
    name: String(label ?? ""),
    children: (node.children || []).map(csvEchartsTree),
  };
}

function csvEchartsOption(view, chart) {
  const axisColor = "#64748b";
  const lineColor = "#e2e8f0";
  const textStyle = {
    fontFamily: '"Segoe UI", "Microsoft YaHei", sans-serif',
    fontSize: 14,
    color: "#334155",
  };
  const tooltip = {
    backgroundColor: "rgba(15, 23, 42, 0.92)",
    borderWidth: 0,
    textStyle: { color: "#f8fafc", fontSize: 14 },
    extraCssText: "border-radius:8px;padding:8px 10px;",
  };
  const legend = {
    top: 6,
    type: "scroll",
    itemWidth: 14,
    itemHeight: 10,
    textStyle: { fontSize: 14, color: "#1e293b" },
    pageTextStyle: { fontSize: 12 },
  };
  if (view === "line") {
    const series = (chart.series || []).filter((item) => item.points?.length);
    const labels = series[0]?.points.map((point) => String(point[0])) || [];
    return {
      animation: false,
      color: CSV_COLORS,
      textStyle,
      tooltip: { ...tooltip, trigger: "axis" },
      legend,
      grid: { left: 72, right: 34, top: 58, bottom: 52 },
      xAxis: {
        type: "category",
        data: labels,
        boundaryGap: false,
        axisLine: { lineStyle: { color: lineColor } },
        axisTick: { show: false },
        axisLabel: { fontSize: 14, color: axisColor },
      },
      yAxis: {
        type: "value",
        scale: true,
        min: (value) => Math.floor(value.min * 0.9),
        max: (value) => Math.ceil(value.max * 1.05),
        axisLabel: { fontSize: 14, color: axisColor },
        splitLine: { lineStyle: { color: lineColor } },
      },
      series: series.map((item) => ({
        name: item.name,
        type: "line",
        data: item.points.map((point) => Number(point[1])),
        symbol: "circle",
        symbolSize: 7,
        lineStyle: { width: 2.6 },
        emphasis: { focus: "series" },
      })),
    };
  }
  if (view === "bar" || view === "profile_bar") {
    const items = (chart.items || []).slice(0, 15);
    return {
      animation: false,
      color: CSV_COLORS,
      textStyle,
      tooltip: { ...tooltip, trigger: "axis", axisPointer: { type: "shadow" } },
      grid: { left: 12, right: 54, top: 18, bottom: 18, containLabel: true },
      xAxis: {
        type: "value",
        axisLabel: { fontSize: 14, color: axisColor },
        splitLine: { lineStyle: { color: lineColor } },
      },
      yAxis: {
        type: "category",
        inverse: true,
        data: items.map((item) => String(item.label)),
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { fontSize: 14, color: "#1e293b" },
      },
      series: [{
        type: "bar",
        data: items.map((item, index) => ({
          value: Number(item.value) || 0,
          itemStyle: { color: CSV_COLORS[index % CSV_COLORS.length], borderRadius: [0, 5, 5, 0] },
        })),
        barMaxWidth: 24,
        label: { show: true, position: "right", fontSize: 14, color: "#334155" },
      }],
    };
  }
  if (view === "pie") {
    const items = (chart.items || []).filter((item) => Number(item.value) > 0).slice(0, 8);
    return {
      animation: false,
      color: CSV_COLORS,
      textStyle,
      tooltip: { ...tooltip, trigger: "item", formatter: "{b}<br/>{c}（{d}%）" },
      legend: { ...legend, orient: "vertical", right: 8, top: "middle" },
      series: [{
        type: "pie",
        radius: ["0%", "62%"],
        center: ["34%", "54%"],
        data: items.map((item) => ({ name: String(item.label), value: Number(item.value) || 0 })),
        label: { fontSize: 14, color: "#334155", formatter: "{b}\n{d}%" },
        labelLine: { length: 10, length2: 8 },
        itemStyle: { borderColor: "#ffffff", borderWidth: 2 },
      }],
    };
  }
  if (view === "heatmap") {
    const values = (chart.cells || []).map((cell) => Number(cell.value)).filter(Number.isFinite);
    const min = values.length ? Math.min(...values) : 0;
    const max = values.length ? Math.max(...values) : 1;
    const xIndex = new Map((chart.x_labels || []).map((label, index) => [label, index]));
    const yIndex = new Map((chart.y_labels || []).map((label, index) => [label, index]));
    return {
      animation: false,
      textStyle,
      tooltip: { ...tooltip, trigger: "item", formatter: (params) => `${params.value[1]} / ${params.value[0]}：${params.value[2]}` },
      grid: { left: 96, right: 36, top: 56, bottom: 74 },
      xAxis: {
        type: "category",
        data: chart.x_labels || [],
        splitArea: { show: true },
        axisLabel: { fontSize: 14, color: axisColor },
        axisLine: { lineStyle: { color: lineColor } },
      },
      yAxis: {
        type: "category",
        data: chart.y_labels || [],
        splitArea: { show: true },
        axisLabel: { fontSize: 14, color: "#1e293b" },
        axisLine: { lineStyle: { color: lineColor } },
      },
      visualMap: {
        min,
        max: min === max ? max + 1 : max,
        calculable: true,
        orient: "horizontal",
        left: "center",
        bottom: 8,
        itemWidth: 12,
        itemHeight: 90,
        textStyle: { fontSize: 14, color: axisColor },
        inRange: { color: ["#f4f9fb", CSV_COLORS[1], CSV_COLORS[0]] },
      },
      series: [{
        type: "heatmap",
        data: (chart.cells || []).map((cell) => [xIndex.get(cell.x), yIndex.get(cell.y), Number(cell.value)]),
        label: { show: true, fontSize: 14, color: "#1e293b" },
        emphasis: { itemStyle: { shadowBlur: 8, shadowColor: "rgba(15,23,42,0.25)" } },
      }],
    };
  }
  if (view === "scatter") {
    return {
      animation: false,
      color: [CSV_COLORS[0]],
      textStyle,
      tooltip: { ...tooltip, trigger: "item", formatter: (params) => `${params.value[0]}，${params.value[1]}` },
      grid: { left: 76, right: 34, top: 46, bottom: 54 },
      xAxis: {
        type: "value",
        name: chart.x_label || "",
        nameTextStyle: { fontSize: 14, color: axisColor },
        axisLabel: { fontSize: 14, color: axisColor },
        splitLine: { lineStyle: { color: lineColor } },
      },
      yAxis: {
        type: "value",
        name: chart.y_label || "",
        nameTextStyle: { fontSize: 14, color: axisColor },
        axisLabel: { fontSize: 14, color: axisColor },
        splitLine: { lineStyle: { color: lineColor } },
      },
      series: [{
        type: "scatter",
        symbolSize: 9,
        data: (chart.points || []).map((point) => [Number(point[0]), Number(point[1])]),
        itemStyle: { opacity: 0.86 },
      }],
    };
  }
  if (view === "mindmap") {
    return {
      animation: false,
      textStyle,
      tooltip: { ...tooltip, trigger: "item" },
      series: [{
        type: "tree",
        data: chart.root ? [csvEchartsTree(chart.root)] : [],
        left: "10%",
        right: "34%",
        top: "4%",
        bottom: "4%",
        orient: "LR",
        symbol: "circle",
        symbolSize: 8,
        initialTreeDepth: -1,
        expandAndCollapse: false,
        lineStyle: { color: CSV_COLORS[1], width: 1.6 },
        label: {
          position: "left",
          align: "right",
          verticalAlign: "middle",
          distance: 8,
          fontSize: 14,
          color: "#1e293b",
        },
        leaves: {
          label: { position: "right", align: "left", fontSize: 14, color: "#475569" },
        },
      }],
    };
  }
  return null;
}

function renderCsvChart(view, summary, options = {}) {
  CSV_COLORS = csvPalette(summary);
  const chart = summary?.charts?.[view];
  const width = Math.max(720, Math.round(options.width || CSV_CHART_WIDTH));
  let height = 380;
  if (view === "bar" || view === "profile_bar") {
    height = Math.max(190, Math.min(430, 76 + (chart?.items || []).length * 42));
  } else if (view === "mindmap") {
    height = Math.max(420, 90 + (chart?.root?.children || []).length * 84);
  }
  if (!chart) {
    const empty = csvChartFrame();
    empty.append(element("p", "csv-chart-empty", "当前视图没有可用的图表数据。"));
    return empty;
  }
  if (!window.echarts) {
    return renderCsvChartFallback(view, summary, options);
  }
  const frame = csvChartFrame();
  const root = element("div", "csv-echart");
  root.style.position = "relative";
  root.style.width = "100%";
  root.style.height = `${height}px`;
  frame.append(root);
  let instance = null;
  try {
    instance = window.echarts.init(root, null, { renderer: "svg", width, height });
    instance.setOption(csvEchartsOption(view, chart), true);
    root.__echarts = instance;
    frame.__echarts = instance;
  } catch (error) {
    instance?.dispose();
    return renderCsvChartFallback(view, summary, options);
  }
  const svg = root.querySelector("svg");
  if (svg) {
    svg.style.width = "100%";
    svg.style.height = "auto";
    svg.style.fill = "none";
    svg.style.stroke = "none";
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", options.title || view);
    svg.addEventListener("click", () => openCsvChartLightbox(svg, options.title || view, options.filename || view));
  }
  return frame;
}

function renderCsvChartFallback(view, summary, options = {}) {
  CSV_COLORS = csvPalette(summary);
  const chart = summary?.charts?.[view];
  const frame = csvChartFrame();
  if (!chart) {
    frame.append(element("p", "csv-chart-empty", "当前视图没有可用的图表数据。"));
    return frame;
  }
  const items = chart.items || [];
  let height = 380;
  if (view === "bar" || view === "profile_bar") {
    height = Math.max(190, Math.min(430, 76 + items.length * 42));
  } else if (view === "mindmap") {
    const childCount = (chart.root?.children || []).length;
    height = Math.max(420, 90 + childCount * 84);
  }
  const width = Math.max(720, Math.round(options.width || CSV_CHART_WIDTH));
  const svg = svgNode("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });
  svg.setAttribute("aria-label", options.title || view);
  svg.append(svgNode("rect", { x: 0, y: 0, width, height, fill: "#ffffff" }));
  if (view === "line") drawCsvLine(svg, chart, width, height);
  else if (view === "bar" || view === "profile_bar") drawCsvBars(svg, chart, height, width);
  else if (view === "pie") drawCsvPie(svg, chart, width, height);
  else if (view === "heatmap") drawCsvHeatmap(svg, chart, width, height);
  else if (view === "scatter") drawCsvScatter(svg, chart, width, height);
  else if (view === "mindmap") drawCsvMindmap(svg, chart, width, height);
  svg.addEventListener("click", () => {
    openCsvChartLightbox(svg, options.title || view, options.filename || view);
  });
  frame.append(svg);
  return frame;
}

function drawCsvLine(svg, chart, width = CSV_CHART_WIDTH, height = 380) {
  const series = (chart.series || []).filter((item) => item.points?.length);
  if (!series.length) return;
  const labels = series[0].points.map((point) => String(point[0]));
  const values = series.flatMap((item) => item.points.map((point) => Number(point[1])));
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (!Number.isFinite(min) || !Number.isFinite(max)) return;
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const left = 78, right = width - 30, top = 72, bottom = height - 58;
  const stepX = labels.length > 1 ? (right - left) / (labels.length - 1) : 0;
  for (let tick = 0; tick <= 4; tick += 1) {
    const y = bottom - ((bottom - top) * tick) / 4;
    svg.append(svgNode("line", { x1: left, y1: y, x2: right, y2: y, stroke: "#e2e8f0" }));
    csvText(svg, left - 12, y + 5, (min + ((max - min) * tick) / 4).toFixed(1), { anchor: "end", size: 15.5, fill: "#334155" });
  }
  const labelStep = Math.max(1, Math.ceil(labels.length / 8));
  labels.forEach((label, index) => {
    if (index % labelStep !== 0 && index !== labels.length - 1) return;
    csvText(svg, left + stepX * index, bottom + 24, csvShort(label, 10), { anchor: "middle", size: 15, fill: "#334155" });
  });
  series.forEach((item, seriesIndex) => {
    const color = CSV_COLORS[seriesIndex % CSV_COLORS.length];
    const points = item.points.map((point, index) => {
      const x = left + stepX * index;
      const y = bottom - ((Number(point[1]) - min) / (max - min)) * (bottom - top);
      return `${x},${y}`;
    });
    svg.append(svgNode("polyline", {
      points: points.join(" "), fill: "none", stroke: color, "stroke-width": 2.6, "stroke-linejoin": "round",
    }));
    if (item.points.length <= 30) {
      item.points.forEach((point, index) => {
        svg.append(svgNode("circle", {
          cx: left + stepX * index,
          cy: bottom - ((Number(point[1]) - min) / (max - min)) * (bottom - top),
          r: 4,
          fill: color,
        }));
      });
    }
    const legendX = left + (seriesIndex % 3) * 210;
    const legendY = 16 + Math.floor(seriesIndex / 3) * 26;
    svg.append(svgNode("rect", { x: legendX, y: legendY - 11, width: 13, height: 13, rx: 3, fill: color }));
    csvText(svg, legendX + 19, legendY, csvShort(item.name, 16), { fill: "#1e293b", size: 15, weight: 600 });
  });
}

function drawCsvBars(svg, chart, height = 380, width = CSV_CHART_WIDTH) {
  const items = (chart.items || []).slice(0, 12);
  if (!items.length) return;
  const max = Math.max(...items.map((item) => Number(item.value) || 0), 1);
  const left = 190, right = width - 36, top = 26, bottom = height - 44;
  const rowHeight = (bottom - top) / items.length;
  items.forEach((item, index) => {
    const y = top + index * rowHeight;
    const barWidth = Math.max(2, ((Number(item.value) || 0) / max) * (right - left));
    csvText(svg, left - 12, y + rowHeight * 0.64, csvShort(item.label, 16), { anchor: "end", size: 15.5, fill: "#1e293b" });
    svg.append(svgNode("rect", {
      x: left, y: y + Math.max(4, rowHeight * 0.18), width: barWidth, height: Math.max(14, rowHeight * 0.62), rx: 5,
      fill: CSV_COLORS[index % CSV_COLORS.length], opacity: 0.92,
    }));
    csvText(svg, left + barWidth + 10, y + rowHeight * 0.64, String(item.value), { size: 14.5, fill: "#334155" });
  });
  csvText(svg, left, height - 12, chart.value_label || "", { size: 14, fill: "#64748b" });
}

function drawCsvPie(svg, chart, width = CSV_CHART_WIDTH, height = 380) {
  const items = (chart.items || []).filter((item) => Number(item.value) > 0).slice(0, 8);
  const total = items.reduce((sum, item) => sum + Number(item.value), 0);
  if (!items.length || total <= 0) return;
  const cx = 270, cy = height / 2, radius = Math.min(132, height / 2 - 34);
  let angle = -Math.PI / 2;
  items.forEach((item, index) => {
    const sweep = (Number(item.value) / total) * Math.PI * 2;
    const end = angle + sweep;
    const x0 = cx + radius * Math.cos(angle);
    const y0 = cy + radius * Math.sin(angle);
    const x1 = cx + radius * Math.cos(end);
    const y1 = cy + radius * Math.sin(end);
    const large = sweep > Math.PI ? 1 : 0;
    svg.append(svgNode("path", {
      d: `M ${cx} ${cy} L ${x0} ${y0} A ${radius} ${radius} 0 ${large} 1 ${x1} ${y1} Z`,
      fill: CSV_COLORS[index % CSV_COLORS.length],
      stroke: "#ffffff",
      "stroke-width": 2,
    }));
    angle = end;
  });
  items.forEach((item, index) => {
    const y = 92 + index * 30;
    svg.append(svgNode("rect", { x: width * 0.56, y: y - 11, width: 13, height: 13, rx: 3, fill: CSV_COLORS[index % CSV_COLORS.length] }));
    csvText(svg, width * 0.56 + 21, y, `${csvShort(item.label, 20)}  ${item.value}`, { size: 15, fill: "#1e293b" });
  });
}

function drawCsvHeatmap(svg, chart, width = CSV_CHART_WIDTH, height = 380) {
  const xLabels = chart.x_labels || [];
  const yLabels = chart.y_labels || [];
  const cells = chart.cells || [];
  if (!xLabels.length || !yLabels.length || !cells.length) return;
  const values = cells.map((cell) => Number(cell.value)).filter((value) => Number.isFinite(value));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const left = 172, top = 76, right = width - 40, bottom = height - 62;
  const cellW = Math.min(76, (right - left) / xLabels.length);
  const cellH = Math.min(48, (bottom - top) / yLabels.length);
  xLabels.forEach((label, index) => {
    csvText(svg, left + index * cellW + cellW / 2, top - 14, csvShort(label, 10), { anchor: "middle", size: 14, fill: "#334155" });
  });
  yLabels.forEach((label, index) => {
    csvText(svg, left - 10, top + index * cellH + cellH * 0.65, csvShort(label, 16), { anchor: "end", size: 15, fill: "#1e293b" });
  });
  cells.forEach((cell) => {
    const xIndex = xLabels.indexOf(cell.x);
    const yIndex = yLabels.indexOf(cell.y);
    if (xIndex < 0 || yIndex < 0) return;
    const ratio = max === min ? 0.5 : (Number(cell.value) - min) / (max - min);
    const color = csvColorAlpha(CSV_COLORS[0], 0.12 + ratio * 0.82);
    svg.append(svgNode("rect", {
      x: left + xIndex * cellW + 1, y: top + yIndex * cellH + 1,
      width: cellW - 2, height: cellH - 2, rx: 4, fill: color,
    }));
    csvText(svg, left + xIndex * cellW + cellW / 2, top + yIndex * cellH + cellH * 0.62, String(cell.value), {
      anchor: "middle", size: 13.5, fill: ratio > 0.58 ? "#ffffff" : "#1e293b",
    });
  });
  csvText(svg, left, height - 20, `${chart.x_label} / ${chart.y_label}`, { size: 14, fill: "#64748b" });
}

function drawCsvScatter(svg, chart, width = CSV_CHART_WIDTH, height = 380) {
  const points = chart.points || [];
  if (!points.length) return;
  const xs = points.map((point) => Number(point[0]));
  const ys = points.map((point) => Number(point[1]));
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const left = 98, right = width - 40, top = 66, bottom = height - 76;
  const sx = (value) => left + ((value - minX) / (maxX - minX || 1)) * (right - left);
  const sy = (value) => bottom - ((value - minY) / (maxY - minY || 1)) * (bottom - top);
  for (let tick = 0; tick <= 4; tick += 1) {
    const y = bottom - ((bottom - top) * tick) / 4;
    svg.append(svgNode("line", { x1: left, y1: y, x2: right, y2: y, stroke: "#e2e8f0" }));
    csvText(svg, left - 12, y + 5, (minY + ((maxY - minY) * tick) / 4).toFixed(1), { anchor: "end", size: 14, fill: "#475569" });
    const x = left + ((right - left) * tick) / 4;
    svg.append(svgNode("line", { x1: x, y1: top, x2: x, y2: bottom, stroke: "#f1f5f9" }));
    csvText(svg, x, bottom + 24, (minX + ((maxX - minX) * tick) / 4).toFixed(1), { anchor: "middle", size: 14, fill: "#475569" });
  }
  svg.append(svgNode("line", { x1: left, y1: bottom, x2: right, y2: bottom, stroke: "#cbd5e1" }));
  svg.append(svgNode("line", { x1: left, y1: top, x2: left, y2: bottom, stroke: "#cbd5e1" }));
  points.forEach((point) => {
    svg.append(svgNode("circle", {
      cx: sx(Number(point[0])), cy: sy(Number(point[1])), r: 6, fill: CSV_COLORS[0], opacity: 0.75,
    }));
  });
  csvText(svg, (left + right) / 2, height - 22, `X · ${chart.x_label}`, { anchor: "middle", size: 14, fill: "#475569" });
  csvText(svg, left, top - 14, `Y · ${chart.y_label}`, { size: 14, fill: "#475569" });
}

function drawCsvMindmap(svg, chart, width = CSV_CHART_WIDTH, height = 420) {
  const root = chart.root;
  if (!root) return;
  const children = (root.children || []).slice(0, 8);
  const rootY = height / 2;
  const rootX = 28;
  const rootW = 190;
  const rootH = 56;
  svg.append(svgNode("rect", { x: rootX, y: rootY - rootH / 2, width: rootW, height: rootH, rx: 12, fill: CSV_COLORS[0] }));
  csvText(svg, rootX + rootW / 2, rootY + 6, csvShort(root.name, 14), { anchor: "middle", size: 16, fill: "#ffffff", weight: 700 });
  const stepY = children.length > 1 ? (height - 100) / (children.length - 1) : 0;
  children.forEach((child, index) => {
    const y = children.length > 1 ? 50 + stepY * index : rootY;
    svg.append(svgNode("path", {
      d: `M ${rootX + rootW} ${rootY} C ${rootX + rootW + 40} ${rootY}, 250 ${y}, 310 ${y}`,
      fill: "none", stroke: CSV_COLORS[1], "stroke-width": 1.6, opacity: 0.7,
    }));
    svg.append(svgNode("rect", { x: 310, y: y - 24, width: 260, height: 48, rx: 10, fill: csvColorAlpha(CSV_COLORS[1], 0.16), stroke: CSV_COLORS[1], "stroke-width": 1.2 }));
    csvText(svg, 326, y + 5, csvShort(child.name, 18), { size: 14, fill: "#1e3a8a", weight: 600 });
    (child.children || []).slice(0, 3).forEach((grand, grandIndex) => {
      csvText(svg, 610, y - 14 + grandIndex * 21, csvShort(grand.value || grand.name, 24), {
        size: 14, fill: "#475569",
      });
    });
  });
}

function renderCsvSummary(task) {
  const summary = task.result?.summary || {};
  const kinds = {
    numeric_timeseries: "数值时序表",
    cross_matrix: "二维交叉表",
    numeric_snapshot: "数值快照表",
    compare_checklist: "分类对比清单",
    text_archive: "纯文本归档",
    mixed: "混合表",
    multi_dataset: "多数据集",
  };
  const recommended = (summary.view_options || []).find((item) => item.id === summary.recommended_view);
  const section = element("section", "csv-summary");
  const head = element("div", "csv-summary-head");
  head.append(
    element("span", "csv-kind", kinds[summary.table_kind] || "混合表"),
    element("strong", "", summary.message || "CSV 分析完成"),
  );
  const datasets = Array.isArray(summary.datasets) && summary.datasets.length ? summary.datasets : [summary];
  const multi = datasets.length > 1;
  const stats = element("div", "csv-summary-stats");
  [
    ["数据规模", multi ? `${datasets.length} 文件 · ${summary.rows ?? 0} 行` : `${summary.rows ?? 0} 行 × ${summary.columns ?? 0} 列`],
    ["完整度", `${Math.round(((summary.completeness ?? 1) <= 1 ? (summary.completeness ?? 1) * 100 : summary.completeness))}%`],
    ["重复行", `${summary.duplicate_rows ?? 0}`],
    ["推荐视图", multi ? "按数据集切换" : recommended?.label || "列总览"],
  ].forEach(([label, value]) => {
    const item = element("div");
    item.append(element("small", "", label), element("strong", "", value));
    stats.append(item);
  });
  section.append(head, stats);
  if (!multi && recommended?.reason) {
    section.append(element("p", "csv-reason", `为什么推荐：${recommended.reason}`));
  }
  if (summary.insight) {
    section.append(element("p", "csv-insight", summary.insight));
  }
  return section;
}

function renderCsvWorkspace(task) {
  const summary = task.result?.summary || {};
  const datasets = Array.isArray(summary.datasets) && summary.datasets.length ? summary.datasets : [summary];
  const main = element("div", "csv-main");
  main.append(renderCsvSummary(task));

  const chartCard = element("section", "csv-chart-card");
  const chartHead = element("div", "csv-chart-head");
  chartHead.append(element("h3", "", "图表"));
  const chartActions = element("div", "csv-chart-actions");
  const svgButton = element("button", "csv-chart-download", "下载 SVG");
  svgButton.type = "button";
  svgButton.addEventListener("click", () => {
    const svg = chartBody.querySelector("svg");
    if (!svg) return;
    const dataset = datasets[datasetIndex] || summary;
    const viewLabel = tabs.querySelector("button.is-active")?.textContent || activeView;
    downloadCsvChartSvg(svg, `${task.target_label || "CSV"}-${dataset.name || "data"}-${viewLabel}`);
  });
  const downloadButton = element("button", "csv-chart-download", "下载 PNG");
  downloadButton.type = "button";
  downloadButton.addEventListener("click", () => {
    const svg = chartBody.querySelector("svg");
    if (!svg) return;
    const dataset = datasets[datasetIndex] || summary;
    const viewLabel = tabs.querySelector("button.is-active")?.textContent || activeView;
    downloadCsvChartPng(svg, `${task.target_label || "CSV"}-${dataset.name || "data"}-${viewLabel}`);
  });
  chartActions.append(svgButton, downloadButton);
  chartHead.append(chartActions);
  const datasetTabs = element("div", "csv-dataset-tabs");
  const tabs = element("div", "csv-view-tabs");
  const chartBody = element("div", "csv-chart-body");
  let datasetIndex = 0;
  if (csvViewState.taskId === task.id && Number.isInteger(csvViewState.dataset)) {
    datasetIndex = Math.min(Math.max(csvViewState.dataset, 0), datasets.length - 1);
  }
  let activeView = "";

  const render = () => {
    const dataset = datasets[datasetIndex] || summary;
    const rawOptions = dataset.view_options && dataset.view_options.length
      ? dataset.view_options
      : [{ id: "profile_bar", label: "列总览", enabled: true, reason: "" }];
    const options = rawOptions.filter((item) => item.id !== "wordcloud");
    const enabled = options.filter((item) => item.enabled);
    const fallback = dataset.recommended_view || "profile_bar";
    if (!activeView || !enabled.some((item) => item.id === activeView)) {
      activeView = enabled.some((item) => item.id === fallback)
        ? fallback
        : (enabled[0]?.id || "profile_bar");
    }
    if (tabs.childElementCount === 0 || tabs.dataset.dataset !== String(datasetIndex)) {
      tabs.replaceChildren();
      tabs.dataset.dataset = String(datasetIndex);
      options.forEach((item) => {
        const button = element("button", "csv-view-tab", item.label);
        button.type = "button";
        button.dataset.view = item.id;
        button.disabled = !item.enabled;
        if (!item.enabled) button.title = item.reason || "当前表格不适合该视图";
        button.addEventListener("click", () => {
          activeView = item.id;
          render();
        });
        tabs.append(button);
      });
    }
    tabs.querySelectorAll("button").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.view === activeView);
    });
    csvViewState = { taskId: task.id, dataset: datasetIndex, view: activeView };
    const viewLabel = options.find((item) => item.id === activeView)?.label || activeView;
    chartBody.querySelector(".csv-echart")?.__echarts?.dispose();
    chartBody.replaceChildren(renderCsvChart(activeView, {
      charts: dataset.charts || {},
      palette: summary.palette || [],
    }, {
      title: `${dataset.name || "数据集"} · ${viewLabel}`,
      filename: `${task.target_label || "CSV"}-${dataset.name || "data"}-${viewLabel}`,
      width: Math.max(720, chartBody.clientWidth || CSV_CHART_WIDTH),
    }));
  };

  if (datasets.length > 1) {
    datasets.forEach((dataset, index) => {
      const button = element("button", "csv-dataset-tab", dataset.name || `数据集 ${index + 1}`);
      button.type = "button";
      button.classList.toggle("is-active", index === datasetIndex);
      button.addEventListener("click", () => {
        datasetIndex = index;
        activeView = "";
        datasetTabs.querySelectorAll("button").forEach((tab, tabIndex) => tab.classList.toggle("is-active", tabIndex === index));
        render();
      });
      datasetTabs.append(button);
    });
    chartHead.append(datasetTabs);
  }
  chartHead.append(tabs);
  chartCard.append(chartHead, chartBody);
  main.append(chartCard);
  render();
  let lastChartWidth = 0;
  const chartResizeObserver = new ResizeObserver((entries) => {
    const nextWidth = Math.round(entries[0]?.contentRect.width || 0);
    if (nextWidth > 0 && Math.abs(nextWidth - lastChartWidth) > 4) {
      lastChartWidth = nextWidth;
      render();
    }
  });
  chartResizeObserver.observe(chartBody);

  if (task.artifacts.length) {
    const disclosure = document.createElement("details");
    disclosure.className = "artifact-disclosure csv-artifacts";
    const label = document.createElement("summary");
    label.textContent = `任务产物（${task.artifacts.length}）`;
    disclosure.append(label, artifactSection(task));
    main.append(disclosure);
  }
  if (task.steps.length) {
    const disclosure = document.createElement("details");
    disclosure.className = "artifact-disclosure csv-artifacts";
    const label = document.createElement("summary");
    label.textContent = `执行步骤（${task.steps.length}）`;
    disclosure.append(label, stepSection(task));
    main.append(disclosure);
  }

  const side = element("aside", "csv-side");
  side.append(chatSection(task, {title: "就这份数据继续提问", placeholder: "例如：这列数值说明了什么趋势？"}));
  const workspace = element("div", "csv-workspace");
  workspace.append(main, side);
  return workspace;
}

function stepSection(task) {
  const list = element("ol", "step-list");
  task.steps.forEach((step) => list.append(renderStep(step)));
  return list;
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
    element(
      "h2",
      "",
      task.task_type === "WEB_COMPARE"
        ? targetLabel(task)
        : task.task_type === "CSV_ANALYSIS" && (task.result?.summary?.datasets || []).length > 1
          ? `${taskTypeLabels[task.task_type]} · ${task.result.summary.datasets.length} 个数据集`
          : `${taskTypeLabels[task.task_type] || task.task_type} · ${targetLabel(task)}`,
    ),
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

  if (task.task_type === "CSV_ANALYSIS") {
    document.body.classList.remove("compare-open");
    document.body.classList.add("csv-open", "detail-open");
    content.append(heading, meta, detailActions, renderCsvWorkspace(task));
    elements.detail.append(content);
    loadTaskChat(task.id);
    return;
  }

  if (task.task_type === "WEB_COMPARE") {
    content.append(heading, meta, detailActions);
    const pane = element("div", "detail-pane compare-pane");
    const top = element("div", "compare-top");
    if (task.error_code) {
      const failure = renderResultSummary(task) || failureCard(task);
      if (failure) top.append(failure);
    }
    const urls = task.params?.urls || [];
    if (urls.length) {
      const sources = element("section", "detail-section");
      sources.append(element("h3", "", "对比来源"));
      const list = element("ol", "source-list");
      urls.forEach((url) => {
        const item = element("li");
        const link = element("a", "", url);
        link.href = url;
        link.target = "_blank";
        link.rel = "noreferrer";
        item.append(link);
        list.append(item);
      });
      sources.append(list);
      top.append(sources);
    }
    let artifactMenu = null;
    if (task.artifacts.length) {
      const menu = document.createElement("details");
      menu.className = "artifact-menu";
      const summary = document.createElement("summary");
      summary.textContent = `任务产物（${task.artifacts.length}）`;
      const popup = element("div", "artifact-popup");
      popup.append(artifactSection(task));
      menu.append(summary, popup);
      artifactMenu = menu;
    }
    pane.append(top);
    if (artifactMenu) pane.append(artifactMenu);
    pane.append(chatSection(task));
    content.append(pane);
    elements.detail.append(content);
    document.body.classList.remove("csv-open");
    document.body.classList.add("compare-open");
    loadTaskChat(task.id);
    return;
  }
  document.body.classList.remove("compare-open", "csv-open");
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
      const isFailureSummary = task.result?.summary?.kind === "failure";
      const error = element("section", "detail-section");
      if (!isFailureSummary) {
        error.append(element("h3", "", "错误信息"), element("p", "error-text", task.error_message));
        if (task.error_code) error.append(element("small", "error-code", `错误码：${task.error_code}`));
      }
      const failedStep = task.steps?.find((step) => step.error_message && step.output);
      if (failedStep) error.append(technicalDetails(failedStep.output));
      if (error.childElementCount) pane.append(error);
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
    const task = await api(`/tasks/${encodeURIComponent(taskId)}`);
    lastDetailSignature = JSON.stringify(task);
    renderDetail(task);
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
  // 轮询时只有内容变化才重建列表，避免每 3 秒整块闪一下。
  const signature = JSON.stringify(payload.items) + `|${state.total}|${state.page}`;
  if (signature !== lastListSignature) {
    lastListSignature = signature;
    renderTaskList();
    renderPagination();
  }
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
  const item = state.items.find((task) => task.id === selectedTaskId);
  if (!item) return;
  const signature = JSON.stringify(item);
  if (signature === lastDetailSignature) return;
  lastDetailSignature = signature;
  await selectTask(selectedTaskId);
}

function scheduleRefresh() {
  window.clearTimeout(refreshTimer);
  if (document.hidden) return;
  refreshTimer = window.setTimeout(() => refresh(), 3000);
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh();
});

async function refresh() {
  // 定时轮询使用 setTimeout 串行调度，避免请求重叠导致旧数据覆盖新数据。
  window.clearTimeout(refreshTimer);
  elements.refresh.disabled = true;
  try {
    await loadTasks();
    if (selectedTaskId && !initialTaskOpened && !state.items.some((task) => task.id === selectedTaskId)) {
      initialTaskOpened = true;
      await selectTask(selectedTaskId);
    }
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
let digestMode = "single";
let createType = "WEB_DIGEST";

function appendCompareFields(target = elements.createFields, group = null) {
  const dims = document.createElement("div");
  dims.className = "dimension-grid";
  ["适用场景", "优缺点", "性能", "限制", "版本"].forEach((name) => {
    const label = document.createElement("label");
    label.className = "check-item";
    const box = input("", "checkbox");
    box.value = name;
    box.checked = true;
    box.dataset.dimension = "1";
    label.append(box, document.createTextNode(name));
    dims.append(label);
  });
  const custom = input("自定义维度，用逗号分隔");
  custom.id = "create-compare-dimensions";
  if (group) {
    target.append(
      group("02", "对比维度", [field("预设维度", dims)]),
      group("03", "补充维度", [field("自定义", custom, "可选，例如：价格、生态、学习成本。")]),
    );
  } else {
    target.append(field("对比维度", dims), field("补充维度", custom, "可选，例如：价格、生态、学习成本。"));
  }
}

function addFieldRow(list, name = "", description = "") {
  if (!list || list.children.length >= 12) return;
  const row = document.createElement("div");
  row.className = "field-row";
  const nameInput = document.createElement("input");
  nameInput.className = "field-name";
  nameInput.placeholder = "字段名，例如：报名截止";
  nameInput.value = name;
  const descInput = document.createElement("input");
  descInput.className = "field-desc";
  descInput.placeholder = "说明，例如：日期，格式 YYYY-MM-DD";
  descInput.value = description;
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "icon-button small";
  remove.textContent = "×";
  remove.setAttribute("aria-label", "删除字段");
  remove.addEventListener("click", () => row.remove());
  row.append(nameInput, descInput, remove);
  list.append(row);
}

function readFieldRows(list) {
  if (!list) return [];
  const seen = new Set();
  const fields = [];
  list.querySelectorAll(".field-row").forEach((row) => {
    const name = row.querySelector(".field-name")?.value.trim() || "";
    const description = row.querySelector(".field-desc")?.value.trim() || "";
    if (!name || seen.has(name)) return;
    seen.add(name);
    fields.push({ name, description });
  });
  return fields;
}

function saveFieldTemplate(fields) {
  if (!fields.length) return;
  const name = window.prompt("模板名称", "竞赛信息模板");
  if (!name) return;
  const saved = JSON.parse(localStorage.getItem("boxpilot.fieldTemplates") || "{}");
  saved[name] = fields;
  localStorage.setItem("boxpilot.fieldTemplates", JSON.stringify(saved));
}

function applyFieldTemplate(list) {
  const saved = JSON.parse(localStorage.getItem("boxpilot.fieldTemplates") || "{}");
  const names = Object.keys(saved);
  if (!names.length) {
    window.alert("还没有保存过字段模板。");
    return;
  }
  const name = window.prompt(`可用模板：${names.join("、")}`, names[0]);
  if (!name || !saved[name]) return;
  list.replaceChildren();
  saved[name].forEach((item) => addFieldRow(list, item.name, item.description || ""));
}

let csvCreateFiles = [];

function renderCreateFields(type) {
  elements.createFields.replaceChildren();
  elements.createDialog.classList.toggle("is-csv", type === "CSV_ANALYSIS");
  const config = FEATURE_META[type];
  elements.createTitle.textContent = config ? config.create : "新建任务";

  const group = (index, title, nodes, hint) => {
    const section = document.createElement("section");
    section.className = "form-group";
    const head = document.createElement("div");
    head.className = "group-head";
    head.append(
      element("span", "group-index", index),
      element("h3", "", title),
    );
    section.append(head);
    nodes.forEach((node) => section.append(node));
    if (hint) section.append(element("p", "group-hint", hint));
    return section;
  };

  const tagEditor = () => {
    const chips = document.createElement("div");
    chips.className = "tag-chips";
    chips.id = "create-tag-chips";
    const presets = document.createElement("div");
    presets.className = "tag-presets";
    const box = document.createElement("input");
    box.id = "create-tag-input";
    box.placeholder = "输入标签后回车，最多 8 个";
    const add = (raw) => {
      const name = (raw || "").trim();
      if (!name || chips.children.length >= 8) return;
      if ([...chips.children].some((chip) => chip.dataset.name === name)) return;
      const chip = document.createElement("span");
      chip.className = "tag-chip";
      chip.dataset.name = name;
      chip.append(document.createTextNode(name));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `移除标签 ${name}`);
      remove.addEventListener("click", () => {
        chip.remove();
        presets.querySelectorAll(".tag-preset").forEach((item) => {
          if (item.dataset.name === name) item.classList.remove("is-on");
        });
      });
      chip.append(remove);
      chips.append(chip);
    };
    const toggle = (name) => {
      const chip = [...chips.children].find((item) => item.dataset.name === name);
      if (chip) {
        chip.remove();
        presets.querySelector(`.tag-preset[data-name="${name}"]`)?.classList.remove("is-on");
        return;
      }
      add(name);
      presets.querySelector(`.tag-preset[data-name="${name}"]`)?.classList.add("is-on");
    };
    ["生活", "学习", "工作"].forEach((name) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "tag-preset";
      button.dataset.name = name;
      button.textContent = name;
      button.addEventListener("click", () => toggle(name));
      presets.append(button);
    });
    box.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      add(box.value);
      box.value = "";
    });
    const wrap = document.createElement("div");
    wrap.className = "tag-editor";
    wrap.append(presets, chips, box);
    return field("标签", wrap, "生活 / 学习 / 工作 可一键选择，也可回车添加，最多 8 个。");
  };

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
    elements.createFields.append(group("00", "任务类型", [field("类型", select)]));
  }

  if (type === "CSV_ANALYSIS") {
    csvCreateFiles = [];
    const file = input("", "file");
    file.id = "create-file";
    file.accept = ".csv,text/csv";
    file.multiple = true;
    file.hidden = true;

    const list = element("div", "csv-file-list");
    list.id = "create-file-list";
    list.hidden = true;

    const head = element("div", "csv-file-head");
    const count = element("span", "csv-file-count");
    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "csv-file-clear";
    clear.textContent = "清空";
    head.append(count, clear);
    head.hidden = true;

    const icon = element("span", "csv-upload-icon", "CSV");
    const title = element("strong", "csv-upload-title", "拖拽 CSV 到这里");
    const subtitle = element("span", "csv-upload-sub", "或点击选择文件");
    const choose = document.createElement("button");
    choose.type = "button";
    choose.className = "button ghost csv-upload-button";
    choose.textContent = "选择文件";

    const notice = element("p", "csv-upload-notice");
    notice.hidden = true;

    const zone = element("div", "csv-upload-zone");
    zone.append(icon, title, subtitle, choose, file);
    zone.setAttribute("role", "button");
    zone.tabIndex = 0;

    const renderFiles = () => {
      list.replaceChildren();
      const total = csvCreateFiles.reduce((sum, item) => sum + item.size, 0);
      const hasFiles = csvCreateFiles.length > 0;
      list.hidden = !hasFiles;
      head.hidden = !hasFiles;
      count.textContent = `已选 ${csvCreateFiles.length} 个文件 · ${formatSize(total)}`;
      zone.classList.toggle("has-files", hasFiles);
      title.textContent = hasFiles ? "继续添加 CSV" : "拖拽 CSV 到这里";
      subtitle.textContent = hasFiles ? "或点击选择更多文件" : "或点击选择文件";
      csvCreateFiles.forEach((item, index) => {
        const row = element("div", "csv-file-item");
        const meta = element("div", "csv-file-meta");
        meta.append(element("strong", "", item.name), element("span", "", formatSize(item.size)));
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "csv-file-remove";
        remove.textContent = "×";
        remove.setAttribute("aria-label", `移除 ${item.name}`);
        remove.addEventListener("click", () => {
          csvCreateFiles.splice(index, 1);
          renderFiles();
        });
        row.append(meta, remove);
        list.append(row);
      });
    };

    const addFiles = (incoming) => {
      const rejected = [];
      for (const item of incoming) {
        if (csvCreateFiles.length >= 5) {
          rejected.push("最多 5 个文件");
          break;
        }
        if (!item.name.toLowerCase().endsWith(".csv")) {
          rejected.push("仅支持 .csv");
          continue;
        }
        if (item.size > 5 * 1024 * 1024) {
          rejected.push("单个文件不能超过 5 MB");
          continue;
        }
        if (csvCreateFiles.some((existing) => existing.name === item.name)) {
          rejected.push("已跳过重名文件");
          continue;
        }
        csvCreateFiles.push(item);
      }
      notice.hidden = rejected.length === 0;
      notice.textContent = [...new Set(rejected)].join(" · ");
      renderFiles();
    };

    const openPicker = () => file.click();
    choose.addEventListener("click", (event) => {
      event.stopPropagation();
      openPicker();
    });
    zone.addEventListener("click", openPicker);
    zone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openPicker();
      }
    });
    zone.addEventListener("dragover", (event) => {
      event.preventDefault();
      zone.classList.add("is-dragging");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("is-dragging"));
    zone.addEventListener("drop", (event) => {
      event.preventDefault();
      zone.classList.remove("is-dragging");
      addFiles([...event.dataTransfer.files]);
    });
    clear.addEventListener("click", () => {
      csvCreateFiles = [];
      notice.hidden = true;
      renderFiles();
    });
    file.addEventListener("change", () => {
      addFiles([...file.files]);
      file.value = "";
    });
    elements.createFields.append(
      group("01", "上传 CSV", [zone, notice, head, list], "最多 5 个文件，单个不超过 5 MB，总计不超过 20 MB。首行作为表头。"),
    );
    return;
  }

  if (type === "REPO_CHECK") {
    const repo = input("https://github.com/owner/repo");
    repo.id = "create-repo-url";
    elements.createFields.append(
      group("01", "仓库", [field("仓库地址", repo, "只允许可信的代码托管域名。")], "克隆后会安装依赖并跑测试，耗时取决于仓库大小。"),
    );
    return;
  }

  if (type === "WEB_ACCEPTANCE") {
    const url = input("https://example.com");
    url.id = "create-url";
    const title = input("页面标题应包含的文字");
    title.id = "create-expected-title";
    elements.createFields.append(
      group("01", "目标页面", [field("网页地址", url)]),
      group("02", "验收条件", [field("预期标题", title, "为空时只记录页面标题和状态码。")]),
    );
    return;
  }

  if (type === "WEB_COMPARE") {
    const urls = document.createElement("textarea");
    urls.id = "create-compare-urls";
    urls.rows = 4;
    urls.placeholder = "https://example.com/a\nhttps://example.com/b";
    elements.createFields.append(
      group("01", "对比链接", [field("链接", urls, "每行一个，2 到 10 个；非法链接会在校验时提示。")]),
    );
    appendCompareFields(elements.createFields, group);
    elements.createFields.append(tagEditor());
    return;
  }

  if (type !== "WEB_DIGEST") {
    const url = input("https://example.com");
    url.id = "create-url";
    elements.createFields.append(group("01", "网页地址", [field("地址", url)]));
    return;
  }

  // 网页资料整理：单页 / 多页 两个模式。
  const tabs = document.createElement("div");
  tabs.className = "mode-tabs";
  const paneSingle = document.createElement("div");
  paneSingle.className = "mode-pane";
  paneSingle.id = "create-pane-single";
  const paneCompare = document.createElement("div");
  paneCompare.className = "mode-pane";
  paneCompare.id = "create-pane-compare";
  paneCompare.hidden = true;
  const makeTab = (label, mode) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mode-tab";
    const title = document.createElement("strong");
    title.textContent = label;
    const desc = document.createElement("small");
    desc.textContent = mode === "single" ? "抓正文与截图，单页一个任务" : "合并成一份分析报告";
    button.append(title, desc);
    button.classList.toggle("is-active", digestMode === mode);
    button.addEventListener("click", () => {
      digestMode = mode;
      tabs.querySelectorAll(".mode-tab").forEach((tab) => tab.classList.remove("is-active"));
      button.classList.add("is-active");
      paneSingle.hidden = mode !== "single";
      paneCompare.hidden = mode !== "compare";
    });
    return button;
  };
  tabs.append(makeTab("单页整理", "single"), makeTab("多页对比", "compare"));

  const url = input("https://example.com");
  url.id = "create-url";
  paneSingle.append(group("01", "网页地址", [field("地址", url)]));

  const batch = document.createElement("textarea");
  batch.id = "create-batch-targets";
  batch.rows = 3;
  batch.placeholder = "https://example.com/a\nhttps://example.com/b";
  const batchPanel = document.createElement("div");
  batchPanel.className = "batch-panel";
  batchPanel.hidden = true;
  batchPanel.append(field("批量地址", batch, "每个地址单独建一个任务；需要合并报告请切到“多页对比”。"));
  const batchToggle = document.createElement("button");
  batchToggle.type = "button";
  batchToggle.className = "link-button";
  batchToggle.textContent = "＋ 批量订阅多个地址";
  batchToggle.addEventListener("click", () => {
    batchPanel.hidden = !batchPanel.hidden;
    batchToggle.textContent = batchPanel.hidden ? "＋ 批量订阅多个地址" : "收起批量订阅";
  });
  paneSingle.append(group("02", "批量订阅", [batchToggle, batchPanel], "默认收起，留空则只创建上面的单个任务。"));

  const batchName = input("例如：每周巡检");
  batchName.id = "create-batch-name";
  paneSingle.append(group("03", "批次", [field("批次名称", batchName, "可选，用于筛选和重跑。")]));

  const goals = [
    ["", "不设目标（保留正文整理）"],
    ["notes", "学习笔记"],
    ["steps", "步骤教程"],
    ["compare", "优缺点对比"],
    ["specs", "参数清单"],
    ["risks", "风险局限整理"],
    ["custom", "自定义目标…"],
  ];
  const goal = document.createElement("select");
  goal.id = "create-goal";
  goals.forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    goal.append(option);
  });
  const customGoal = document.createElement("textarea");
  customGoal.id = "create-goal-custom";
  customGoal.rows = 2;
  customGoal.hidden = true;
  customGoal.placeholder = "例如：只保留部署步骤和失败后的补救措施";
  const customWrap = field("自定义目标", customGoal);
  customWrap.hidden = true;
  goal.addEventListener("change", () => {
    const custom = goal.value === "custom";
    customGoal.hidden = !custom;
    customWrap.hidden = !custom;
  });

  const extraction = document.createElement("select");
  extraction.id = "create-extraction";
  extraction.innerHTML = '<option value="free">自由抽取（正文 + 条目）</option><option value="fields">结构化字段</option>';
  const fieldPanel = document.createElement("div");
  fieldPanel.id = "create-field-panel";
  fieldPanel.hidden = true;
  const fieldList = document.createElement("div");
  fieldList.id = "create-field-list";
  fieldList.className = "field-list";
  const fieldActions = document.createElement("div");
  fieldActions.className = "field-actions";
  const addFieldBtn = document.createElement("button");
  addFieldBtn.type = "button";
  addFieldBtn.className = "button secondary small";
  addFieldBtn.textContent = "添加字段";
  const saveTpl = document.createElement("button");
  saveTpl.type = "button";
  saveTpl.className = "button ghost small";
  saveTpl.textContent = "保存为模板";
  const applyTpl = document.createElement("button");
  applyTpl.type = "button";
  applyTpl.className = "button ghost small";
  applyTpl.textContent = "套用模板";
  fieldActions.append(addFieldBtn, saveTpl, applyTpl);
  const fieldHint = document.createElement("p");
  fieldHint.className = "field-hint";
  fieldHint.textContent = "按“字段名 + 说明”抽成表格，缺失值写“无数据”。";
  fieldPanel.append(fieldList, fieldActions, fieldHint);
  addFieldBtn.addEventListener("click", () => addFieldRow(fieldList));
  saveTpl.addEventListener("click", () => saveFieldTemplate(readFieldRows(fieldList)));
  applyTpl.addEventListener("click", () => applyFieldTemplate(fieldList));
  extraction.addEventListener("change", () => {
    fieldPanel.hidden = extraction.value !== "fields";
    if (!fieldPanel.hidden && !fieldList.children.length) addFieldRow(fieldList);
  });
  paneSingle.append(
    group("04", "阅读与抽取", [
      field("阅读目标", goal, "精读只参考正文前 10000 字，超出部分会截断。"),
      customWrap,
      field("抽取方式", extraction, "结构化字段会生成 fields.csv 与 fields.json。"),
      fieldPanel,
    ]),
  );

  const compareUrls = document.createElement("textarea");
  compareUrls.id = "create-compare-urls";
  compareUrls.rows = 4;
  compareUrls.placeholder = "https://example.com/a\nhttps://example.com/b";
  paneCompare.append(
    group("01", "对比链接", [field("链接", compareUrls, "每行一个，2 到 10 个。")]),
  );
  appendCompareFields(paneCompare, group);

  elements.createFields.append(tabs, paneSingle, paneCompare, tagEditor());
}


function openCreate(defaultType) {
  const type = pageType || defaultType || "WEB_DIGEST";
  createType = type;
  elements.createError.hidden = true;
  elements.createError.textContent = "";
  renderCreateFields(type);
  if (type === "WEB_DIGEST") {
    const list = document.getElementById("create-field-list");
    if (list && !list.children.length) addFieldRow(list);
  }
  elements.createDialog.showModal();
  // 把焦点放到第一个字段，避免关闭按钮一打开就套上焦点框。
  elements.createFields.querySelector("input, select, textarea")?.focus();
}

async function submitCreate(event) {
  event.preventDefault();
  const type = createType || document.querySelector("#create-type")?.value || "";
  if (!type) return;
  elements.createError.hidden = true;

  const fail = (message) => {
    elements.createError.textContent = message;
    elements.createError.hidden = false;
  };

  try {
    if (type === "CSV_ANALYSIS") {
      if (!csvCreateFiles.length) return fail("请选择要分析的 CSV 文件。");
      const total = csvCreateFiles.reduce((sum, item) => sum + item.size, 0);
      if (csvCreateFiles.length > 5) return fail("一次最多上传 5 个 CSV 文件。");
      if (csvCreateFiles.some((item) => item.size > 5 * 1024 * 1024)) return fail("单个 CSV 不能超过 5 MB。");
      if (total > 20 * 1024 * 1024) return fail("CSV 总大小不能超过 20 MB。");
      const body = new FormData();
      csvCreateFiles.forEach((item) => body.append("files", item));
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

    if (type === "WEB_COMPARE" || (type === "WEB_DIGEST" && digestMode === "compare")) {
      const urlsText = document.querySelector("#create-compare-urls")?.value || "";
      const urls = urlsText.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
      if (urls.length < 2) return fail("请至少填写两个网页地址。");
      if (urls.length > 10) return fail("一次最多对比 10 个网页。");
      const dimensions = [...document.querySelectorAll("[data-dimension]:checked")].map(
        (box) => box.value,
      );
      const extra = (document.querySelector("#create-compare-dimensions")?.value || "")
        .split(/[,，]/)
        .map((item) => item.trim())
        .filter(Boolean);
      const task = await api("/tasks", {
        method: "POST",
        body: JSON.stringify({
          task_type: "WEB_COMPARE",
          params: { urls, dimensions: [...dimensions, ...extra].slice(0, 8) },
        }),
      });
      const compareTags = [...document.querySelectorAll("#create-tag-chips .tag-chip")].map((chip) => chip.dataset.name);
      if (compareTags.length) {
        await api(`/tasks/${task.id}/tags`, { method: "PUT", body: JSON.stringify({ names: compareTags }) }).catch(() => {});
      }
      elements.createDialog.close();
      window.location.assign(`task_list.html?type=WEB_DIGEST&task=${task.id}`);
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
      const batchTags = [...document.querySelectorAll("#create-tag-chips .tag-chip")].map((chip) => chip.dataset.name);
      if (batchTags.length) {
        await Promise.all(created.items.filter((item) => item.task_id && !item.deduplicated).map((item) =>
          api(`/tasks/${item.task_id}/tags`, { method: "PUT", body: JSON.stringify({ names: batchTags }) }).catch(() => {}),
        ));
      }
      elements.createDialog.close();
      window.location.assign(`task_list.html?type=${type}&q=${encodeURIComponent(created.batch_id)}`);
      return;
    }

    const params = type === "REPO_CHECK" ? { repo_url: repo } : { url };
    if (type === "WEB_ACCEPTANCE" && expected) params.expected_title = expected;
    if (type === "WEB_DIGEST") {
      const goalSelect = document.querySelector("#create-goal");
      const customGoal = document.querySelector("#create-goal-custom");
      const goalValue = goalSelect?.value || "";
      if (goalValue === "custom") {
        const text = customGoal?.value.trim() || "";
        if (text) params.goal = text;
      } else if (goalValue) {
        params.goal = goalValue;
      }
      const extraction = document.querySelector("#create-extraction");
      if (extraction?.value === "fields") {
        const fields = readFieldRows(document.querySelector("#create-field-list"));
        if (fields.length) params.fields = fields;
      }
    }
    const task = await api("/tasks", {
      method: "POST",
      body: JSON.stringify({ task_type: type, params }),
    });
    const tagNames = [...document.querySelectorAll("#create-tag-chips .tag-chip")].map((chip) => chip.dataset.name);
    if (tagNames.length) {
      await api(`/tasks/${task.id}/tags`, { method: "PUT", body: JSON.stringify({ names: tagNames }) }).catch(() => {});
    }
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
  if (event.target.closest("#detail-close")) document.body.classList.remove("detail-open", "compare-open", "csv-open");
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") document.body.classList.remove("detail-open", "compare-open", "csv-open");
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
