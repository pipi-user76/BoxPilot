# 知识库文档检索范围与侧边栏

## 目标

- 文档勾选只表示“参与检索”，不再承担删除职责。
- 删除单篇文档统一走卡片右侧 `×`。
- 文档区变成可收起的侧边栏，不再用 `×` 关闭。
- 文档标题与任务列表保持一致，不出现 URL 或域名。

## 交互

### 勾选与保存

- 每个知识库在 `localStorage` 保存 `kb.selection.<kbId>`：`{ selected: string[], known: string[] }`；`known` 用来识别“新入库文档”并自动选中。
- 无记录时默认全选当前文档；新入库文档自动加入选择。
- 勾选/取消只修改草稿 `draftSelection`，点击“保存”才写入 `savedSelection` 并生效。
- “取消”把草稿恢复为已保存集合。
- 已保存集合为空时提问会被拦截，并自动打开文档栏提示先勾选并保存。
- 文档栏底部显示“已选择 N / M 篇”，未保存时显示橙色“待保存”。

### 侧边栏

- 宽屏（>1360px）为第三列；窄屏（≤1360px）为覆盖层。
- 顶部“文档 N”按钮和聊天头“检索：全部文档 / 已选 N 篇”chip 都可以打开。
- 收起按钮为 `›`；开关状态保存在 `kb.docsOpen`。
- `#docs` hash 可直接打开文档栏。

### 删除

- 仅卡片右侧 `×` 删除单篇，删除前确认。
- 删除后同步从已保存、草稿、已知集合中移除。

## 标题

`backend/naming.py::display_name` 依次取：

1. `result.summary.label`（模型生成的主题，≤24 字）
2. `result.summary.title`（页面标题，截断 `|` / `—`）
3. 对比任务的来源标题：两站 `A vs B`，多站 `A / B 等 N 站`
4. 无可用信息时只显示类型名

包含 `http://`、`www.` 或域名后缀的候选会被忽略；“未找到”“404”“访问失败”同样过滤。

历史文档可用一次性脚本刷新：

```bash
python -m scripts.sync_document_titles --dry-run
python -m scripts.sync_document_titles
```

## 接口

- `GET /kb/{id}/documents` 返回摘要（`preview`，不含全文）。
- `GET /tasks/{task_id}/kb-documents` 返回任务已入库的知识库。
- `DELETE /kb/{id}/documents/{doc_id}` 删除文档与向量。
- 提问体新增 `document_ids: string[]`；为空会被前端拦截。

## 测试

- `tests/test_naming.py`：模型标题优先、域名标题过滤、404 过滤、无信号只显示类型、页面标题截断。
- `tests/test_kb.py`：摘要、任务入库归属、越权、删除级联。
- 前端手工：新文档默认选中；取消勾选→保存后检索范围变化；取消还原；空集合提问被拦截；侧边栏收起状态刷新保留。
