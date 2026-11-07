# BoxPilot 接口与分工契约

版本：v0.3，2026-11-07。曾雨格负责沙箱、调度、公共 API 和四个模板；陈咏忻负责 SQLite 四表持久化。

## 1. 状态约定

任务状态统一使用大写：

```text
PENDING -> RUNNING -> SUCCESS / FAILED / TIMEOUT / CANCELLED
```

`CANCELLED` 为新增终态。等待中的任务可立即取消；运行中的任务在“步骤之间”检查取消请求，当前步骤执行完毕后落为 `CANCELLED`，不会留下半写产物。每个步骤状态为 `RUNNING`、`SUCCESS` 或 `FAILED`。失败时停止后续步骤，并保留错误码和错误信息。

重试不修改原任务，而是创建新任务：`attempt = 原任务.attempt + 1`，`retry_of = 原任务 ID`。只有 `FAILED`、`TIMEOUT`、`CANCELLED` 可重试。

## 2. 数据库契约

陈咏忻提供 SQLite 数据访问实现，替换 `backend/db.py` 中的 `InMemoryTaskStore`。必须保持现有 `TaskStore` 方法可调用。

### tasks

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT/UUID | 主键 |
| `task_type` | TEXT | `CSV_ANALYSIS`、`WEB_DIGEST`、`REPO_CHECK`、`WEB_ACCEPTANCE` 或开发用 `MOCK` |
| `status` | TEXT | 大写任务状态 |
| `params_json` | TEXT | 任务输入参数 JSON |
| `result_json` | TEXT | 任务结果 JSON |
| `error_code` | TEXT | 失败错误码 |
| `error_message` | TEXT | 失败说明 |
| `created_at` | DATETIME | 创建时间 |
| `started_at` | DATETIME | 开始时间 |
| `finished_at` | DATETIME | 完成时间 |
| `attempt` | INTEGER | 第几次尝试，默认 1 |
| `retry_of` | TEXT | 重试来源任务 ID，可空 |

### steps

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT/UUID | 主键 |
| `task_id` | TEXT | 外键，关联 tasks |
| `step_index` | INTEGER | 从 1 开始 |
| `name` | TEXT | 步骤名称 |
| `status` | TEXT | RUNNING/SUCCESS/FAILED |
| `output` | TEXT | 输出摘要，最多 4000 字符 |
| `error_code` | TEXT | 失败错误码 |
| `error_message` | TEXT | 失败说明 |
| `duration_ms` | INTEGER | 耗时 |
| `started_at` | DATETIME | 开始时间 |
| `finished_at` | DATETIME | 完成时间 |

### artifacts

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT/UUID | 主键 |
| `task_id` | TEXT | 外键，关联 tasks |
| `filename` | TEXT | 下载文件名 |
| `relative_path` | TEXT | 相对 `ARTIFACT_ROOT/<task_id>` |
| `content_type` | TEXT | MIME 类型 |
| `size_bytes` | INTEGER | 文件大小，必须大于 0 |
| `created_at` | DATETIME | 创建时间 |

### task_events（可选）

用于把关键事件持久化到数据库，便于页面展示时间线；不替代 `logs/boxpilot.jsonl`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER | 自增主键 |
| `task_id` | TEXT | 外键，关联 tasks |
| `event` | TEXT | 事件名，与结构化日志一致 |
| `level` | TEXT | INFO/WARNING/ERROR |
| `payload_json` | TEXT | 事件附加字段 JSON |
| `created_at` | DATETIME | 发生时间 |

## 3. TaskStore 方法契约

```python
create_task(task_type, params) -> TaskRecord
get_task(task_id) -> TaskRecord | None
list_tasks(query="", statuses=(), task_type=None, page=1, page_size=20) -> tuple[list[TaskRecord], int]
count_tasks() -> dict[str, int]
start_task(task_id) -> TaskRecord
finish_task(task_id, status, result, error_code, error_message) -> TaskRecord
add_step(task_id, step_index, name) -> TaskStep
finish_step(step_id, status, output, error_code, error_message) -> TaskStep
add_artifact(artifact) -> Artifact
get_artifact(artifact_id) -> tuple[Artifact, task_id] | None
delete_task(task_id) -> bool
purge_tasks(statuses, older_than) -> list[str]
```

