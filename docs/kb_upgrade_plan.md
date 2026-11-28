# 知识库升级计划（RAG 与问答前端）

更新时间：2026-11-28

## 1. 背景与目标

当前知识库已能上传文档、跨任务检索和回答，但检索只用向量、回答是整段返回、前端更像文档列表而不是问答产品。本轮升级三个目标：

1. 检索增强：向量 + 关键词混合召回，支持查询改写、范围过滤与引用定位。
2. 问答前端：对标通用问答系统，流式输出、来源面板、会话管理、文档筛选。
3. 可运维：重建索引脚本、数据迁移、测试与文档。

## 2. 现状

- 存储：SQLite `kb_documents` / `kb_messages` / `kb_conversations`，`user_version=9`；`kb_chunks` 保存切片与标题路径。
- 检索：Chroma 向量 + SQLite FTS5（不可用时回退 LIKE 词频），RRF 融合（k=60）。
- 回答：SSE 流式；引用 `[n]` 标记 `used`；支持 `document_ids` / `kinds` 过滤。
- 前端：会话侧栏、流式气泡、停止/重生成/复制、来源折叠、文档抽屉（搜索/类型筛选/勾选删除）。
- 未做：重排模型（`KB_RERANK` 默认关闭）、回答反馈落库、引用点击跳转原文。

## 3. 架构

```
上传/任务入库 → 切片(chunk_sections 600/80, 带标题路径) → 向量(Chroma) + 文本(SQLite FTS5)
提问 → 查询改写(有历史时) → 向量 top20 + 关键词 top20 → RRF → 上下文+历史 → LLM 流式
     → SSE: citations → delta… → done(落库 kb_messages / kb_conversations)
```

## 4. 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/kb/{id}/ask` | 非流式回答，支持 `top_k`、`min_score`、`document_ids`、`kinds` |
| POST | `/kb/{id}/ask/stream` | SSE：`citations`、`delta`、`done`、`error` |
| GET | `/kb/{id}/conversations` | 会话列表 |
| PATCH | `/kb/{id}/conversations/{cid}` | 重命名 |
| DELETE | `/kb/{id}/conversations/{cid}` | 删除会话与消息 |
| POST | `/kb/{id}/ingest-task` | 任务会话入库（`include_chat`） |
| POST | `/kb/{id}/upload` | 上传 PDF/TXT/MD |

## 5. 数据与迁移

- `user_version 8 → 9`：新增 `kb_chunks`（含 heading）、`kb_conversations`；FTS5 表懒创建，失败自动回退。
- 重建索引：`python -m scripts.reindex_kb`（可加 `--kb <id>`）。
- 兼容：旧文档无 chunk 时按需重建；旧会话无标题时用首问补齐。

## 6. 验收

- 检索：同一问题在纯向量与混合检索下对比，关键词命中能进入上下文。
- 流式：首字延迟 < 3s，`停止` 能中断，结束后消息与引用落库。
- 会话：重命名/删除后刷新仍生效；删除只影响该会话。
- 引用：回答里的 `[n]` 与来源列表一一对应，未使用的来源标灰。
- 回归：全量单元测试通过，人工测试 M49–M51 记录完整。

## 7. 风险与后续

- FTS5 对中文分词有限，已用 LIKE 回退；后续可接 jieba 或独立搜索服务。
- 查询改写会增加一次模型调用，可用 `KB_REWRITE=false` 关闭。
- 重排默认关闭，等有稳定评测集再开。
- 下一步：引用点击定位原文、回答反馈、按文档/类型批量管理。
