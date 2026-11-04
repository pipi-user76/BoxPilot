# BoxPilot

BoxPilot 是基于 AIO Sandbox 的轻量任务执行工作台。后端接收任务、按固定步骤调用沙箱并记录状态、步骤与产物，最终通过 HTTP 提供查询和下载。

## 当前进度

当前完成第 1 周曾雨格负责的基础骨架：

- FastAPI 应用、健康检查和任务公共路由
- 统一 `SandboxAdapter`：Shell、文件读写、浏览器导航、页面文本和截图
- 任务状态机与固定步骤调度器
- 独立任务产物目录、产物登记和安全下载
- `MOCK` 模板端到端闭环，用于在真实四个模板接入前验证调度、数据库契约和沙箱
- 命令限制、任务 ID/路径校验、超时和结构化错误码
- 单元测试、真实沙箱冒烟脚本和接口契约

`MOCK` 是开发期模板。`CSV_ANALYSIS`、`WEB_DIGEST`、`REPO_CHECK`、`WEB_ACCEPTANCE` 尚未注册时会返回明确的 `TEMPLATE_NOT_AVAILABLE`，不会生成伪成功任务。

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

运行真实沙箱集成测试：

```powershell
$env:BOXPILOT_RUN_SANDBOX_TESTS='1'
.\.venv\Scripts\python.exe -m unittest tests.test_sandbox_integration -v
```

## API 摘要

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 应用与沙箱状态 |
| `POST` | `/tasks` | 创建普通 JSON 任务 |
| `GET` | `/tasks` | 任务列表 |
| `GET` | `/tasks/{id}` | 任务、步骤和产物详情 |
| `GET` | `/tasks/{id}/artifacts` | 任务产物清单 |
| `GET` | `/artifacts/{id}/download` | 下载已登记产物 |

当前可直接创建的任务示例：

```json
{
  "task_type": "MOCK",
  "params": {}
}
```

完整字段与团队对接约定见 [docs/interface_contract.md](docs/interface_contract.md)。

人工操作步骤见 [docs/manual_testing.md](docs/manual_testing.md)。

## 目录说明

```text
backend/
  main.py            FastAPI 应用和公共路由装配
  db.py              SQLite 连接、TaskStore 与临时内存实现
  scheduler.py       状态机和固定步骤调度
  sandbox_client.py  Shell/File/Browser 统一调用
  routes_tasks.py    任务查询与下载接口
  routes_csv.py      CSV 上传接口占位
  models.py          Pydantic 数据契约
  storage.py         本地任务产物存储
  templates/         四个任务模板
frontend/
  create_task.*      创建任务页面
  task_list.*        任务列表和状态页面
samples/             测试输入与样例
artifacts/           程序运行产物，不提交真实数据
scripts/
  init_db.py         三表初始化入口
  test_sandbox.py    真实沙箱冒烟测试
tests/               单元测试和可选沙箱集成测试
docs/                接口契约与验收记录
```

## 安全边界

- 沙箱端口只绑定本机回环地址。
- Shell 命令经过危险模式检查，并限制在任务工作目录内执行。
- 任务 ID、文件名和相对路径均做路径逃逸校验。
- 下载只允许访问数据库/任务记录中已登记的产物。
- 黑名单不是完整隔离。代码体检只应运行可信公开仓库和预设测试命令。

## 已知限制

- 当前任务存储为内存实现，服务重启后任务记录丢失；等待陈咏忻接入 SQLite 三表。
- 当前只注册 `MOCK` 模板；四个正式模板按计划在后续周接入。
- 当前无前端页面；任务列表和下载接口已可供陈咏忻对接。
