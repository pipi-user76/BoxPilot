# BoxPilot

BoxPilot 是基于 AIO Sandbox 的轻量任务执行工作台。后端接收任务、按固定步骤调用沙箱并记录状态、步骤与产物，最终通过 HTTP 提供查询和下载。

## 快速使用源码包

解压后进入本 README 所在目录，准备 Python 3.13、uv 和 Docker，按下方步骤启动 AIO Sandbox 及后端。数据库自动创建，采用单进程、单 Worker 运行。

`samples/normal.csv` 是包含 99 条商品明细的模拟超市数据，含 8 个品类、3 条重复行、10 个缺失单元格，预期完整度为 99.1%。`baseline.csv` 是三行基础样例，另提供缺失重复样例和空文件。

此包包含源码、静态页面、依赖锁定文件、测试及使用说明。运行环境依赖由 `uv sync --locked` 安装，运行时数据由服务生成。

## 项目定位

浏览器能打开网页，Git 客户端能拉仓库，为什么还需要这个项目？区别在于“看一次”和“反复按同一标准检查并留下记录”。

适合用 BoxPilot 的场景：

- 同一套检查要周期性重复，例如每次发版前确认页面标题、截图和仓库测试结果。
- 多人协作时需要统一口径，避免每个人看到的结果和结论不一致。
- 检查结论需要留档，产物包含截图、报告和原始日志，可回溯到具体任务。
- 需要批量或无人值守执行，例如一次提交多个页面验收。

不适合的场景：

- 只想临时看一眼网页或仓库，直接用浏览器和 Git 客户端更快。
- 需要人工判断的探索性工作，固定步骤反而限制发挥。

## 当前进度

更新时间：2026-11-27。工作台按功能拆成独立页面：侧栏进入“网页资料整理 / CSV 数据分析 / 代码仓库体检 / 网页应用验收”，每个页面只列本类任务，“创建任务”用悬浮框按类型收集参数，提交后直接回到对应列表。集成版默认接入 SQLite 三表与用户表，四个模板沿用统一 API、调度器和沙箱适配器；工作台支持批量订阅、全选下载与规范命名；任务按用户隔离，服务器部署脚本与 systemd/Caddy 模板已补齐。

| 模块 | 状态 | 当前实现 |
|---|---|---|
| FastAPI 与任务 API | 已完成 | 创建、搜索分页列表、详情、取消、重试、批量删除、清理、产物预览与下载 |
| AIO Sandbox 适配 | 已完成 | Shell、文件、浏览器、Readability 正文提取、整页截图、上传和下载 |
| 任务调度 | 已完成 | 固定步骤、状态流转、失败停止、串行执行锁、步骤边界取消、失败重试、步骤与任务超时兜底 |
| 结构化日志 | 已完成 | JSON Lines 输出到 stdout 与 `logs/boxpilot.jsonl`（5 MB × 3 轮转） |
| 网页资料整理 `WEB_DIGEST` | 已完成 | `article.md` 去噪正文、整页 `screenshot.png`、`web-digest.json`（词数、阅读时长、标题数、链接数、图片数） |
| CSV 分析 `CSV_ANALYSIS` | 已完成 | multipart 上传、标准库分析、`summary.json`（列类型、缺失、唯一值、高频值）、中文 `report.md` |
| 仓库体检 `REPO_CHECK` | 已完成 | 白名单仓库、固定测试命令、离线 `demo://python`、失败重试、中文错误说明与失败报告；默认用 uv 在任务目录建独立虚拟环境，按 `requires-python` 选或现场补装 3.13/3.12/3.11/3.10，装完依赖再补 pytest 并尽量把项目装成可编辑包 |
| 网页验收 `WEB_ACCEPTANCE` | 已完成 | 标题比对、整页截图、`acceptance.json`（预期/实际标题、状态码、加载耗时、截图大小、检查时间） |
| Web 工作台 | 已完成进阶版 | 四个功能各自成页，创建任务使用悬浮框；侧栏 + 列表 + 详情三段式布局；状态页签、服务端搜索分页、批量订阅、全选与批量下载、一键下载全部、取消/重试、任务结论、文本与图片预览 |
| 下载命名 | 已完成 | 任务与压缩包统一为 `类型_目标_时间_短ID`，批量包按任务名分目录 |
| SQLite 持久化 | 已接入 | 应用默认使用 SQLite 存储任务、步骤和产物索引 |
| 账号与数据隔离 | 已完成 | 用户名/密码登录、scrypt 哈希、签名 Cookie，任务按 `owner_id` 隔离，管理员可查看全部 |
| 服务器部署 | 已完成 | `deploy/bootstrap.sh` 一键安装，systemd 单进程托管，Caddy 反代与 HTTPS 模板 |
| 10 至 15 个系统样例 | 进行中 | 已有 86 项自动化测试，尚需形成统一验收表和统计报告 |

