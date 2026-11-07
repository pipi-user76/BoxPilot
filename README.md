# BoxPilot

BoxPilot 是基于 AIO Sandbox 的轻量任务执行工作台。后端接收任务、按固定步骤调用沙箱并记录状态、步骤与产物，最终通过 HTTP 提供查询和下载。

## 当前进度

更新时间：2026-11-07。当前状态为“四个模板可运行，具备取消、重试、结构化日志、搜索分页、批量清理和产物预览”。

| 模块 | 状态 | 当前实现 |
|---|---|---|
| FastAPI 与任务 API | 已完成 | 创建、搜索分页列表、详情、取消、重试、批量删除、清理、产物预览与下载 |
| AIO Sandbox 适配 | 已完成 | Shell、文件、浏览器、Readability 正文提取、整页截图、上传和下载 |
| 任务调度 | 已完成 | 固定步骤、状态流转、失败停止、串行执行锁、步骤边界取消、失败重试 |
| 结构化日志 | 已完成 | JSON Lines 输出到 stdout 与 `logs/boxpilot.jsonl`（5 MB × 3 轮转） |
| 网页资料整理 `WEB_DIGEST` | 已完成 | `article.md`、`page.txt`、整页 `screenshot.png`、`web-digest.json` |
| CSV 分析 `CSV_ANALYSIS` | 已完成 | multipart 上传、标准库分析、`summary.json`、`report.txt` |
| 仓库体检 `REPO_CHECK` | 已完成 | 白名单仓库、固定测试命令、离线 `demo://python` |
| 网页验收 `WEB_ACCEPTANCE` | 已完成 | 标题比对、截图、`acceptance.json` |
| Web 工作台 | 已完成进阶版 | 服务端搜索与分页、批量清理、取消/重试、任务结论、文本与图片预览 |
| SQLite 持久化 | 待接入 | 当前仍使用内存 `TaskStore`，重启后任务记录丢失 |
| 10 至 15 个系统样例 | 进行中 | 已有 42 项自动化测试，尚需形成统一验收表和统计报告 |

当前已注册 `MOCK`、`CSV_ANALYSIS`、`WEB_DIGEST`、`REPO_CHECK` 和 `WEB_ACCEPTANCE`。未知任务类型会返回明确校验错误，不会生成伪成功任务。

最近验证结果：

- 42 项 Python 测试中 41 项执行通过，1 项真实沙箱集成测试默认跳过。
- 真实 `WEB_DIGEST` 使用沙箱 Readability 去噪，产物为 4 个文件，整页截图约 75 KB。
- 真实取消任务在步骤边界落为 `CANCELLED`，失败任务重试生成 `attempt=2` 且 `retry_of` 指向原任务。
- 列表分页、搜索、批量清理、文本预览和图片预览均已通过浏览器联调。
- 当前 `/health` 返回 `ok`。

## 下一步计划

| 优先级 | 时间 | 工作内容 | 完成标准 |
|---|---|---|---|
| P0 | 11/08-11/12 | 陈咏忻接入 SQLite 四表（`tasks`、`steps`、`artifacts`、`task_events`）、`init_db.py` 和持久化 `TaskStore` | 重启后端后任务、步骤和产物仍可查询 |
| P0 | 11/08-11/12 | 用持久化存储跑通四个模板的端到端回归 | 四种任务各至少一次成功并生成非空产物 |
| P1 | 11/13-11/17 | 建立 10 至 15 个统一测试用例和结果登记表 | 记录预期、实际、耗时、成功率及失败原因 |
| P1 | 11/13-11/17 | 日志落盘检索与演示脚本 | 用 JSON 日志还原一次失败任务的完整经过 |
| P2 | 11/18-11/24 | 认证、限流与独立沙箱隔离评估 | 非本机访问时必须携带凭据 |
| P2 | 11/25-12/04 | README、设计文档、演示数据、演示视频和发布检查 | 在干净环境按文档启动并完成现场演示 |

已知限制：公开 GitHub 仓库在当前沙箱网络下可能出现 TLS 握手失败，仓库体检现场演示优先使用 `demo://python`；内存任务存储尚未替换，服务重启后任务与日志外的运行状态丢失。

## 环境要求

- Windows + Docker Desktop，或可访问本机 AIO Sandbox 的等价环境
- Python 3.13 与 `uv`
- AIO Sandbox 1.11.0
- 项目虚拟环境 `.venv`

经实测，AIO Sandbox 容器内运行用户为 `gem`，可写工作区为 `/home/gem`。BoxPilot 使用 `/home/gem/boxpilot/<task_id>` 作为任务目录。

## 启动沙箱

先启动 Docker Desktop，再运行：

```powershell
.\start_sandbox.bat
```

脚本固定镜像版本为 `ghcr.io/agent-infra/sandbox:1.11.0`，只将服务绑定到 `127.0.0.1:8080`。启动后访问 <http://127.0.0.1:8080>。

## 安装与启动后端

