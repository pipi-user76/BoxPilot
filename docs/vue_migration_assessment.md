# Vue 前端迁移评估与执行方案

日期：2026-11-28  
评估对象：`C:\Users\唔系迪西\Desktop\BoxPilot_vue_20261128.zip`  
对照基线：当前仓库 `D:\github project\BoxPilot`

## 1. 结论

**不建议把 zip 直接合并进当前仓库，也不建议现在为了“换框架”做整体重写。**

原因不是 Vue 本身，而是这份 zip 的实际完成度和当前仓库的差距：

- 它是“Vue 外壳 + 旧控制器兼容层”，不是完整的 Vue 组件化重写。
- 它缺少当前已经上线的知识库独立页、对比对话、文档检索范围、流式状态、任务详情铺满等改动。
- 它的路由里没有 `/ui/kb.html`，知识库仍塞在 `TasksView.vue` 的 `#kb-panel` 中；`Sidebar.vue` 也没有知识库入口。
- 它的 `controllers/*.js` 是旧版前端脚本的拷贝，例如 `task_list` 41,732B（当前 69,032B）、`kb` 14,505B（当前 `kb_page.js` 30,796B）。
- zip 的 `frontend/` 只有 17 个旧文件；当前仓库 `frontend/` 是 21 个文件、282,755B。

如果团队的目标是长期组件化、多人协作和前端测试，可以做**以当前仓库为基线的增量迁移**；如果目标只是“用上 Vue”，收益不足以抵消回归风险。

## 2. zip 审计

### 2.1 技术栈与结构

- Vue `^3.5.46`、Pinia `^3.0.4`、Vue Router `^4.6.5`、Vite `^7.3.8`、TypeScript `^5.9.3`。
- 4 个 view：`LoginView`、`TasksView`、`SchedulesView`、`UsersView`；1 个 `Sidebar.vue`；25 个 `src` 文件。
- Node 要求 `>=22.12.0`，包管理器 `pnpm@11.25.0`。
- 构建产物由 FastAPI 托管：存在 `frontend_dist/index.html` 时挂 `/ui`，否则回退原 `frontend/`。
- 生产不需要 Node 进程，构建时需要 Node + pnpm。

### 2.2 实际迁移方式

`webapp/src/lib/page.ts::usePage(controllers)` 在 `onMounted` 里创建 `createScope(root, router)`，然后逐个运行旧控制器；`scope.ts` 用 Proxy 把 `window/document/fetch/timers` 限定到当前页面。

`UsersView.vue` 这类页面只是把旧 HTML 复制进 template，保留 `#create-open`、`.page-head`、`.topbar` 等 ID/class，再调用 `usePage([mount_users])`。业务状态仍在旧控制器里，通过 DOM 读写完成，不是响应式 Vue。

Pinia 只有一个 `useSession`（`user/loaded/load/reset`），用于 `/auth/me`；任务、知识库、调度状态都没有进 store。

`KeepAlive` 按 `route.path + ':' + route.query.type` 缓存页面，`scope` 负责暂停/恢复轮询和监听。这部分设计可以借鉴。

### 2.3 与当前仓库的功能差距

| 能力 | zip | 当前仓库 |
| --- | --- | --- |
| 知识库独立页 `/ui/kb.html` | 无路由、无侧栏入口 | 有独立页、删除/重命名/文档选择 |
| 对比详情对话 | 旧控制器 | 流式状态、自动摘要降级、铺满布局 |
| 文档检索范围 | 无 | 草稿+保存、localStorage、空集合拦截 |
| 文档标题 | 旧拼接 | 语义标题、404 过滤、同步脚本 |
| 任务标签/快照/导出 | 旧版本 | schema v10、接口与 UI |
| 前端文件 | 17 个旧文件 | 21 个文件、282KB |

`docs/vue_migration.md` 自己也写明：“部分成熟业务渲染仍复用原 JavaScript 控制器，并非每一段业务代码都已重写为响应式 Vue/TypeScript”；视觉核对只覆盖 1440×900 的关键区域。

## 3. 更换框架的必要性

### 3.1 收益

- Router + KeepAlive：切页不整页刷新，返回保留搜索/筛选状态。
- 组件复用：侧栏、通知、详情、弹窗、文档卡可以抽成组件。
- 类型与测试：TypeScript + Vitest 能覆盖状态和 API 边界。
- 构建拆分：按页面懒加载，减少首屏脚本。

### 3.2 成本

- 当前 5 个页面、21 个静态文件，零构建、零 Node 依赖；引入 Vite 后部署链多一步。
- 现有页面大量依赖 DOM ID、CSS 作用域、Canvas、SSE、文件选择器；逐页迁移都要做视觉回归。
- zip 的兼容层把 `window/document/fetch` 代理到页面作用域，调试成本高于普通 Vue；继续沿用等于“Vue 壳 + 旧代码”，组件化收益有限。
- 迁移期间要同时维护两套入口，容易出现登录态、通知轮询、localStorage、SSE 双份逻辑。

### 3.3 判断

| 方案 | 适用条件 | 结论 |
| --- | --- | --- |
| A. 保持原生，按模块抽取 | 当前 1–2 人维护、页面数量稳定 | **当前推荐** |
| B. 以当前仓库为基线增量迁移 Vue | 前端持续迭代、多人协作、需要组件库/测试 | 可行，按第 4 节执行 |
| C. 合并 zip 后替换 | 希望立即得到 Vue 工程 | **不推荐**，会回退知识库与对比功能 |

触发 B 的信号：前端页面继续增加、同一交互在 3 处以上重复、需要前端单测/E2E、多人并行改前端。