当前已注册 `MOCK`、`CSV_ANALYSIS`、`WEB_DIGEST`、`REPO_CHECK` 和 `WEB_ACCEPTANCE`。未知任务类型会返回明确校验错误，不会生成伪成功任务。

最近验证结果：

- 86 项 Python 测试中，1 项真实沙箱集成测试默认跳过。
- 登录链路已实测：未登录访问工作台 303 跳转登录页；管理员 alice 可见全部 8 条任务，新账号 bob 登录后列表为 0，直接访问 alice 的任务详情与打包接口均返回 404。
- 真实 `WEB_DIGEST` 使用沙箱 Readability 去噪并识别列表条目，`books.toscrape.com` 抽出 20 条、268 词，产出 5 个文件（正文、条目 JSON、条目 CSV、整页截图、报告）。
- 批量订阅一次提交多个地址，共享同一批次号；批次可在列表里筛选，详情里可一键重跑。
- 任务与压缩包按 `类型_目标_时间_短ID` 命名，单个压缩包内的文件也带同一前缀，下载后不会互相覆盖。
- 真实仓库体检默认建 `.venv` 并现场补装 Python 3.13.6，装完依赖后跑通本项目 44 项测试（1 项跳过）。
- 真实 CSV 分析生成 `summary.json` 与中文 `report.md`，样例完整度 91.7%。
- 真实取消任务在步骤边界落为 `CANCELLED`，失败任务重试生成 `attempt=2` 且 `retry_of` 指向原任务。
- 列表分页、搜索、批量清理、文本预览和图片预览均已通过浏览器联调。
- 健康检查反映运行时状态；服务与 AIO Sandbox 启动后检查 `/health`。

## 产物清单

| 模板 | 产物 | 说明 |
|---|---|---|
| `WEB_DIGEST` | `article.md`、`items.json`、`items.csv`、`screenshot.png`、`web-digest.json` | 列表页抽条目成表，文章页只留正文、截图和报告；报告含词数、阅读时长、条目数与爬虫元数据 |
| `CSV_ANALYSIS` | `summary.json`、`report.md` | JSON 含行列数、重复行、缺失单元格、完整度、每列类型/唯一值/示例/高频值；Markdown 为中文列概览表 |
| `REPO_CHECK` | `check-report.md`、`check-report.json`、`test.log` | 成功时的最小集合；克隆重试过才有 `clone.log`，安装失败才有 `install.log`；`install-result.json` 只留在沙箱 |
| `WEB_ACCEPTANCE` | `acceptance.json`、`page.png` | JSON 含预期/实际标题、是否通过、状态码、加载耗时、截图大小、检查时间 |

失败任务同样会留下能解释原因的产物，例如仓库克隆失败时的 `clone-failure.json`。

下载时的命名规则：`web-digest_books.toscrape.com_20261123-2204_e24e56ee.zip`。单任务包内是 `web-digest_..._article.md` 这类带前缀的文件；批量包按任务名建目录，目录内保留原文件名。

## 下一步计划

| 优先级 | 时间 | 工作内容 | 完成标准 |
|---|---|---|---|
| 已完成 | SQLite 三表 | 默认持久化 `TaskStore` 已接入 | 重启后任务、步骤和产物仍可查询 |
| 已完成 | 2026-11-16 | 四模板持久化真实沙箱回归 | 四类任务成功，重启后详情与产物内容一致 |
| P1 | 11/13-11/17 | 建立 10 至 15 个统一测试用例和结果登记表 | 记录预期、实际、耗时、成功率及失败原因 |
| P1 | 11/13-11/17 | 日志落盘检索与演示脚本 | 用 JSON 日志还原一次失败任务的完整经过 |
| 已完成 | 2026-11-26 | 登录、数据隔离与服务器部署 | 未登录跳转登录页；任务按 `owner_id` 隔离；`deploy/bootstrap.sh` 可在 Ubuntu 上一次性装好 |
| P2 | 11/25-12/04 | README、设计文档、演示数据、演示视频和发布检查 | 在干净环境按文档启动并完成现场演示 |

已知限制：沙箱网络受限时，公开仓库克隆可能超时或被拒绝；系统会自动重试一次，并给出中文原因和替代方案，现场演示优先使用 `demo://python`。

