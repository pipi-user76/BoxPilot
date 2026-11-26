// 文件职责：登录页交互；只负责表单提交与跳转，权限判断全部在后端完成。

const params = new URLSearchParams(window.location.search);
const requested = params.get("next") || "";
// 只接受站内 /ui/ 路径，防止被构造成跳到外部站点。
const nextUrl = requested.startsWith("/ui/") && !requested.startsWith("//")
  ? requested
  : "/ui/task_list.html";

const errorBox = document.querySelector("#error");
const loginForm = document.querySelector("#login-form");
const registerForm = document.querySelector("#register-form");
const toggle = document.querySelector("#toggle-register");

function showError(message) {
  errorBox.textContent = message || "";
  if (message) {
    const button = document.querySelector("#submit");
    button.classList.remove("shake");
    void button.offsetWidth; // 重排一次，确保连续失败也能重新播放动画
    button.classList.add("shake");
  }
}

async function request(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  const button = loginForm.querySelector("button");
  button.disabled = true;
  try {
    await request("/auth/login", {
      username: document.querySelector("#username").value.trim(),
      password: document.querySelector("#password").value,
    });
    window.location.replace(nextUrl);
  } catch (error) {
    showError(error.message);
  } finally {
    button.disabled = false;
  }
});

registerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  const button = registerForm.querySelector("button");
  button.disabled = true;
  try {
    await request("/auth/register", {
      username: document.querySelector("#reg-username").value.trim(),
      password: document.querySelector("#reg-password").value,
    });
    window.location.replace(nextUrl);
  } catch (error) {
    showError(error.message);
  } finally {
    button.disabled = false;
  }
});

toggle.addEventListener("click", () => {
  const visible = registerForm.classList.toggle("visible");
  loginForm.classList.toggle("secondary", visible);
  toggle.parentElement.hidden = visible;
  document.querySelector("#title").textContent = visible ? "创建管理员账号" : "登录";
});

// 只有系统里还没有任何账号时才提示“创建管理员”，避免普通用户看到无效入口。
fetch("/auth/bootstrap")
  .then((response) => (response.ok ? response.json() : { needs_admin: false }))
  .then((data) => {
    if (!data.needs_admin) {
      toggle.parentElement.hidden = true;
    }
  })
  .catch(() => {
    toggle.parentElement.hidden = true;
  });