## 4. 如果采用 Vue：不改展示的迁移方案

原则：URL 不变、DOM/class 不变、CSS 视觉不变、后端接口不变、随时可回退。

### 4.1 基线冻结

1. 在当前仓库打 tag（例如 `pre-vue-migration`）。
2. 用现有 8031/8033 抓取基线截图：1440×900、1280×937、390×844；覆盖登录、全部任务、四个功能页、任务详情（折叠/展开产物）、知识库（三栏/抽屉/空会话/删除框）、定时任务、账号管理。
3. 记录关键 DOM 尺寸与颜色：侧栏 280px、详情 340px、知识库三栏 220/1fr/320、按钮高度 38/42px。
4. 锁定接口契约：`/auth/*`、`/tasks*`、`/kb*`、`/schedules*`、`/notifications*`、`/health`。

### 4.2 工程骨架

- 新建 `webapp/`：Vue 3 + TS + Vite + Router + Pinia；从当前 `frontend/` 复制 CSS 与静态资源，不采用 zip 里的旧控制器。
- 保留 `frontend/` 作为回退；构建输出 `frontend_dist/`。
- `backend/main.py` 增加 `FRONTEND_MODE`（`static` 默认 / `vue`）：`vue` 且 `frontend_dist/index.html` 存在时挂构建产物，否则回退 `frontend/`。
- 保持 `/ui/*.html` 地址；Router 使用 `createWebHistory()`，后端对 `/ui/*` 非静态路径回退 `index.html`。
- 构建脚本 `scripts/build_frontend.ps1`（或 `.cmd`）：`pnpm install --frozen-lockfile`、`pnpm build`；CI 只跑构建，不跑 dev server。

### 4.3 壳层（第 1 阶段）

- `App.vue`、`router.ts`、`Sidebar.vue`、`session` store、通知轮询、Toast、错误边界。
- 路由必须包含：`login`、`task_list`、`kb`、`schedules`、`users`；`create_task.html` 重定向。
- 站内 `<a>` 拦截覆盖全部 `/ui/*.html`，包括 `kb.html`；未登录守卫、管理员守卫与当前后端一致。
- 页面标题映射补“知识库”。

### 4.4 页面迁移顺序（每页一个可回滚 PR）

1. 登录页：保留 Canvas 画廊，只把表单/请求状态 Vue 化。
2. 账号管理：表格、创建/重置/删除对话框。
3. 定时任务：列表、cron 构造器、运行历史抽屉。
4. 任务列表 + 详情：列表筛选/分页、行选择、标签 chips、详情标签页、产物预览、对比/知识库对话。
5. 知识库：三栏布局、文档抽屉、勾选草稿/保存、删除确认、空状态、标题。
6. 创建任务弹窗：单页/多页两栏、标签、字段抽取。

每页迁移时保留原 HTML 的 ID/class 作为过渡，先让 Vue 控制数据，再把 DOM 控制器替换为 composable；不要同时改视觉。

### 4.5 兼容层退出

- 短期可复用 `usePage/scope` 跑旧控制器，但只作为过渡；每个页面迁完后删除对应 `controllers/*.js`。
- 新代码禁止直接 `document.querySelector` 全局查找；统一通过模板 ref 或组件事件。
- API 客户端集中到 `webapp/src/api/`，类型从 Pydantic/接口响应手写或用 OpenAPI 生成。

### 4.6 视觉与交互验收

每个页面必须通过：

- URL/query 与当前一致（含 `?type=`、`?task=`、`?status=`）。
- 1440/1280/390 三档截图对照；布局尺寸偏差 ≤2px，颜色/字号一致。
- 折叠/展开、空状态、错误态、加载态、长文本、窄屏覆盖层。
- 任务轮询 3s 不闪动；切换页面暂停/恢复；SSE 问答不重复。
- 登录态、退出、管理员权限、通知未读、localStorage 键（`kb.selection.*`、`kb.docsOpen`）保持兼容。

### 4.7 切换与回滚

- 默认 `FRONTEND_MODE=static`；验收通过后切 `vue`，保留一个发布周期。
- 回滚只需把环境变量切回 `static`，不动数据库与接口。
- 构建产物带内容哈希，HTML `no-store`，`/ui/assets/*` 长缓存；与当前缓存策略一致。

### 4.8 工作量估算

- 壳层/构建/路由/会话：1.5–2 人日。
- 登录 + 账号 + 定时：1.5–2 人日。
- 任务列表/详情/对比对话：2–3 人日。
- 知识库（三栏/选择/删除/文档卡）：1.5–2 人日。
- 视觉回归与修复：1.5–2 人日。
- 合计约 **8–11 人日**，不含后续组件化重构。

## 5. 对 zip 的处理建议

- 不合并 zip 的 `backend/` 与 `frontend/` 快照；它们落后于当前功能。
- 可以借鉴 `webapp/src/lib/page.ts`、`scope.ts`、`App.vue` 的 KeepAlive/页面作用域思路，以及 `build-frontend.cmd` 的构建托管方式。
- 如果团队坚持使用 zip，先把它作为独立分支跑通构建与 1440 截图，再逐页把当前功能补回去；不要直接覆盖 `frontend/`。

## 6. 待确认

1. 是否把 Vue 迁移列为下一阶段目标，还是先保持原生、只做组件抽取？
2. 若迁移，是否接受 `webapp/ + frontend_dist` 的双目录与 Node 22/pnpm 构建依赖？
3. 视觉验收以哪三档分辨率为准（建议 1440×900、1280×937、390×844）。
