# BoxPilot 项目进展报告

- 日期：2026-11-05
- 负责人：曾雨格
- 当前阶段：第 2 至第 3 周功能推进
- 状态：新增模板已完成并通过本地与真实沙箱验证

## 1. 本阶段完成内容

### 1.1 可信代码仓库体检

实现了 `REPO_CHECK` 模板：

- 仅接受 `github.com`、`gitee.com`、`gitlab.com` 等白名单主机。
- 只允许无凭据的 HTTPS 仓库地址，拒绝查询参数、片段和路径异常。
- 使用 `git clone --depth 1` 浅克隆公开仓库。
- 自动识别 Python、Node 和不支持的项目类型。
- Python 项目固定运行 `python -m pytest -q`。
- Node 项目固定运行 `npm test -- --runInBand`。
- 测试命令非零退出属于有效检查结果，任务仍生成报告并标记 `test_status=failed`。
- 克隆失败、超时或沙箱异常会停止任务并保留错误码。
- 生成可下载的 `check-report.json` 和 `test.log`。

考虑到当前 AIO Sandbox 没有 pytest，且访问公开 GitHub 时出现 TLS 失败，新增了明确标记的离线演示输入 `demo://python`：

- 上传 `samples/repo_check_demo/` 中的示例代码。
- 在沙箱内执行本地 Git 初始化和提交。
- 使用 Python 标准库 `unittest` 执行 3 个测试。
- 断网环境也能完成仓库体检演示。

### 1.2 网页应用验收

实现了 `WEB_ACCEPTANCE` 模板：

- 必填参数为 `url` 和 `expected_title`。
- 只接受合法的 HTTP/HTTPS URL。
- 使用统一沙箱浏览器打开页面。
- 标题比较会归一化空白和大小写。
- 页面标题匹配时，报告 `passed=true`。
- 标题不匹配时，任务仍为 `SUCCESS`，报告记录 `passed=false` 和失败原因。
- 页面无法访问、截图失败或超时会使任务失败。
- 生成可下载的 `acceptance.json` 和 `page.png`。

### 1.3 调度与安全

- 新增单进程执行锁，避免并发请求同时操作共享浏览器页面。
- 任务依次执行固定步骤，失败后停止后续步骤。
- 模板通过统一 `TaskContext` 保存本次执行的临时状态，不写入公共参数。
- 所有新增后端代码均增加了文件职责说明和关键设计注释。

### 1.4 测试与文档

- 新增 `tests/test_templates.py`，覆盖仓库体检和网页验收的核心结果语义。
- 更新接口契约、README、环境变量示例和人工测试说明。
- 人工测试文档覆盖启动、冒烟、任务提交、页面检查、异常场景和安全测试。

## 2. 验证结果

| 验证项 | 结果 |
|---|---|
| Python 全量测试 | 20 项通过 |
| 真实 AIO Sandbox 集成测试 | 通过 |
| 前端 JavaScript 语法检查 | 通过 |
| 真实网页验收：标题匹配 | `SUCCESS`，生成 `acceptance.json`、`page.png` |
| 真实网页验收：标题不匹配 | `SUCCESS`，报告 `passed=false` |
| 离线仓库体检 | `SUCCESS`，3 个 unittest 通过，生成 2 个产物 |
| 后台任务执行顺序 | 增加执行锁，避免浏览器互相覆盖 |

真实网页验收记录：

- 目标：`http://host.docker.internal:8000/ui/task_list.html`
- 预期标题：`BoxPilot 任务工作台`
- 实际标题：`BoxPilot 任务工作台`
- HTTP 状态：200
- 验收结论：通过

标题不匹配记录：

- 预期标题：`故意不匹配的标题`
- 实际标题：`BoxPilot 任务工作台`
- 任务状态：`SUCCESS`
- 验收结论：`passed=false`

离线仓库体检记录：

- 输入：`demo://python`
- 工具链：`python-unittest`
- 测试命令：`cd repo && python -m unittest discover -q`
- 退出码：0
- 测试输出：`Ran 3 tests ... OK`

## 3. 当前可访问功能

- 任务工作台：`http://127.0.0.1:8000/ui/task_list.html`
- 创建任务页面：`http://127.0.0.1:8000/ui/create_task.html`
- Swagger：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`

当前已注册模板：

- `MOCK`
- `REPO_CHECK`
- `WEB_ACCEPTANCE`

尚未注册：

- `CSV_ANALYSIS`
- `WEB_DIGEST`

## 4. 已知问题与限制

1. 当前沙箱访问公开 GitHub 仓库时出现 `gnutls_handshake()` TLS 失败。代码能正确记录为 `COMMAND_FAILED`，但公开仓库演示依赖网络环境；现场演示应优先使用 `demo://python`。
2. 当前任务存储仍为内存实现，后端重启后任务记录丢失。需要陈咏忻接入 SQLite 三表。
3. 代码体检只运行固定测试命令，不是对任意项目的完整 CI。
4. Node 测试命令假定项目支持 Jest 风格参数，后续需要根据实际仓库调整。
5. 黑名单和目录限制只是基础防线，不能替代不可信代码的强隔离。

## 5. 后续工作

- 陈咏忻接入 SQLite `TaskStore`、`init_db.py`、CSV 上传和网页资料整理。
- 将任务创建页面接到正式 CSV 与网页整理模板。
- 增加任务筛选、更多模板参数校验和任务存储持久化测试。
- 完成 10 至 15 个端到端演示样例和最终测试统计。
- 进行最终页面视觉检查、演示脚本录制和部署文档整理。

## 6. 结论

本阶段完成了曾雨格负责的两个正式模板、离线仓库演示、调度串行化、安全校验和配套测试。当前版本已具备网页应用验收和代码仓库体检的真实执行与产物下载能力，可以继续推进数据库持久化和剩余两个模板的联调。
