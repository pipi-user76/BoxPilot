// 文件职责：账号管理页交互；只允许管理员访问，创建账号走 /auth/users。

const elements = {
  currentUser: document.querySelector("#current-user"),
  logout: document.querySelector("#logout"),
  rows: document.querySelector("#user-rows"),
  count: document.querySelector("#user-count"),
  listError: document.querySelector("#list-error"),
  form: document.querySelector("#create-user-form"),
  username: document.querySelector("#new-username"),
  password: document.querySelector("#new-password"),
  role: document.querySelector("#new-role"),
  submit: document.querySelector("#create-user"),
  error: document.querySelector("#form-error"),
};

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (response.status === 401) {
    window.location.replace("/ui/login.html?next=/ui/users.html");
    throw new Error("未登录");
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload && payload.detail;
    throw new Error((detail && detail.message) || `请求失败（${response.status}）`);
  }
  return payload;
}

function renderUsers(users) {
  elements.rows.replaceChildren();
  elements.count.textContent = `${users.length} 个账号`;
  for (const user of users) {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = user.username;
    const role = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "role-badge";
    badge.dataset.role = user.is_admin ? "admin" : "user";
    badge.textContent = user.is_admin ? "管理员" : "普通用户";
    role.append(badge);
    const created = document.createElement("td");
    created.textContent = formatTime(user.created_at);
    const last = document.createElement("td");
    last.textContent = formatTime(user.last_login_at);
    row.append(name, role, created, last);
    elements.rows.append(row);
  }
}

async function loadUsers() {
  try {
    const users = await api("/auth/users");
    elements.listError.textContent = "";
    renderUsers(users);
  } catch (error) {
    elements.listError.textContent = error.message;
  }
}

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  elements.error.textContent = "";
  elements.submit.disabled = true;
  try {
    await api("/auth/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: elements.username.value.trim(),
        password: elements.password.value,
        is_admin: elements.role.value === "admin",
      }),
    });
    elements.form.reset();
    await loadUsers();
  } catch (error) {
    elements.error.textContent = error.message;
  } finally {
    elements.submit.disabled = false;
  }
});

elements.logout.addEventListener("click", async () => {
  await fetch("/auth/logout", { method: "POST" });
  window.location.replace("/ui/login.html");
});

async function boot() {
  try {
    const user = await api("/auth/me");
    if (!user.is_admin) {
      window.location.replace("/ui/task_list.html");
      return;
    }
    elements.currentUser.textContent = `${user.username} · 管理员`;
    elements.currentUser.hidden = false;
    elements.logout.hidden = false;
    await loadUsers();
  } catch (error) {
    elements.listError.textContent = error.message;
  }
}

boot();
