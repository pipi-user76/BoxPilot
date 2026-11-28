# 前端白屏治理执行方案

日期：2026-11-28  
范围：`frontend/` 的登录、任务、知识库、定时任务、账号管理页面；以及如果启用 Vue 构建时的 SPA 路由。

## 1. 结论与根因

当前仓库是 MPA（每个 tab 是一个独立 HTML 文档）。点击侧栏时浏览器会卸载旧页、下载并解析新文档；在新文档的 CSS 生效之前，浏览器先画默认白底，所以出现一瞬白屏。不是某个页面 JS 崩了——直接访问 8 个页面都能正常渲染。

如果白屏持续存在或整页全空，则要分两类：

- 静态页：HTML/CSS 请求失败、JS 语法错误、鉴权重定向循环。
- Vue 构建：路由缺失（当前 zip 没有 `/ui/kb.html`）、history fallback 未配置、`beforeEach` 异步守卫未返回、组件 setup 抛错且没有 error boundary。

## 2. P0：当前静态页的抗白屏（已实施）

目标：点击任意 tab，旧页保持可见，新页首帧就是应用背景色，不出现白底。

1. **首帧背景**
   - 每个 HTML 的 `<head>` 内联 `<style>html{background:#f4f6f9}</style>`，登录页为 `#000`。
   - 背景在外部 CSS 下载前就生效，慢网/冷缓存也不会先白。
2. **跨文档过渡**
   - `styles.css` / `login.css` 增加：
     ```css
     @view-transition { navigation: auto; }
     ::view-transition-old(root), ::view-transition-new(root) { animation-duration: 160ms; }
     ```
   - Chrome/Edge 126+ 会把旧页与新页做交叉淡入，不再出现中间白帧。
3. **预取**
   - 每个页面 `<head>` 加 Speculation Rules：
     ```html
     <script type="speculationrules">
     {"prefetch":[{"source":"list","urls":["task_list.html","kb.html","schedules.html","users.html"]}]}
     </script>
     ```
   - 浏览器空闲时预取四个工作区文档，点击时直接命中缓存。
4. **切页反馈**
   - 新增 `frontend/nav.js`：拦截同源 `/ui/*.html` 链接点击，给 `<html>` 加 `is-navigating`。
   - `styles.css` 顶部显示 3px 进度条（登录页绿色），旧页不会变白或消失；`pageshow` 清除状态。
5. **版本**
   - `styles.css?v=20261128-15`、`login.css?v=20261128-10`、`nav.js?v=20261128-1`。
   - 静态资源 `no-store`，HTML 不需要重启后端；浏览器 `Ctrl+F5` 即可。

## 3. P1：验证矩阵（必须全过）

| 场景 | 操作 | 通过标准 |
| --- | --- | --- |
| 侧栏切换 | 任务↔知识库↔定时↔账号，各 10 次 | 无全白帧；顶部进度条出现；新页首屏 ≤300ms（本地） |
| 功能 tab | 全部/网页整理/CSV/仓库/验收，各 5 次 | 列表不整体消失，筛选后正常刷新 |
| 深链接 | 直接打开 `/ui/kb.html`、`?task=`、`?type=` | 不跳错页、不白屏 |
| 登录/退出 | 未登录访问受保护页、退出后再进 | 跳登录页有背景色，不白屏 |
| 慢网 | DevTools Slow 3G + 4× CPU | 旧页保持可见，进度条出现；新页首帧为背景色 |
| 硬刷新 | Ctrl+F5 每个页面 | 无 404、无 console error |
| 回退 | 浏览器后退/前进 | 页面完整渲染，状态不丢 |

自动化建议（后续）：

- 用 Playwright 对每个 tab 点击并记录 `requestAnimationFrame` 首帧截图，比较非空像素占比；连续 30 次无空帧。
- 监听 `pageerror`、`unhandledrejection`、`console.error`，任何一条即失败。
- 记录 `performance.getEntriesByType('paint')` 的 `first-paint` 与 `first-contentful-paint`，目标差值 <150ms。

## 4. P2：如果使用 Vue 构建，必须补的防白屏项

1. 路由完整：补 `/ui/kb.html`，`create_task.html` 重定向，`/ui/:pathMatch(.*)*` 指向 NotFound 而不是静默白屏。
2. Vite `base: '/ui/'`；FastAPI 对 `/ui/*` 非静态文件回退 `index.html`，并保持 `/ui/assets/*` 长缓存。
3. `beforeEach` 的 `await session.load()` 必须 `try/finally`，失败也要放行到登录页或错误页；首屏先渲染壳，再加载数据。
4. `app.config.errorHandler` + `router.onError` + `window.onerror/unhandledrejection` 统一渲染错误卡片，禁止留空 `<div id="app">`。
5. KeepAlive 页面切换加 `Suspense` 或 loading 占位；异步组件 `defineAsyncComponent` 配 error/loading 组件。
6. 登录页与工作区样式隔离（scoped 或独立 CSS），避免 `.panel` 等规则污染导致整页不可见。
7. 验收：所有 tab 连续点击 50 次、慢网、深链接、后退、刷新，无白屏；console 零错误。

## 5. P3：监控与回滚

- 在 `nav.js` 记录 `pageshow` 时间与 `performance.now()`，超过 1s 的切页写入 `sessionStorage` 最近 5 条，便于用户回报。
- 可选：上报 `/api/client-events`（需后端新增）记录 `pageerror`、白屏时长；没有后端时先保存在 localStorage。
- 回滚：P0 全部是静态文件改动，回退 `styles.css`/`login.css`/`nav.js`/HTML 版本即可；无数据库、接口变更。

## 6. 本次改动清单

- `frontend/styles.css`：`@view-transition`、`html` 背景、`is-navigating` 进度条。
- `frontend/login.css`：同上（黑色背景、绿色进度条）。
- `frontend/nav.js`：站内导航拦截与 `is-navigating`。
- `frontend/task_list.html`、`kb.html`、`schedules.html`、`users.html`、`login.html`：内联背景、speculation rules、`nav.js`、版本号。
- `frontend/create_task.html`：内联背景（立即重定向页）。

全量 150 项测试通过。