## 环境要求

- Windows + Docker Desktop，或 Ubuntu 22.04/24.04（服务器部署见 [docs/deployment.md](docs/deployment.md)）
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

应用启动时自动初始化 SQLite。已有本地容器可通过 `SANDBOX_URL` 指定，请参阅 [SQLite 集成与验收](docs/sqlite_integration.md)。

```powershell
uv sync
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

访问：

- 任务工作台：<http://127.0.0.1:8000/ui/task_list.html>
- 单个功能页：`/ui/task_list.html?type=WEB_DIGEST`（可换成 `CSV_ANALYSIS`、`REPO_CHECK`、`WEB_ACCEPTANCE`）
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

如果当前终端无法启动项目虚拟环境，可直接使用：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
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

预期结果：`Ran 86 tests ... OK (skipped=1)`。真实沙箱集成测试默认跳过，其余 85 项测试可在未启动 Docker 时执行。

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
| `POST` | `/tasks/batch` | 批量订阅：一次提交多个地址，共享批次号与批次名 |
| `POST` | `/tasks/csv` | 上传 CSV 并创建分析任务 |
| `GET` | `/tasks` | 任务列表，支持 `q`、`status`、`task_type`、`page`、`page_size` |
| `GET` | `/tasks/{id}` | 任务、步骤和产物详情 |
| `POST` | `/tasks/{id}/cancel` | 取消等待中或运行中的任务 |
| `POST` | `/tasks/{id}/retry` | 以新任务重试失败、超时或已取消的任务 |
| `POST` | `/tasks/batch-delete` | 批量删除已结束任务 |
| `POST` | `/tasks/cleanup` | 按状态和天数清理历史任务 |
| `GET` | `/tasks/{id}/artifacts` | 任务产物清单 |
| `GET` | `/tasks/{id}/artifacts/archive` | 一键打包下载，文件名形如 `web-digest_quotes.toscrape.com_20261123-2204_d999048a.zip` |
| `GET` | `/tasks/artifacts/archive?task_ids=a,b` | 批量打包下载，最多 50 个任务，按任务规范名分目录 |
| `GET` | `/artifacts/{id}/download` | 下载已登记产物，`?inline=1` 供浏览器内联显示 |
| `GET` | `/artifacts/{id}/preview` | 文本截断预览或图片预览地址 |

`GET /tasks` 返回 `{items, total, page, page_size, pages, counts}`；`counts` 始终统计全量任务，不受筛选条件影响。取消运行中的任务在“步骤之间”生效，当前步骤不会被强制中断。

`POST /tasks/batch` 接收 `{task_type, targets, batch_name}`，一行一个地址，单次最多 20 个。同一批任务共享 `_batch_id`，按批次号搜索即可筛出整批；详情里的“重跑本批”会按原地址重建一批任务。

下载命名统一为 `类型_目标_时间_短ID`：单任务压缩包内每个文件都带这个前缀，批量包按该名字分目录，同名产物不会互相覆盖。

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

仓库体检默认就会准备测试环境：克隆后按仓库的 `requires-python` 挑解释器，用 uv 在任务目录建 `.venv`，安装声明依赖、补 pytest，并尝试以可编辑方式安装项目本身。

```json
{
  "task_type": "REPO_CHECK",
  "params": {"repo_url": "https://github.com/pypa/sampleproject"}
}
```

只想做浅层检查、跳过下载和安装时，显式传 `"install_deps": false`。安装结果写 `install-result.json`，安装失败时额外保留 `install.log`，结论写进 Markdown 报告。

沙箱镜像自带 3.10 和 3.12。仓库要求 3.13 时，会先用 `uv python install` 现场补装；解释器下载默认走 npmmirror 镜像（`UV_PYTHON_INSTALL_MIRROR`），实测三秒内装好 3.13.6。补装失败才会降级：改用现有最高版本试跑，并在报告里注明结果仅供参考；要严格校验，传 `"allow_version_mismatch": false`。

沙箱直连 PyPI 经常超时，默认已改用阿里云镜像（`UV_INDEX_URL`）；要恢复官方源，把它显式设为空字符串。镜像源只影响下载，不改变测试命令。

完整字段与团队对接约定见 [docs/interface_contract.md](docs/interface_contract.md)。

人工操作步骤见 [docs/manual_testing.md](docs/manual_testing.md)。

服务器部署见 [docs/deployment.md](docs/deployment.md)。

安全边界、信任模型与已知缺口见 [docs/security.md](docs/security.md)，命令注入、SSRF、幂等限流与超时兜底的改进方案见 [docs/security_hardening_plan.md](docs/security_hardening_plan.md)。

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
  db.py              TaskStore 协议与内存测试实现
  sqlite_store.py    SQLite 三表及持久化实现
  scheduler.py       状态机、固定步骤调度、取消与重试
  logging_config.py  JSON Lines 结构化日志
  sandbox_client.py  Shell/File/Browser 统一调用
  routes_tasks.py    任务查询、取消/重试、清理与预览接口
  routes_csv.py      CSV multipart 上传接口
  models.py          Pydantic 数据契约
  storage.py         本地任务产物存储
  templates/         四个任务模板
frontend/
  login.*            登录与首次创建管理员
  users.*            管理员账号管理页
  create_task.*      旧创建页，已改为跳转到功能页
  task_list.*        按功能分页的任务列表、创建弹窗与产物预览
samples/             测试输入与样例
artifacts/           程序运行产物，不提交真实数据
logs/                结构化日志，不提交
scripts/
  init_db.py         三表初始化入口（用 python -m scripts.init_db 运行）
  create_user.py     创建管理员或普通账号
  test_sandbox.py    真实沙箱冒烟测试
tests/               单元测试、进阶用例与可选沙箱集成测试
docs/                接口契约与验收记录
```