`list_tasks` 按 `created_at` 倒序返回 `(当页记录, 满足筛选的总数)`；`count_tasks` 返回 `{"total": n, "PENDING": n, ...}`，统计全量、不受筛选影响。`purge_tasks` 返回被清理的任务 ID，调用方负责删除对应产物目录。调度器只依赖这些方法，不直接执行 SQL。数据库实现必须处理事务和线程安全；SQLite 建议启用 WAL。

## 4. 公共 API

### POST /tasks

普通任务使用 JSON：

```json
{
  "task_type": "WEB_ACCEPTANCE",
  "params": {
    "url": "https://example.com",
    "expected_title": "Example Domain"
  }
}
```

返回 `202` 和任务记录。未注册模板返回 `422 TEMPLATE_NOT_AVAILABLE`。

### POST /tasks/csv

使用 `multipart/form-data` 接收 `file`。当前限制：

- 扩展名必须为 `.csv`。
- 文件不能为空，最大 5 MB。
- 文件保存为任务目录中的 `input.csv`。
- 创建任务后调用统一调度器，不另建执行流程。
- 产物为 `summary.json` 和 `report.txt`。

通过普通 JSON 接口提交 `CSV_ANALYSIS` 会返回 `422 CSV_UPLOAD_REQUIRED`。

### 查询与清理

| 方法 | 路径 | 返回 |
|---|---|---|
| `GET` | `/tasks?q=&status=&task_type=&page=1&page_size=20` | `{items,total,page,page_size,pages,counts}` |
| `GET` | `/tasks/{task_id}` | 任务、步骤和产物 |
| `POST` | `/tasks/{task_id}/cancel` | 取消任务，202 |
| `POST` | `/tasks/{task_id}/retry` | 创建重试任务，202 |
| `POST` | `/tasks/batch-delete` | `{task_ids}` → `{deleted,skipped}` |
| `POST` | `/tasks/cleanup` | `{statuses,older_than_days}` → `{deleted,skipped}` |
| `GET` | `/tasks/{task_id}/artifacts` | 产物清单 |
| `GET` | `/artifacts/{artifact_id}/download` | 文件流，`?inline=1` 内联显示 |
| `GET` | `/artifacts/{artifact_id}/preview` | `{kind,text,truncated,url,...}` |
| `GET` | `/health` | 应用与沙箱健康状态 |

说明：

- `q` 匹配任务 ID、类型、参数、错误信息和步骤名；`status` 支持逗号分隔多值，未知状态返回 `422 VALIDATION_ERROR`；`page_size` 上限 100。
- 取消已结束任务返回 `409 TASK_NOT_CANCELLABLE`；重试不可重试状态返回 `409 TASK_NOT_RETRYABLE`；CSV 重试缺少 `input.csv` 返回 `409 INPUT_NOT_FOUND`。
- `batch-delete` 与 `cleanup` 跳过或拒绝活动任务（`PENDING`、`RUNNING`），删除记录时同步删除产物目录。
- 预览文本上限 `PREVIEW_TEXT_LIMIT`（默认 200000 字符）；图片返回带 `?inline=1` 的下载地址；其他类型提示下载。

错误体统一为：

```json
{
  "detail": {
    "code": "TASK_NOT_FOUND",
    "message": "task not found: ..."
  }
}
```

## 5. SandboxAdapter 契约

所有沙箱调用必须经过 `backend/sandbox_client.py`，模板中不得直接 import `agent_sandbox`。

```python
health() -> dict
ensure_task_workspace(task_id) -> dict
exec_shell(command, task_id, timeout_s=None, check_exit=True) -> dict
write_text_file(task_id, relative_path, content) -> dict
read_text_file(task_id, relative_path) -> dict
upload_file(task_id, local_path, relative_path) -> dict
download_file(task_id, relative_path, local_path) -> dict
navigate(url, timeout_s=None) -> dict
get_page_text() -> dict
get_page_markdown() -> dict
take_screenshot(save_path, full_page=False) -> dict
```

