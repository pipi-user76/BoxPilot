// 定时任务页：列表、创建/编辑、立即执行与执行历史。
const TYPE_LABELS = {
  WEB_DIGEST: "网页资料整理",
  CSV_ANALYSIS: "CSV 数据分析",
  REPO_CHECK: "代码仓库体检",
  WEB_ACCEPTANCE: "网页应用验收",
  MOCK: "开发验证",
};

const TYPE_SHORT = {
  WEB_DIGEST: "网页整理",
  CSV_ANALYSIS: "CSV 分析",
  REPO_CHECK: "仓库体检",
  WEB_ACCEPTANCE: "网页验收",
  MOCK: "开发验证",
};

const state = { items: [], editing: null, historyId: null };

function $(selector) {
  return document.querySelector(selector);
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}) },
    ...options,
  });
  if (response.status === 401) {
    window.location.href = "/ui/login.html?next=/ui/schedules.html";
    throw new Error("unauthorized");
  }
  return response;
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getMonth() + 1}/${date.getDate()} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function ruleText(cron) {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return cron;
  const [minute, hour, day, month, week] = parts;
  if (minute.startsWith("*/") && hour === "*" && day === "*" && month === "*" && week === "*") {
    return `每 ${minute.slice(2)} 分钟`;
  }
  if (/^\d+$/.test(minute) && hour === "*" && day === "*" && month === "*" && week === "*") {
    return `每小时第 ${Number(minute)} 分`;
  }
  if (/^\d+$/.test(minute) && /^\d+$/.test(hour) && day === "*" && month === "*" && week === "*") {
    return `每天 ${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  }
  const names = { 0: "周日", 1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六" };
  if (/^\d+$/.test(minute) && /^\d+$/.test(hour) && day === "*" && month === "*" && names[week]) {
    return `每${names[week]} ${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  }
  return cron;
}

function cronFromForm() {
  const mode = $("#cron-mode").value;
  const minute = Number($("#cron-minute").value || 0);
  const hour = Number($("#cron-hour").value || 8);
  const day = Number($("#cron-weekday").value || 1);
  if (mode === "minutes") return `*/${Math.min(Math.max(Number($("#cron-every").value || 30), 1), 59)} * * * *`;
  if (mode === "hourly") return `${minute} * * * *`;
  if (mode === "weekly") return `${minute} ${hour} * * ${day}`;
  return `${minute} ${hour} * * *`;
}

function syncCronInputs() {
  const mode = $("#cron-mode").value;
  $("#cron-every").hidden = mode !== "minutes";
  $("#cron-hour").hidden = !["daily", "weekly"].includes(mode);
  $("#cron-minute").hidden = mode === "minutes";
  $("#cron-weekday").hidden = mode !== "weekly";
  $("#cron-preview").textContent = `cron：${cronFromForm()}`;
}

function renderFields(type, params = {}) {
  const box = $("#schedule-fields");
  const field = (label, id, value, placeholder) =>
    `<label class="field"><span class="field-label">${label}</span>
      <input id="${id}" value="${value ? String(value).replace(/"/g, "&quot;") : ""}" placeholder="${placeholder}"></label>`;
  if (type === "WEB_DIGEST") {
    box.innerHTML = field("网页地址", "param-url", params.url, "https://example.com");
  } else if (type === "REPO_CHECK") {
    box.innerHTML = field("仓库地址", "param-repo", params.repo_url, "https://github.com/owner/repo");
  } else if (type === "WEB_ACCEPTANCE") {
    box.innerHTML =
      field("网页地址", "param-url", params.url, "https://example.com") +
      field("预期标题", "param-title", params.expected_title, "页面标题");
  } else {
    box.innerHTML = field("标记（可选）", "param-tag", params.tag, "用于区分不同任务");
  }
}

function collectParams(type) {
  if (type === "WEB_DIGEST") return { url: $("#param-url").value.trim() };
  if (type === "REPO_CHECK") return { repo_url: $("#param-repo").value.trim() };
  if (type === "WEB_ACCEPTANCE") {
    return { url: $("#param-url").value.trim(), expected_title: $("#param-title").value.trim() };
  }
  const tag = $("#param-tag").value.trim();
  return tag ? { tag } : {};
}

function openDialog(record = null) {
  state.editing = record;
  $("#schedule-dialog-title").textContent = record ? "编辑定时任务" : "新建定时任务";
  $("#schedule-submit").textContent = record ? "保存" : "创建";
  $("#schedule-name").value = record ? record.name : "";
  $("#schedule-type").value = record ? record.task_type : "WEB_DIGEST";
  $("#schedule-type").disabled = Boolean(record);
  renderFields(record ? record.task_type : "WEB_DIGEST", record ? record.params : {});
  if (record) {
    const parts = record.cron.split(/\s+/);
    const minute = parts[0];
    if (minute.startsWith("*/")) {
      $("#cron-mode").value = "minutes";
      $("#cron-every").value = minute.slice(2);
    } else if (parts[1] === "*") {
      $("#cron-mode").value = "hourly";
      $("#cron-minute").value = Number(minute);
    } else if (parts[4] === "*") {
      $("#cron-mode").value = "daily";
      $("#cron-hour").value = Number(parts[1]);
      $("#cron-minute").value = Number(minute);
    } else {
      $("#cron-mode").value = "weekly";
      $("#cron-hour").value = Number(parts[1]);
      $("#cron-minute").value = Number(minute);
      $("#cron-weekday").value = parts[4];
    }
  }
  syncCronInputs();
  $("#schedule-error").hidden = true;
  $("#schedule-dialog").showModal();
  $("#schedule-name").focus();
}

function closeDialog() {
  $("#schedule-dialog").close();
}

async function submit(event) {
  event.preventDefault();
  const type = $("#schedule-type").value;
  const payload = {
    task_type: type,
    params: collectParams(type),
    cron: cronFromForm(),
    name: $("#schedule-name").value.trim(),
  };
  const missing =
    (type === "WEB_DIGEST" && !payload.params.url) ||
    (type === "REPO_CHECK" && !payload.params.repo_url) ||
    (type === "WEB_ACCEPTANCE" && (!payload.params.url || !payload.params.expected_title)) ||
    !payload.name;
  if (missing) {
    const error = $("#schedule-error");
    error.textContent = "请把名称和任务参数填写完整。";
    error.hidden = false;
    return;
  }
  const url = state.editing ? `/schedules/${state.editing.id}` : "/schedules";
  const response = await api(url, {
    method: state.editing ? "PUT" : "POST",
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    const error = $("#schedule-error");
    error.textContent = detail?.detail?.message || "保存失败，请稍后再试。";
    error.hidden = false;
    return;
  }
  closeDialog();
  await load();
}

async function load() {
  const response = await api("/schedules");
  if (!response.ok) return;
  state.items = await response.json();
  render();
}

async function lastStatus(record) {
  try {
    const response = await api(`/schedules/${record.id}/runs?limit=1`);
    if (!response.ok) return null;
    const runs = await response.json();
    return runs[0] || null;
  } catch (error) {
    return null;
  }
}

async function render() {
  const keyword = $("#schedule-search").value.trim().toLowerCase();
  const rows = state.items.filter((item) => {
    if (!keyword) return true;
    return item.name.toLowerCase().includes(keyword) || TYPE_LABELS[item.task_type].toLowerCase().includes(keyword);
  });
  const list = $("#schedule-list");
  $("#schedule-empty").hidden = rows.length > 0;
  list.innerHTML = "";
  for (const record of rows) {
    const last = await lastStatus(record);
    const statusText = !record.enabled
      ? '<span class="status-badge neutral"><i></i>已暂停</span>'
      : last
        ? last.status === "SUCCESS"
          ? '<span class="status-badge success"><i></i>成功</span>'
          : last.status === "RUNNING" || last.status === "PENDING"
            ? '<span class="status-badge running"><i></i>运行中</span>'
            : '<span class="status-badge danger"><i></i>失败</span>'
        : '<span class="status-badge neutral"><i></i>未执行</span>';
    const row = document.createElement("div");
    row.className = "schedule-row";
    row.innerHTML = `
      <button class="schedule-name" type="button">
        <strong>${record.name}</strong>
        <small>#${record.id.slice(0, 8)}</small>
      </button>
      <span class="row-type">${TYPE_SHORT[record.task_type]}</span>
      <span class="row-rule">${ruleText(record.cron)}</span>
      <span class="row-next">${formatTime(record.next_run_at)}</span>
      <span class="row-status">${statusText}</span>
      <span class="row-actions">
        <button type="button" class="link-button" data-action="toggle">${record.enabled ? "暂停" : "启用"}</button>
        <button type="button" class="link-button" data-action="run">立即执行</button>
        <button type="button" class="link-button" data-action="edit">编辑</button>
        <button type="button" class="link-button danger" data-action="delete">删除</button>
      </span>`;
    row.querySelector(".schedule-name").addEventListener("click", () => openHistory(record));
    row.querySelector('[data-action="toggle"]').addEventListener("click", () => toggle(record));
    row.querySelector('[data-action="run"]').addEventListener("click", () => runNow(record));
    row.querySelector('[data-action="edit"]').addEventListener("click", () => openDialog(record));
    row.querySelector('[data-action="delete"]').addEventListener("click", () => remove(record));
    list.append(row);
  }
}

async function toggle(record) {
  await api(`/schedules/${record.id}`, {
    method: "PATCH",
    body: JSON.stringify({ enabled: !record.enabled }),
  });
  await load();
}

async function runNow(record) {
  const response = await api(`/schedules/${record.id}/run`, { method: "POST" });
  if (response.ok) {
    const task = await response.json();
    window.location.href = `/ui/task_list.html?task=${encodeURIComponent(task.id)}`;
  }
}

async function remove(record) {
  if (!window.confirm(`删除定时任务「${record.name}」？已创建的历史任务会保留。`)) return;
  await api(`/schedules/${record.id}`, { method: "DELETE" });
  await load();
}

async function openHistory(record) {
  state.historyId = record.id;
  $("#history-title").textContent = record.name;
  const list = $("#history-list");
  list.innerHTML = '<p class="empty-state">加载中…</p>';
  $("#history-dialog").showModal();
  const response = await api(`/schedules/${record.id}/runs?limit=20`);
  const runs = response.ok ? await response.json() : [];
  if (!runs.length) {
    list.innerHTML = '<p class="empty-state">还没有执行记录。</p>';
    return;
  }
  list.innerHTML = runs
    .map((task) => {
      const tone = task.status === "SUCCESS" ? "success" : task.status === "RUNNING" ? "running" : "danger";
      const label = task.status === "SUCCESS" ? "成功" : task.status === "RUNNING" ? "运行中" : "失败";
      return `<a class="history-row" href="/ui/task_list.html?task=${encodeURIComponent(task.id)}">
        <span>${formatTime(task.created_at)}</span>
        <span class="status-badge ${tone}"><i></i>${label}</span>
        <span>${(task.steps || []).length} 个步骤</span>
      </a>`;
    })
    .join("");
}

async function loadUser() {
  const response = await api("/auth/me");
  if (!response.ok) return;
  const user = await response.json();
  $("#current-user").textContent = user.username;
  $("#current-role").textContent = user.is_admin ? "管理员" : "成员";
  $("#user-avatar").textContent = user.username.slice(0, 1).toUpperCase();
  $("#user-card").hidden = false;
}

async function loadHealth() {
  const badge = $("#health");
  try {
    const response = await fetch("/health");
    const data = await response.json();
    const ok = data.status === "ok";
    badge.textContent = ok ? "环境正常" : "环境降级";
    badge.dataset.state = data.status;
  } catch (error) {
    badge.textContent = "服务不可用";
    badge.dataset.state = "degraded";
  }
}

$("#today").textContent = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric", month: "long", day: "numeric", weekday: "long",
}).format(new Date());

$("#create-open").addEventListener("click", () => openDialog());
$("#refresh").addEventListener("click", load);
$("#schedule-search").addEventListener("input", render);
$("#schedule-form").addEventListener("submit", submit);
$("#schedule-cancel").addEventListener("click", closeDialog);
$("#schedule-cancel-2").addEventListener("click", closeDialog);
$("#schedule-type").addEventListener("change", (event) => renderFields(event.target.value));
["#cron-mode", "#cron-every", "#cron-hour", "#cron-minute", "#cron-weekday"].forEach((selector) => {
  $(selector).addEventListener("change", syncCronInputs);
});
$("#history-close").addEventListener("click", () => $("#history-dialog").close());
$("#logout-rail").addEventListener("click", async () => {
  await fetch("/auth/logout", { method: "POST" });
  window.location.href = "/ui/login.html";
});

loadUser();
loadHealth();
load();

if (location.hash === "#new") {
  openDialog();
}