## 账号与登录

- 每个任务记录归属用户（`owner_id`）。普通用户只能看到、取消、重试、下载自己的任务；管理员可以查看全部任务。
- 登录使用 HttpOnly + SameSite=Lax 的签名 Cookie，默认 7 天；密码用标准库 `scrypt` 加盐哈希，数据库里没有明文。
- 首次部署打开 `/ui/login.html`，点“创建管理员”即可；之后管理员从侧栏“账号管理”进入 `/ui/users.html` 创建普通用户或管理员，也可以运行 `python -m scripts.create_user <用户名> <密码> [--admin]`。
- 账号管理页与四个功能页共用同一套外壳，管理员可以在行内重置密码或删除账号；不能删除当前登录账号，也不能删掉最后一个管理员。重置密码后旧密码立即失效，删除账号不会连带删除其历史任务。
- 生产环境必须设置 `SESSION_SECRET`，否则每次重启都会让所有会话失效；开启 HTTPS 后再把 `SESSION_COOKIE_SECURE` 设为 `true`。
- 数据库从 `user_version=1` 自动迁移到 2：新增 `users` 表与 `tasks.owner_id`。旧任务没有归属，只有管理员可见。

## 安全边界

- 沙箱端口只绑定本机回环地址。
- 未登录时页面跳转到登录页，任务与产物接口返回 401；任务级接口统一校验 `owner_id`，越权访问按 404 处理。
- Shell 命令经过危险模式检查，并限制在任务工作目录内执行；用户参数统一经 `quote_arg` 转义并拒绝控制字符。
- 网页任务会解析域名并拒绝回环、内网、链路本地和云元数据地址，端口只允许 80/443。本机代理把域名解析成 `198.18.0.0/15` 的 fake-IP 时，可通过 `TRUST_PROXY_DNS=true` 放行；云服务器建议保持 `false`。
- 相同任务参数在 300 秒内只创建一次；`POST /tasks` 与 `POST /tasks/csv` 按来源 IP 限流，超限返回 429。
- 任务 ID、文件名和相对路径均做路径逃逸校验。
- 下载只允许访问数据库/任务记录中已登记的产物。
- 黑名单不是完整隔离。代码体检只应运行可信公开仓库和预设测试命令。

## 部署

服务器部署步骤、systemd 与 Caddy 模板见 [docs/deployment.md](docs/deployment.md)。一键脚本：

```bash
sudo REPO_URL=git@github.com:you/BoxPilot.git bash deploy/bootstrap.sh
```

脚本会把沙箱、后端和 systemd 服务装好，默认只监听本机端口，再由 Caddy 对外提供 HTTPS。

## 已知限制

- SQLite 默认运行于单进程、单 Worker。重启时中断任务标记为失败，可手动重试。详见 [SQLite 集成与验收](docs/sqlite_integration.md)。
- 运行中任务的取消在步骤边界生效，正在执行的 Shell 或浏览器动作不会被强制终止。
- 当前公开仓库体检可能受沙箱网络出口影响：克隆会失败重试，装依赖也可能超时；离线演示使用 `demo://python`。
- 沙箱自带 Python 3.10 与 3.12；3.13 在任务开始时按需补装，镜像不可达时才会降级试跑并在报告里标注。