`get_page_markdown` 复用沙箱自带 Readability，返回 `{"title": ..., "markdown": ..., "extraction": "readability"}`；适配层负责剥掉 `"Title: ...\nContent:\n"` 前缀。模板在正文为空时应回退 `get_page_text`，并把 `extraction` 记为 `text`。

成功结构：

```json
{"success": true, "data": {}, "error": null}
```

失败结构：

```json
{"success": false, "data": null, "error": {"code": "TIMEOUT", "message": "..."}}
```

固定错误码：`VALIDATION_ERROR`、`SANDBOX_UNAVAILABLE`、`TIMEOUT`、`COMMAND_BLOCKED`、`COMMAND_FAILED`、`FILE_NOT_FOUND`、`ARTIFACT_EMPTY`、`INTERNAL_ERROR`。

## 6. 模板契约

模板实现 `backend/templates/base.py` 的 `TaskTemplate`：

```python
class TaskTemplate(Protocol):
    name: str
    steps: tuple[StepDefinition, ...]
```

每个步骤接收 `TaskContext(task_id, params, artifact_store, state)`，返回 `StepOutcome(output, artifacts)`。`StepOutcome` 还带可选 `result` 字典，调度器会把它合并进任务 `result.summary`，供前端展示“预期/实际标题”“行数列数”“正文长度”等业务结论。`state` 仅保存本次模板执行的临时中间结果。

正式模板接入后注册：

```python
TaskType.CSV_ANALYSIS: CsvAnalysisTemplate(...)
TaskType.WEB_DIGEST: WebDigestTemplate(...)
TaskType.REPO_CHECK: RepoCheckTemplate(...)
TaskType.WEB_ACCEPTANCE: WebAcceptanceTemplate(...)
```

当前已实现并注册：

- `CSV_ANALYSIS`：必须通过 `POST /tasks/csv` 上传，生成 `summary.json` 和 `report.txt`。
- `WEB_DIGEST`：参数 `url`，用 Readability 提取正文、截整页图，生成 `article.md`、`page.txt`、`screenshot.png` 和 `web-digest.json`；`summary` 含 `extraction`、`markdown_length`、`full_page`、`screenshot_bytes`。
- `REPO_CHECK`：参数 `repo_url`，必须是 `TRUSTED_GIT_HOSTS` 内的 HTTPS 仓库；离线演示使用 `demo://python`，生成 `check-report.json` 和 `test.log`。
- `WEB_ACCEPTANCE`：参数 `url`、`expected_title`，生成 `acceptance.json` 和 `page.png`。

仓库测试失败和网页标题不匹配都属于有效检查结果，任务状态仍为 `SUCCESS`；访问失败、超时或产物缺失才使任务失败。

## 7. 存储与目录

- 本地产物根目录：`artifacts`，可通过 `ARTIFACT_ROOT` 修改。
- 日志文件：`logs/boxpilot.jsonl`，可通过 `LOG_FILE` 修改，5 MB 轮转、保留 3 份。
- 每个任务目录：`artifacts/<task_id>`。
- 沙箱任务目录：`/home/gem/boxpilot/<task_id>`。
- 沙箱内运行用户为 `gem`，没有 `/workspace` 写权限。
- 数据库只保存相对路径，不保存宿主或沙箱绝对路径。

## 8. 结构化日志契约

每行一个 JSON，字段固定：

```json
{
  "ts": "2026-11-07T19:48:22.123456+00:00",
  "level": "INFO",
  "logger": "boxpilot.scheduler",
  "event": "step_succeeded",
  "task_id": "...",
  "task_type": "WEB_DIGEST",
  "step": "截取整页截图",
  "duration_ms": 764
}
```

事件名：`task_created`、`task_started`、`step_started`、`step_succeeded`、`step_failed`、`task_succeeded`、`task_crashed`、`task_cancelled`、`task_retried`。错误事件额外带 `error_code` 和 `error_message`。

## 9. 当前待陈咏忻交付

1. SQLite 版 `TaskStore`，保持第 3 节方法签名。
2. `scripts/init_db.py` 建表（tasks、steps、artifacts、可选 task_events）和重复执行测试。
3. 将内存任务、步骤和产物记录迁移到 SQLite，保证服务重启后仍可查询。
