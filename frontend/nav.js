/* 站内切页时的轻量反馈：旧页保持可见，顶部显示进度条，避免“点了没反应”的空白感。 */
(() => {
  const markNavigating = () => {
    document.documentElement.classList.add("is-navigating");
  };

  document.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    const anchor = event.target.closest("a[href]");
    if (!anchor || anchor.target || anchor.hasAttribute("download")) return;
    const url = new URL(anchor.href, window.location.href);
    if (url.origin !== window.location.origin || !url.pathname.startsWith("/ui/")) return;
    if (url.pathname === window.location.pathname && url.search === window.location.search) return;
    markNavigating();
  });

  window.addEventListener("pageshow", () => {
    document.documentElement.classList.remove("is-navigating");
  });
})();
