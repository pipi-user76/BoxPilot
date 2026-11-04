/*
 * 文件职责：处理任务类型切换、参数收集和创建接口提交。
 * CSV 使用 multipart/form-data，其余任务使用 JSON；接口错误会显示在表单内。
 */

const form = document.querySelector("#create-form");
const taskType = document.querySelector("#task-type");
const message = document.querySelector("#form-message");

const fieldMap = {
  WEB_DIGEST: ["url"],
  CSV_ANALYSIS: ["file"],
  REPO_CHECK: ["repo_url"],
  WEB_ACCEPTANCE: ["url", "expected_title"],
};

function updateFields() {
  // 每种模板只显示自身需要的字段，防止用户提交无关参数。
  const visibleFields = new Set(fieldMap[taskType.value] || []);
  document.querySelectorAll("[data-field]").forEach((field) => {
    field.hidden = !visibleFields.has(field.dataset.field);
  });
}

function valueOf(id) {
  return document.querySelector(`#${id}`).value.trim();
}

async function readError(response) {
  const body = await response.json().catch(() => null);
  return body?.detail?.message || body?.detail || `请求失败 (${response.status})`;
}

taskType.addEventListener("change", updateFields);
updateFields();

form.addEventListener("submit", async (event) => {
  // 提交时禁用按钮，防止重复创建同一任务。
  event.preventDefault();
  message.textContent = "正在提交...";
  const submit = form.querySelector("button[type=submit]");
  submit.disabled = true;

  try {
    let response;
    if (taskType.value === "CSV_ANALYSIS") {
      const fileInput = document.querySelector("#file");
      if (!fileInput.files.length) throw new Error("请选择 CSV 文件");
      const body = new FormData();
      body.append("file", fileInput.files[0]);
      response = await fetch("/tasks/csv", { method: "POST", body });
    } else {
      const params = {};
      for (const field of fieldMap[taskType.value] || []) {
        const value = valueOf(field);
        if (value) params[field] = value;
      }
      response = await fetch("/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_type: taskType.value, params }),
      });
    }
    if (!response.ok) throw new Error(await readError(response));
    const task = await response.json();
    window.location.assign(`task_list.html?task=${encodeURIComponent(task.id)}`);
  } catch (error) {
    message.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});
