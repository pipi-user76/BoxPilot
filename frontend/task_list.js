/*
 * 文件职责：轮询任务列表、渲染步骤和产物，并定时刷新健康状态。
 * 所有文本通过 textContent 写入，避免任务输入被当作 HTML 注入页面。
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
};

let selectedTaskId = new URLSearchParams(window.location.search).get("task");
let refreshTimer = null;

const elements = {
  list: document.querySelector("#task-list"),
  empty: document.querySelector("#empty-state"),
  health: document.querySelector("#health"),
  refresh: document.querySelector("#refresh"),
  filter: document.querySelector("#status-filter"),
  detail: document.querySelector("#detail-panel"),
  lastUpdated: document.querySelector("#last-updated"),
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

async function api(url, options = {}) {
  // 所有接口错误优先读取后端结构化 detail，保证页面显示可理解的信息。
  const response = await fetch(url, { headers: { Accept: "application/json" }, ...options });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const message = body?.detail?.message || body?.detail || `请求失败 (${response.status})`;
    throw new Error(message);
  }
  return body;
}

function renderCounts(tasks) {
  elements.counts.total.textContent = String(tasks.length);
  elements.counts.running.textContent = String(tasks.filter((task) => task.status === "RUNNING").length);
  elements.counts.success.textContent = String(tasks.filter((task) => task.status === "SUCCESS").length);
  elements.counts.failed.textContent = String(
    tasks.filter((task) => task.status === "FAILED" || task.status === "TIMEOUT").length,
  );
}

function renderTaskList(tasks) {
  // 列表只使用最新快照重建，选中任务则通过额外请求刷新详情。
  elements.list.replaceChildren();
  const filter = elements.filter.value;
  const visibleTasks = filter ? tasks.filter((task) => task.status === filter) : tasks;
  elements.empty.hidden = visibleTasks.length > 0;

  for (const task of visibleTasks) {
    const row = element("button", "task-row");
    row.type = "button";
    row.dataset.taskId = task.id;
    row.setAttribute("aria-current", String(task.id === selectedTaskId));

    const name = element("div", "task-name");
    name.append(
      element("strong", "", taskTypeLabels[task.task_type] || task.task_type),
      element("span", "task-meta", `#${task.id.slice(0, 8)} · ${task.steps.length} 个步骤`),
    );
    row.append(
      name,
      element("span", "status-badge", statusLabels[task.status] || task.status),
      element("span", "task-time", formatTime(task.created_at)),
    );
    row.querySelector(".status-badge").dataset.status = task.status;
    row.addEventListener("click", () => selectTask(task.id));
    elements.list.append(row);
  }
}

function renderStep(step) {
  const item = element("li", "step-item");
  const content = element("div");
  content.append(element("strong", "", `${step.step_index}. ${step.name}`));
  if (step.output) content.append(element("small", "", step.output));
  if (step.error_message) content.append(element("small", "error-text", step.error_message));
  const badge = element("span", "status-badge", statusLabels[step.status] || step.status);
  badge.dataset.status = step.status;
  item.append(content, badge);
  return item;
}

function renderArtifact(artifact) {
  const item = element("li", "artifact-item");
  const content = element("div");
  content.append(
    element("strong", "", artifact.filename),
    element("small", "", `${Math.max(1, Math.round(artifact.size_bytes / 1024))} KB`),
  );
  const link = element("a", "button secondary", "下载");
  link.href = `/artifacts/${encodeURIComponent(artifact.id)}/download`;
  item.append(content, link);
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
  );

  content.append(heading, meta);

  if (task.error_message) {
    const error = element("section", "detail-section");
    error.append(element("h3", "", "错误信息"), element("p", "error-text", `${task.error_code}: ${task.error_message}`));
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

async function refresh() {
  // 定时轮询使用 setTimeout 串行调度，避免请求重叠导致旧数据覆盖新数据。
  elements.refresh.disabled = true;
  try {
    const tasks = await api("/tasks");
    renderCounts(tasks);
    renderTaskList(tasks);
    elements.lastUpdated.textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
    if (selectedTaskId) await selectTask(selectedTaskId);
  } catch (error) {
    elements.list.replaceChildren();
    elements.empty.hidden = false;
    elements.empty.querySelector("strong").textContent = "无法加载任务";
    elements.empty.querySelector("span").textContent = error.message;
  } finally {
    elements.refresh.disabled = false;
    window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(refresh, 3000);
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

elements.refresh.addEventListener("click", refresh);
elements.filter.addEventListener("change", refresh);
refreshHealth();
refresh();
