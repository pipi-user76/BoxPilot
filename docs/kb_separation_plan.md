# 知识库独立成页与任务手动入库（执行文档）

## 背景

当前知识库挂载在【网页资料整理】的“任务列表 / 我的知识库”Tab 内，入口只在 `?type=WEB_DIGEST` 页面出现，且自动归档与手动入库混在一起。目标是把知识库拆成与【网页资料整理】同级的独立模块，并在任务详情提供显式的“添加到知识库”出口。

## 范围

1. 新增独立页面 `/ui/kb.html`，侧栏“知识库”与“网页资料整理”同级。
2. 移除网页资料整理页里的知识库 Tab，页面只保留任务列表。
3. 任务详情（网页整理 / 网页对比）新增“添加到知识库”：选择已有库或新建，默认同时存入本次对话。
4. 知识库页保留并强化上传 PDF / TXT / MD，用于补充资料。
5. 后端 `ingest-task` 支持 `include_chat`，把 `task_messages` 写成 `conversation.md` 一并入库。

## 行为约定

- 自动归档（`KB_AUTO_INGEST=true`）继续保留：任务成功后 `.md/.csv` 进“任务归档”。
- 手动入库是额外动作，不改变自动归档内容；同一 `(kb, task, artifact)` 重复入库覆盖旧文档。
- 对话为空时跳过 `conversation.md`；任务无产物时返回可读提示，不产生空文档。
- 删除任务不删除知识库文档，只标记 `orphaned`，界面显示“来源任务已删除”。
- 知识库问答检索范围是该知识库全部文档，跨任务、跨批次，不受原任务限制。

## 接口变化

`POST /kb/{kb_id}/ingest-task`

```json
{ "task_id": "...", "artifacts": ["article.md", "items.csv"], "include_chat": true }
```

- `include_chat=true` 时额外写入 `artifact_name="conversation.md"`、`kind="conversation"`，标题为“任务标题 · 对话记录”。
- 对话内容按“我 / 助手”拼接，超过 20000 字截断。

## 页面结构

- `frontend/kb.html`：复用工作台外壳（侧栏、顶栏、用户卡），主区为知识库工作区。
- `frontend/kb_page.js`：独立启动，不依赖 `?type=` 与 Tab，负责列表、上传、问答、导出。
- `frontend/kb_ingest.js`：任务详情按钮与入库弹窗。
- `task_list.html` 删除 `#feature-tabs`、`#kb-panel` 与 `kb.js` 引用。

## 验收

- 侧栏进入知识库页，可新建库、上传 PDF/MD、提问、导出。
- 任务详情点“添加到知识库”，选已有库或新建后，产物与对话出现在目标库。
- 重复入库不产生重复文档；删除任务后文档保留并标记来源已删除。
- `python -m unittest discover -s tests -q` 全绿；人工测试补 M46/M47。

## 风险

- 旧链接 `?type=WEB_DIGEST#kb` 不再定位知识库，会落到任务列表；如有需要可加跳转，但当前不做兼容。
- 对话入库会增加文档数量，已在标题和 `kind=conversation` 上区分。
