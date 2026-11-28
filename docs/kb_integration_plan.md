# 知识库联动执行方案（待确认）

更新时间：2026-11-28
范围：把「网页资料整理」的任务产物与对话沉淀到「我的知识库」，并让知识库反过来服务整理任务的分析与问答。

## 1. 现状

- 知识库表：`knowledge_bases`、`kb_documents`、`kb_messages`（`user_version=5`）；向量在 Chroma `kb_chunks`，嵌入用百炼 `text-embedding-v4`（1024 维）。
- 已有入口：任务详情右上「存入知识库」、知识库页「上传文档」（PDF/TXT/MD）、RAG 问答与相似度阈值。
- 缺口：
  - 只存 `article.md` 一类正文，`items.json/items.csv/source-N.md` 没有入库。
  - 对话（`task_messages`）不会进知识库。
  - 知识库检索只发生在知识库页，整理任务的对话不查知识库。

## 2. 目标与非目标

目标：
1. 任务产物可一键/自动入库，带来源标签与任务关联。
2. 任务对话可沉淀为知识条目（问答对或摘要）。
3. 整理任务与对比任务的对话能检索知识库，回答里给出 `[n]` 引用。
4. 入库、检索、删除都有归属校验（沿用 `owner_id`）。

非目标（本期不做）：
- 跨用户共享知识库。
- 复杂文档解析（表格 OCR、扫描件）。
- 向量库迁移（继续用 Chroma 本地目录）。

## 3. 分步执行

### P0 产物入库（约 1 天）

1. **数据模型**（`backend/sqlite_store.py`，`SCHEMA_VERSION=8`）
   - `kb_documents` 增加 `task_id TEXT NULL`、`artifact_name TEXT NULL`、`kind TEXT`（`web_digest` / `web_compare` / `items` / `chat`）。
   - 迁移：`ALTER TABLE ... ADD COLUMN`，空值兼容历史文档。
2. **入库服务**（`backend/kb.py`）
   - 新增 `ingest_task_artifacts(task, names: list[str], kb_id, owner_id)`：
     - 按产物类型取文本：`article.md`、`source-N.md`、`items.csv`（转 Markdown 表）、`compare.md`。
     - 切片沿用 800/100，元数据写 `task_id`、`artifact_name`、`kind`。
     - 同一 `task_id + artifact_name` 重复入库时先删旧 chunk 再写。
3. **接口**（`backend/routes_kb.py`）
   - `POST /kb/{kb_id}/ingest-task` body `{task_id, artifacts?: string[]}`，默认全部可读文本产物。
   - `GET /kb/{kb_id}/documents` 返回新增 `task_id/kind` 字段。
4. **前端**
   - 任务详情「存入知识库」弹窗：列出可入库产物，多选，默认全选文本类（不含 png）。
   - 知识库文档卡显示来源标签（任务 ID / 产物名）。

### P1 对话沉淀（约 0.5 天）

1. `POST /tasks/{task_id}/chat/save` body `{kb_id, mode: "qa" | "summary"}`：
   - `qa`：每条 user/assistant 作为一条 doc（或合并为一问一答）。
   - `summary`：调用 LLM 把对话压成 300 字摘要后入库。
2. 前端对话区加「存入知识库」按钮，选目标库与模式。
3. 重复保存按 `task_id + kind=chat + 时间戳` 去重。

### P2 检索联动（约 1 天）

1. `TaskChatService.ask` 增加可选 `kb_ids`：
   - 先按问题检索知识库 top-3（沿用阈值），把片段拼进 system 上下文，标 `[k1]…` 与任务来源 `[1]…` 分开编号。
   - 回答末尾附「知识库引用」列表。
2. 前端对话区加「参考知识库」多选（默认当前用户全部库）。
3. 知识库页新增「从任务提问」入口：选任务 → 进入该任务对话（复用 P2 检索）。

### P3 收尾（约 0.5 天）

- 删除任务时：默认保留知识库文档（加 `orphaned=true` 标记），提供「同步删除」勾选。
- 文档：README 增「知识库联动」段；`docs/interface_contract.md` 增接口；`docs/manual_testing.md` 增 M45–M47。
- 回归：入库/检索/越权/重复入库/删除同步各一条测试。

## 4. 接口与数据结构摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/kb/{kb_id}/ingest-task` | 任务产物入库，返回文档 ID 列表 |
| POST | `/tasks/{task_id}/chat/save` | 对话入库（qa/summary） |
| GET | `/kb/{kb_id}/documents` | 增加 `task_id/kind` |
| POST | `/tasks/{task_id}/chat` | 增加可选 `kb_ids`，回答带知识库引用 |

## 5. 风险与回滚

- 迁移只加列，失败可回滚到 v7（备份 `data/boxpilot.db`）。
- Chroma 写入失败时先回滚 SQL 文档记录，接口返回 503，不产生半条数据。
- 检索联动可能拉高时延：默认只查 top-3、阈值 0.3，可在设置里关。

## 6. 需要确认的点

1. 是否按 P0 → P1 → P2 顺序做，还是一次做到 P2？
2. 任务完成后是否默认自动入库（当前计划默认手动，自动作为开关）？
3. 删除任务时知识库文档保留还是同步删除（当前计划保留并标记）？
