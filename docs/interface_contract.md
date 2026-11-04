# BoxPilot 接口与分工契约

版本：v0.1，2026-11-04。曾雨格负责沙箱、调度和公共 API；陈咏忻负责 SQLite 三表、CSV/网页整理模板和任务创建表单。

## 1. 状态约定

任务状态统一使用大写：

```text
PENDING -> RUNNING -> SUCCESS / FAILED
```

超时可使用 `TIMEOUT`。每个步骤状态为 `RUNNING`、`SUCCESS` 或 `FAILED`。失败时停止后续步骤，并保留错误码和错误信息。

## 2. 数据库契约

陈咏忻提供 SQLite 数据访问实现，替换 `app/services/task_store.py` 中的 `InMemoryTaskStore`。必须保持现有 `TaskStore` 方法可调用。

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

## 3. TaskStore 方法契约

```python
create_task(task_type, params) -> TaskRecord
get_task(task_id) -> TaskRecord | None
list_tasks() -> list[TaskRecord]
start_task(task_id) -> TaskRecord
finish_task(task_id, status, result, error_code, error_message) -> TaskRecord
add_step(task_id, step_index, name) -> TaskStep
finish_step(step_id, status, output, error_code, error_message) -> TaskStep
add_artifact(artifact) -> Artifact
get_artifact(artifact_id) -> tuple[Artifact, task_id] | None
```

调度器只依赖这些方法，不直接执行 SQL。数据库实现必须处理事务和线程安全；SQLite 建议启用 WAL。

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

由陈咏忻实现。使用 `multipart/form-data` 接收 `file`，完成扩展名、大小和内容校验后调用统一任务创建服务，不另建调度器。

### 查询与下载

| 方法 | 路径 | 返回 |
|---|---|---|
| `GET` | `/tasks` | 任务列表 |
| `GET` | `/tasks/{task_id}` | 任务、步骤和产物 |
| `GET` | `/tasks/{task_id}/artifacts` | 产物清单 |
| `GET` | `/artifacts/{artifact_id}/download` | 文件流 |
| `GET` | `/health` | 应用与沙箱健康状态 |

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

所有沙箱调用必须经过 `app/adapters/sandbox.py`，模板中不得直接 import `agent_sandbox`。

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
take_screenshot(save_path, full_page=False) -> dict
```

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

模板实现 `app/executor/templates/base.py` 的 `TaskTemplate`：

```python
class TaskTemplate(Protocol):
    name: str
    steps: tuple[StepDefinition, ...]
```

每个步骤接收 `TaskContext(task_id, params, artifact_store)`，返回 `StepOutcome(output, artifacts)`。模板只组织固定步骤，不自行改变任务状态。

正式模板接入后注册：

```python
TaskType.CSV_ANALYSIS: CsvAnalysisTemplate(...)
TaskType.WEB_DIGEST: WebDigestTemplate(...)
TaskType.REPO_CHECK: RepoCheckTemplate(...)
TaskType.WEB_ACCEPTANCE: WebAcceptanceTemplate(...)
```

## 7. 存储与目录

- 本地产物根目录：`data/artifacts`，可通过 `ARTIFACT_ROOT` 修改。
- 每个任务目录：`data/artifacts/<task_id>`。
- 沙箱任务目录：`/home/gem/boxpilot/<task_id>`。
- 沙箱内运行用户为 `gem`，没有 `/workspace` 写权限。
- 数据库只保存相对路径，不保存宿主或沙箱绝对路径。

## 8. 当前待陈咏忻交付

1. SQLite 版 `TaskStore`，保持第 3 节方法签名。
2. `scripts/init_db.py` 建表和重复执行测试。
3. `POST /tasks/csv` 与 CSV 参数校验。
4. `CSV_ANALYSIS`、`WEB_DIGEST` 两个模板实现。
5. 任务创建表单，可调用当前 JSON 接口和 CSV 上传接口。