```powershell
uv sync
uv run uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

访问：

- 任务工作台：<http://127.0.0.1:8000/ui/task_list.html>
- 创建任务页面：<http://127.0.0.1:8000/ui/create_task.html>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

如果当前终端无法启动项目虚拟环境，可直接使用：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

## 验证沙箱

```powershell
.\.venv\Scripts\python.exe scripts\test_sandbox.py
```

脚本验证任务目录、文件写入、文件读取、Shell、浏览器导航、页面文本和截图。截图保存在 `artifacts/smoke/smoke.png`。任何一项失败时进程返回非零退出码。

## 运行测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

预期结果：`Ran 42 tests ... OK (skipped=1)`。真实沙箱集成测试默认跳过，其余 41 项使用替身沙箱，不需要 Docker。

运行真实沙箱集成测试：

```powershell
$env:BOXPILOT_RUN_SANDBOX_TESTS='1'
.\.venv\Scripts\python.exe -m unittest tests.test_sandbox_integration -v
```

测试临时目录统一放在工作区 `.tmp/`，不使用系统临时目录，以避开 Windows 沙箱对 `tempfile.mkdtemp` 的权限限制。

## API 摘要

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 应用与沙箱状态 |
| `POST` | `/tasks` | 创建普通 JSON 任务 |
| `POST` | `/tasks/csv` | 上传 CSV 并创建分析任务 |
| `GET` | `/tasks` | 任务列表，支持 `q`、`status`、`task_type`、`page`、`page_size` |
| `GET` | `/tasks/{id}` | 任务、步骤和产物详情 |
| `POST` | `/tasks/{id}/cancel` | 取消等待中或运行中的任务 |
| `POST` | `/tasks/{id}/retry` | 以新任务重试失败、超时或已取消的任务 |
| `POST` | `/tasks/batch-delete` | 批量删除已结束任务 |
| `POST` | `/tasks/cleanup` | 按状态和天数清理历史任务 |
| `GET` | `/tasks/{id}/artifacts` | 任务产物清单 |
| `GET` | `/artifacts/{id}/download` | 下载已登记产物，`?inline=1` 供浏览器内联显示 |
| `GET` | `/artifacts/{id}/preview` | 文本截断预览或图片预览地址 |

`GET /tasks` 返回 `{items, total, page, page_size, pages, counts}`；`counts` 始终统计全量任务，不受筛选条件影响。取消运行中的任务在“步骤之间”生效，当前步骤不会被强制中断。

当前可直接创建的任务示例：

```json
{
  "task_type": "MOCK",
  "params": {}
}
```

网页资料整理示例：

```json
{
  "task_type": "WEB_DIGEST",
  "params": {"url": "http://host.docker.internal:8000/ui/task_list.html"}
}
```

CSV 分析使用 `POST /tasks/csv` 的 multipart 表单上传，字段名为 `file`，只接受不超过 5 MB 的 `.csv` 文件。

完整字段与团队对接约定见 [docs/interface_contract.md](docs/interface_contract.md)。

人工操作步骤见 [docs/manual_testing.md](docs/manual_testing.md)。

当前阶段进展见 [docs/progress_report_2026-11-07.md](docs/progress_report_2026-11-07.md)，纯文本汇报见 [docs/progress_report_2026-11-07.txt](docs/progress_report_2026-11-07.txt)，上一阶段见 [docs/progress_report_2026-11-06.md](docs/progress_report_2026-11-06.md)。

功能评估与进阶规划见 [docs/functional_assessment_2026-11-06.md](docs/functional_assessment_2026-11-06.md)。

## 日志与排查

调度器与接口按 JSON Lines 写日志，默认输出 `logs/boxpilot.jsonl`，单文件 5 MB、保留 3 份。查看最近事件：

```powershell
Get-Content .\logs\boxpilot.jsonl -Tail 20
```

按任务过滤：

```powershell
Select-String -Path .\logs\boxpilot.jsonl -Pattern '<task_id>'
```

每条记录含 `ts`、`level`、`logger`、`event`；任务事件额外带 `task_id`、`task_type`、`step`、`duration_ms`，失败事件还带 `error_code`、`error_message`。事件清单见 [docs/interface_contract.md](docs/interface_contract.md) 第 8 节。

## 目录说明

```text
backend/
  main.py            FastAPI 应用和公共路由装配
  db.py              SQLite 连接、TaskStore 与临时内存实现
  scheduler.py       状态机、固定步骤调度、取消与重试
  logging_config.py  JSON Lines 结构化日志
  sandbox_client.py  Shell/File/Browser 统一调用
  routes_tasks.py    任务查询、取消/重试、清理与预览接口
  routes_csv.py      CSV multipart 上传接口
  models.py          Pydantic 数据契约
  storage.py         本地任务产物存储
  templates/         四个任务模板
frontend/
  create_task.*      创建任务页面
  task_list.*        任务列表、搜索分页与产物预览
samples/             测试输入与样例
artifacts/           程序运行产物，不提交真实数据
logs/                结构化日志，不提交
scripts/
  init_db.py         四表初始化入口
  test_sandbox.py    真实沙箱冒烟测试
tests/               单元测试、进阶用例与可选沙箱集成测试
docs/                接口契约与验收记录
```

## 安全边界

- 沙箱端口只绑定本机回环地址。
- Shell 命令经过危险模式检查，并限制在任务工作目录内执行。
- 任务 ID、文件名和相对路径均做路径逃逸校验。
- 下载只允许访问数据库/任务记录中已登记的产物。
- 黑名单不是完整隔离。代码体检只应运行可信公开仓库和预设测试命令。

## 已知限制

- 当前任务存储为内存实现，服务重启后任务记录丢失；等待陈咏忻接入 SQLite 四表。
- 运行中任务的取消在步骤边界生效，正在执行的 Shell 或浏览器动作不会被强制终止。
- 当前公开仓库体检可能受沙箱网络出口影响，离线演示使用 `demo://python`。
