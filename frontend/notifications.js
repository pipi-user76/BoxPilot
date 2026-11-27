// 全局通知铃铛：所有页面共用，轮询未读数并在下拉面板里展示最近通知。
(function () {
  const POLL_MS = 30000;
  let open = false;
  let unread = 0;

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function build() {
    const topbar = document.querySelector(".topbar");
    if (!topbar) return null;
    const wrap = document.createElement("div");
    wrap.className = "notify";
    wrap.innerHTML = `
      <button type="button" class="notify-button" id="notify-toggle" aria-label="通知">
        <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7">
          <path d="M18 8a6 6 0 1 0-12 0c0 6-2 7-2 7h16s-2-1-2-7" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="M10.3 20a2 2 0 0 0 3.4 0" stroke-linecap="round"/>
        </svg>
        <span class="notify-dot" hidden></span>
      </button>
      <div class="notify-panel" hidden>
        <div class="notify-head">
          <strong>通知</strong>
          <button type="button" class="link-button" id="notify-read-all">全部已读</button>
        </div>
        <div class="notify-list" id="notify-list"><p class="notify-empty">暂无通知</p></div>
      </div>`;
    topbar.append(wrap);
    return wrap;
  }

  async function load() {
    try {
      const response = await fetch("/notifications?limit=8", { headers: { Accept: "application/json" } });
      if (!response.ok) return;
      const data = await response.json();
      unread = data.unread || 0;
      const dot = document.querySelector(".notify-dot");
      if (dot) dot.hidden = unread === 0;
      const list = document.getElementById("notify-list");
      if (!list) return;
      if (!data.items.length) {
        list.innerHTML = '<p class="notify-empty">暂无通知</p>';
        return;
      }
      list.innerHTML = data.items
        .map((item) => {
          const kind = item.kind === "error" ? "报错" : "完成";
          const tone = item.kind === "error" ? "error" : "success";
          return `<a class="notify-item${item.read_at ? "" : " unread"}" href="/ui/task_list.html?task=${encodeURIComponent(item.task_id)}">
            <span class="notify-kind ${tone}">${kind}</span>
            <span class="notify-body">
              <strong>${escapeHtml(item.title)}</strong>
              <small>${escapeHtml(item.body || "")}</small>
            </span>
          </a>`;
        })
        .join("");
    } catch (error) {
      /* 通知失败不影响主流程 */
    }
  }

  function toggle() {
    const panel = document.querySelector(".notify-panel");
    if (!panel) return;
    open = !open;
    panel.hidden = !open;
    if (open) load();
  }

  document.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (target.closest("#notify-toggle")) {
      toggle();
      return;
    }
    if (target.closest("#notify-read-all")) {
      await fetch("/notifications/read-all", { method: "POST" });
      load();
      return;
    }
    if (!target.closest(".notify") && open) {
      open = false;
      const panel = document.querySelector(".notify-panel");
      if (panel) panel.hidden = true;
    }
  });

  build();
  load();
  setInterval(load, POLL_MS);
})();
