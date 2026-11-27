// 文件职责：账号管理交互；沿用工作台外壳，支持创建、重置密码与删除。
// 所有写操作都走 /auth/users，权限由后端再校验一次。

const elements = {
  currentUser: document.querySelector("#current-user"),
  currentRole: document.querySelector("#current-role"),
  avatar: document.querySelector("#user-avatar"),
  userCard: document.querySelector("#user-card"),
  logout: document.querySelector("#logout-rail"),
  rows: document.querySelector("#user-rows"),
  count: document.querySelector("#user-count"),
  listError: document.querySelector("#list-error"),
  refresh: document.querySelector("#refresh"),
  createOpen: document.querySelector("#create-open"),
  createDialog: document.querySelector("#create-dialog"),
  createForm: document.querySelector("#create-user-form"),
  username: document.querySelector("#new-username"),
  password: document.querySelector("#new-password"),
  role: document.querySelector("#new-role"),
  submit: document.querySelector("#create-user"),
  cancel: document.querySelector("#create-cancel"),
  createClose: document.querySelector("#create-close"),
  error: document.querySelector("#form-error"),
  resetDialog: document.querySelector("#reset-dialog"),
  resetTitle: document.querySelector("#reset-title"),
  resetError: document.querySelector("#reset-error"),
  resetCancel: document.querySelector("#reset-cancel"),
  resetClose: document.querySelector("#reset-close"),
  resetConfirm: document.querySelector("#reset-confirm"),
  resetDoneDialog: document.querySelector("#reset-done-dialog"),
  resetDoneOk: document.querySelector("#reset-done-ok"),
  resetDoneClose: document.querySelector("#reset-done-close"),
  deleteDialog: document.querySelector("#delete-dialog"),
  deleteTitle: document.querySelector("#delete-title"),
  deleteError: document.querySelector("#delete-error"),
  deleteCancel: document.querySelector("#delete-cancel"),
  deleteClose: document.querySelector("#delete-close"),
  deleteConfirm: document.querySelector("#delete-confirm"),
  today: document.querySelector("#today"),
};

let currentUser = null;
let pendingUser = null;

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function shortTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
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
    const row = document.createElement("div");
    row.className = "user-row";

    const main = document.createElement("div");
    main.className = "user-main";
    const avatar = document.createElement("span");
    avatar.className = "avatar";
    avatar.textContent = String(user.username).slice(0, 1).toUpperCase();
    const text = document.createElement("div");
    text.className = "user-cell-text";
    text.append(
      Object.assign(document.createElement("strong"), { textContent: user.username }),
      Object.assign(document.createElement("small"), {
        textContent: user.last_login_at ? `最近登录 ${shortTime(user.last_login_at)}` : "尚未登录",
      }),
    );
    main.append(avatar, text);

    const role = document.createElement("span");
    role.className = "role-badge";
    role.dataset.role = user.is_admin ? "admin" : "user";
    role.textContent = user.is_admin ? "管理员" : "普通用户";

    const created = document.createElement("span");
    created.className = "user-created";
    created.textContent = formatTime(user.created_at);

    const actions = document.createElement("div");
    actions.className = "user-actions";
    const reset = document.createElement("button");
    reset.className = "button secondary small";
    reset.type = "button";
    reset.textContent = "重置密码";
    reset.addEventListener("click", () => openReset(user));
    const remove = document.createElement("button");
    remove.className = "button danger small";
    remove.type = "button";
    remove.textContent = "删除";
    if (user.id === currentUser.id) {
      remove.disabled = true;
      remove.title = "不能删除当前登录的账号";
    } else {
      remove.addEventListener("click", () => openDelete(user));
    }
    actions.append(reset, remove);

    row.append(main, role, created, actions);
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

function openCreate() {
  elements.createForm.reset();
  elements.error.textContent = "";
  elements.createDialog.showModal();
  elements.username.focus();
}

const RESET_PASSWORD = "12345678";

function openReset(user) {
  pendingUser = user;
  elements.resetError.textContent = "";
  elements.resetTitle.textContent = `重置 ${user.username} 的密码`;
  elements.resetDialog.showModal();
  elements.resetCancel.focus({ preventScroll: true });
}

function openDelete(user) {
  pendingUser = user;
  elements.deleteError.textContent = "";
  elements.deleteTitle.textContent = `删除 ${user.username}`;
  elements.deleteDialog.showModal();
  elements.deleteCancel.focus({ preventScroll: true });
}

elements.createOpen.addEventListener("click", openCreate);
elements.refresh.addEventListener("click", loadUsers);
elements.cancel.addEventListener("click", () => elements.createDialog.close());
elements.createClose.addEventListener("click", () => elements.createDialog.close());

elements.createForm.addEventListener("submit", async (event) => {
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
    elements.createDialog.close();
    await loadUsers();
  } catch (error) {
    elements.error.textContent = error.message;
  } finally {
    elements.submit.disabled = false;
  }
});

elements.resetCancel.addEventListener("click", () => elements.resetDialog.close());
elements.resetClose.addEventListener("click", () => elements.resetDialog.close());

elements.resetConfirm.addEventListener("click", async () => {
  if (!pendingUser) return;
  elements.resetError.textContent = "";
  elements.resetConfirm.disabled = true;
  try {
    await api(`/auth/users/${pendingUser.id}/password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: RESET_PASSWORD }),
    });
    elements.resetDialog.close();
    elements.resetDoneDialog.showModal();
    elements.resetDoneOk.focus({ preventScroll: true });
  } catch (error) {
    elements.resetError.textContent = error.message;
  } finally {
    elements.resetConfirm.disabled = false;
  }
});

elements.resetDoneOk.addEventListener("click", () => elements.resetDoneDialog.close());
elements.resetDoneClose.addEventListener("click", () => elements.resetDoneDialog.close());

elements.deleteCancel.addEventListener("click", () => elements.deleteDialog.close());
elements.deleteClose.addEventListener("click", () => elements.deleteDialog.close());

elements.deleteConfirm.addEventListener("click", async () => {
  if (!pendingUser) return;
  elements.deleteError.textContent = "";
  elements.deleteConfirm.disabled = true;
  try {
    await api(`/auth/users/${pendingUser.id}`, { method: "DELETE" });
    elements.deleteDialog.close();
    await loadUsers();
  } catch (error) {
    elements.deleteError.textContent = error.message;
  } finally {
    elements.deleteConfirm.disabled = false;
  }
});

elements.logout.addEventListener("click", async () => {
  await fetch("/auth/logout", { method: "POST" });
  window.location.replace("/ui/login.html");
});

function today() {
  const now = new Date();
  elements.today.textContent = now.toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "long",
  });
}

async function boot() {
  today();
  try {
    const user = await api("/auth/me");
    if (!user.is_admin) {
      window.location.replace("/ui/task_list.html");
      return;
    }
    currentUser = user;
    elements.currentUser.textContent = user.username;
    elements.currentRole.textContent = "管理员";
    elements.avatar.textContent = String(user.username).slice(0, 1).toUpperCase();
    elements.userCard.hidden = false;
    await loadUsers();
  } catch (error) {
    elements.listError.textContent = error.message;
  }
}

boot();
