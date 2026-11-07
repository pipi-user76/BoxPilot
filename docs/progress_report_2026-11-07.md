# BoxPilot 进阶优化进展（2026-11-07）

本轮按“取消/重试/日志、正文提取与截图、搜索分页与预览”三条线推进，后端、前端、测试和文档同步更新。

## 1. 任务生命周期

| 能力 | 实现 | 关键约束 |
|---|---|---|
| 取消 | `POST /tasks/{id}/cancel` | 等待中立即 `CANCELLED`；运行中在步骤边界生效 |
| 重试 | `POST /tasks/{id}/retry` | 新建任务，`attempt+1`、`retry_of` 指向原任务；只允许 FAILED/TIMEOUT/CANCELLED |
| 批量删除 | `POST /tasks/batch-delete` | 跳过 PENDING/RUNNING，删除记录同时删除产物目录 |
| 历史清理 | `POST /tasks/cleanup` | 按状态与天数清理；请求含活动状态时返回 422 |

取消采用“步骤边界”而不是强杀线程：共享执行锁下强杀会留下半写产物，且沙箱命令无法可靠回滚。重试复制原任务参数，CSV 任务额外复制 `input.csv`，缺失时返回 `409 INPUT_NOT_FOUND`。

## 2. 结构化日志

- `backend/logging_config.py` 输出 JSON Lines 到 stdout 与 `logs/boxpilot.jsonl`（5 MB × 3）。
- 事件：`task_created`、`task_started`、`step_started`、`step_succeeded`、`step_failed`、`task_succeeded`、`task_crashed`、`task_cancelled`、`task_retried`。
- 根日志器固定 `WARNING`，仅 `boxpilot` 命名空间使用 `LOG_LEVEL`，避免 httpx 请求日志污染。
- 日志目录不可写时只告警，不影响服务启动。

## 3. 网页正文与截图

- `SandboxAdapter.get_page_markdown()` 复用沙箱自带 Readability（`browser_page.get_markdown`），剥掉 `Title:/Content:` 前缀。
- `WEB_DIGEST` 改为 5 步：校验地址 → 打开网页 → 提取正文并去噪 → 整页截图 → 生成报告。
- 产物：`article.md`、`page.txt`、`screenshot.png`（`full_page=true`）、`web-digest.json`。
- Readability 返回空正文时回退 `get_page_text()`，`extraction` 记为 `text`。
- 实测 example.com：Markdown 958 字符、纯文本 917 字符、整页截图 75 KB。

## 4. 列表与产物

- `GET /tasks` 改为服务端分页与搜索：`q`、`status`（逗号分隔）、`task_type`、`page`、`page_size`；返回 `{items,total,page,page_size,pages,counts}`。
- `counts` 始终统计全量任务，不受筛选影响；前端概览数字因此保持稳定。
- 前端新增复选框选中、批量删除、清理历史、搜索防抖（300 ms）、每页条数切换和空页回退。
- 产物新增 `GET /artifacts/{id}/preview`：文本按 200000 字符截断、图片返回内联地址、其他类型提示下载；下载支持 `?inline=1`。
- 预览使用原生 `<dialog>`，`Esc` 可关闭。

## 5. 验证结果

- 自动化测试：`Ran 42 tests ... OK (skipped=1)`，新增 13 项进阶用例。
- 真实联调：累计创建 15 个任务，覆盖 SUCCESS/FAILED/CANCELLED；最终保留 8 个演示任务（5 成功、2 失败、1 取消）。取消、重试、分页、搜索、批量删除、文本预览、图片预览均通过。
- 浏览器截图：列表、详情、产物区和预览弹窗已逐屏检查，1280 px 无横向溢出。

## 6. 已知限制

- 任务存储仍是内存实现，服务重启后任务丢失；SQLite 四表由陈咏忻接入。
- 运行中取消不会打断当前 Shell/浏览器动作。
- 预览只做截断，不做语法高亮或 Markdown 渲染。
- 日志尚无检索接口，排查依赖文件或 stdout。

## 7. 下一步

1. SQLite 四表持久化（`tasks`、`steps`、`artifacts`、`task_events`，`tasks` 增加 `attempt`、`retry_of`）与四模板回归。
2. 统一 10–15 个验收用例并登记耗时与失败原因。
3. 认证、限流与独立沙箱隔离评估。
4. 演示数据、演示视频与发布检查。
