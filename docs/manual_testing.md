# BoxPilot 人工测试说明

本文用于开发人员在不依赖自动化测试的情况下，检查沙箱、后端、前端和错误处理是否正常。

最后更新：2026-11-13，对应 66 项自动化测试与安全边界加固后的版本。

## 1. 测试前准备

1. 启动 Docker Desktop，等待 Docker 服务进入运行状态。
2. 在项目根目录运行 `.\start_sandbox.bat`，确认 AIO Sandbox 已启动。
3. 启动后端：

```powershell
./.venv/Scripts/python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

4. 打开 `http://127.0.0.1:8000/health`，应看到 `status` 为 `ok`，且 `sandbox.success` 为 `true`。
5. 打开 `http://127.0.0.1:8000/docs`，确认 Swagger 能正常加载。
6. 确认项目根目录出现 `logs/boxpilot.jsonl`；后续每个任务都应向其中追加 JSON 行。

## 2. 先跑真实沙箱冒烟测试

执行：

```powershell
./.venv/Scripts/python.exe scripts/test_sandbox.py
```

预期结果：

- 终端最后出现 `"success": true`。
- `workspace`、`file_write`、`file_read`、`shell`、`navigation`、`page_text`、`screenshot` 都为成功。
- `artifacts/smoke/smoke.png` 存在且大于 0 字节。

如果这一步失败，不要继续测试任务接口，先检查 Docker、8080 端口和沙箱服务。

## 3. 通过 Swagger 测试任务主链路

1. 在 `/docs` 中找到 `POST /tasks`。
2. 点击 `Try it out`，填入：

```json
{
  "task_type": "MOCK",
  "params": {}
}
```

3. 点击 `Execute`，预期返回 `202`，任务状态为 `PENDING`。
4. 复制响应中的任务 `id`。
5. 调用 `GET /tasks/{task_id}`，预期状态变为 `SUCCESS`。
6. 检查响应中三个步骤：`准备任务目录`、`执行沙箱验证`、`归档验证结果` 都应为 `SUCCESS`。
7. 检查 `artifacts` 中至少有一个 `mock-result.txt`。
8. 调用 `GET /artifacts/{artifact_id}/download`，预期下载文本：

```text
BoxPilot scheduler smoke test completed successfully.
```

## 4. 测试任务工作台

打开 `http://127.0.0.1:8000/ui/task_list.html`。

检查：

- 页面标题显示“任务工作台”，右上角健康状态显示“环境正常”。
- 概览区显示全部、运行中、成功、失败四个数字；它们统计全量任务，不受搜索和筛选影响。
- 任务列表显示刚才创建的 `MOCK` 任务，行内显示类型、短 ID、步骤数和状态徽标。
- 在搜索框输入任务 ID 前缀，300 ms 后列表自动刷新；清空后恢复全部。
- 切换状态筛选、切换“每页 10/20/50”，页码与“共 N 项”同步变化。
- 点击任务后，右侧显示任务 ID、创建/开始/结束时间、尝试次数和三个中文步骤。
- 详情顶部“任务结论”显示“开发验证完成”和沙箱命令输出。
- 产物区出现 `mock-result.txt`，带“预览”和“下载”两个按钮。

## 5. 测试创建任务页面

打开 `http://127.0.0.1:8000/ui/create_task.html`。

检查字段切换：

- 选择“网页资料整理”时，只显示网页地址。
- 选择“CSV 数据分析”时，只显示 CSV 文件上传。
- 选择“代码仓库体检”时，只显示仓库地址。
- 选择“网页应用验收”时，显示网页地址和预期标题。

四个正式模板均已注册。提交时应根据字段填写有效输入，错误输入会返回结构化校验信息。

## 6. 网页资料整理人工测试

在 Swagger 调用 `POST /tasks`：

```json
{
  "task_type": "WEB_DIGEST",
  "params": {"url": "http://host.docker.internal:8000/ui/task_list.html"}
}
```

预期任务状态为 `SUCCESS`，生成：

- `article.md`：Readability 去噪后的正文，保留 Markdown 链接。
- `screenshot.png`：整页截图。
- `web-digest.json`：URL、标题、HTTP 状态、提取方式，以及 `content` 里的字符数、词数、预计阅读分钟、标题数、链接数和图片数。

详情页的“任务结论”区应显示 `正文提取：Readability 去噪`、正文词数、预计阅读时长、HTTP 状态和截图大小。

如果页面无法打开或页面文本为空，任务应失败，不生成伪成功任务。

## 7. CSV 数据分析人工测试

使用 Swagger 的 `POST /tasks/csv` 上传一个 `.csv` 文件，字段名为 `file`。也可以从创建任务页面选择“CSV 数据分析”并上传文件。

测试样例：

```csv
name,score,group
A,10,x
B,20,x
B,20,x
C,,y
```

预期任务状态为 `SUCCESS`，生成：

- `summary.json`：4 行、3 列、1 个重复行、`score` 列 1 个缺失值、完整度 91.7%，并带每列的类型、唯一值、前三个示例和高频值。
- `report.md`：中文分析报告，含列概览表格（列名、类型、非空、缺失、唯一值、摘要）。数值列显示最小/最大/均值，文本列显示示例和高频值。

打开预览，确认表格列对齐、均值保留两位小数，且不再出现 `page.txt`、`report.txt` 这类旧产物。

空文件、非 `.csv` 文件或超过 5 MB 的文件应被明确拒绝。

## 8. 测试异常与安全场景

### 未知任务类型

在 Swagger 调用 `POST /tasks`：

```json
{
  "task_type": "UNKNOWN",
  "params": {}
}
```

预期在请求模型校验阶段返回 `422`。

### CSV 缺少文件

直接调用 `POST /tasks/csv` 但不提供文件，预期返回 `422`。

### CSV 误用普通任务接口

通过 `POST /tasks` 提交 `CSV_ANALYSIS`，预期返回 `422`，错误码为 `CSV_UPLOAD_REQUIRED`。CSV 必须使用文件上传接口。

### 不存在的任务

调用 `GET /tasks/not-found`，预期返回 `404`，错误码为 `TASK_NOT_FOUND`。

### 不存在的产物

调用 `GET /artifacts/not-found/download`，预期返回 `404`，错误码为 `ARTIFACT_NOT_FOUND`。

### 危险命令拦截

在项目根目录执行：

```powershell
./.venv/Scripts/python.exe -c "from backend.sandbox_client import SandboxAdapter; print(SandboxAdapter().exec_shell('rm -rf /', task_id='manual-test'))"
```

预期 `success` 为 `False`，错误码为 `COMMAND_BLOCKED`，且命令不能真正执行。

## 9. 仓库体检人工测试

在 Swagger 调用 `POST /tasks`：

```json
{
  "task_type": "REPO_CHECK",
  "params": {"repo_url": "demo://python"}
}
```

预期：

- 六个步骤全部为 `SUCCESS`，其中“准备测试环境”负责选解释器和装依赖。
- 生成 `check-report.md`、`check-report.json`、`test.log`；克隆重试过才保留 `clone.log`，安装失败才保留 `install.log`，成功时不再产生这两份日志。
- 报告首页是中文摘要：仓库、项目类型、结论、测试命令、退出码、克隆次数、仓库要求 Python、实际使用 Python、依赖准备状态；JSON 只保留同样的结论字段，不再堆原始输出。
- 报告中 `toolchain` 为 `python-unittest`，`test_status` 为 `passed`，退出码为 0。

公开 GitHub/Gitee/GitLab 仓库也可作为输入。沙箱网络不可达时，任务会自动重试一次，随后失败并给出中文原因；错误码为 `REPO_UNREACHABLE` 或 `REPO_TLS_FAILED`，原始 git 输出折叠在“技术详情”，同时生成 `clone-failure.json` 说明产物。

仓库体检默认就会准备测试环境，不需要额外参数。系统会在任务目录用 uv 建独立 `.venv`，按仓库的 `requires-python` 从 3.13、3.12、3.11、3.10 中挑可用解释器（缺 3.13 时现场补装），安装声明依赖，再补 pytest 并尝试把项目装成可编辑包；结果写入沙箱内的 `install-result.json`，安装失败时才追加 `install.log`。只想做浅层检查时，显式传 `"install_deps": false`。

沙箱镜像自带 Python 3.10 与 3.12。仓库要求 3.13 时，系统会先用 `uv python install` 补装（默认走 npmmirror，通常三秒内完成），装上后按严格版本运行，报告多一行“解释器准备：按仓库要求现场安装”。补装失败才会降级：报告多一行“版本说明”和 `version_relaxed: true`；传 `"allow_version_mismatch": false` 可改为直接给出“版本不满足”，不执行安装。安装失败、缺依赖、缺 pytest 都记为 `test_status=error`，不会误报成“测试未通过”；沙箱既没有 pytest 也没有标准库用例时标记为 `not_run`。

## 10. 网页验收人工测试

提交：

```json
{
  "task_type": "WEB_ACCEPTANCE",
  "params": {
    "url": "http://host.docker.internal:8000/ui/task_list.html",
    "expected_title": "BoxPilot 任务工作台"
  }
}
```

预期任务状态为 `SUCCESS`，生成 `acceptance.json` 和 `page.png`。`acceptance.json` 含预期标题、实际标题、`passed`、HTTP 状态、加载耗时、截图大小、检查时间；报告中 `passed` 应为 `true`。

将 `expected_title` 改为一个不匹配的值后再次提交：任务仍应为 `SUCCESS`，但报告中的 `passed` 应为 `false`。如果页面无法访问，任务状态才应为 `FAILED`。

## 11. 任务取消与重试人工测试

### 取消等待中的任务

创建任务后立刻在工作台点击“取消”。预期状态变为 `CANCELLED`，不再生成产物。

### 取消运行中的任务

创建一个 `WEB_DIGEST` 任务，在它执行期间点击“取消”。预期当前步骤继续跑完，任务在下一步开始前落为 `CANCELLED`；已产出的文件保留，不会出现只写了一半的产物。

### 重试失败任务

让一个任务失败（例如网页验收使用 `http://127.0.0.1:9/`）。在列表或详情中点击“重试”，预期：

- 生成新任务，列表显示“第 2 次尝试”，详情显示重试来源。
- 原任务保持失败状态，不被覆盖。
- 对 `SUCCESS` 任务不显示重试按钮；直接调用 `POST /tasks/{id}/retry` 返回 `409 TASK_NOT_RETRYABLE`。

## 12. 搜索、分页、批量清理与产物预览

- 搜索框输入任务 ID 前缀、类型关键字或错误码，300 ms 后列表刷新，右侧统计数字保持不变。
- 状态筛选选择“已取消”，只显示 `CANCELLED` 任务；切换“每页 10/20/50”，页码与总数同步更新。
- 勾选若干任务后点击“删除选中”，活动任务会被跳过，提示中显示删除数与跳过数。
- 点击“清理历史”，默认清理 `SUCCESS`、`FAILED`、`TIMEOUT`、`CANCELLED`，活动任务不会被删除。
- 打开一个 `WEB_DIGEST` 任务详情，产物区每个文件都有“预览”“下载”。文本预览显示截断后的正文，图片预览显示整页截图，二进制文件提示下载。
- 预览弹窗可用右上角关闭按钮或 `Esc` 关闭，弹窗内“下载文件”可用。

## 13. 页面响应式检查

在浏览器开发者工具中切换宽度：

- 1280 px：任务列表和详情栏左右排列。
- 800 px：详情栏移动到任务列表下方，页面不应出现横向滚动条。
- 620 px 以下：概览区和任务行切换为单列布局。

同时检查按钮文字不溢出、任务状态不遮挡任务名称、下载按钮仍可点击。

## 14. 自动化回归

无沙箱单元测试：

```powershell
./.venv/Scripts/python.exe -m unittest discover -s tests -v
```

预期 `Ran 66 tests ... OK (skipped=1)`。真实沙箱用例默认跳过，其余 65 项使用替身沙箱。

真实沙箱集成测试：

```powershell
$env:BOXPILOT_RUN_SANDBOX_TESTS='1'
./.venv/Scripts/python.exe -m unittest discover -s tests -v
```

前端脚本语法检查：

```powershell
node --check frontend/task_list.js
node --check frontend/create_task.js
```

## 15. 人工测试记录模板

| 编号 | 场景 | 操作 | 预期 | 实际 | 通过 |
|---|---|---|---|---|---|
| M01 | 沙箱冒烟 | 运行 `scripts/test_sandbox.py` | 全部检查成功 |  |  |
| M02 | 健康检查 | 打开 `/health` | 状态 `ok` |  |  |
| M03 | 创建任务 | Swagger 调用 `POST /tasks` | 返回 `202` |  |  |
| M04 | 任务完成 | 查询任务详情 | 状态 `SUCCESS`，3 个步骤成功 |  |  |
| M05 | 产物下载 | 点击下载按钮 | 文件内容正确 |  |  |
| M06 | 未知任务类型 | 提交 `UNKNOWN` | 返回 `422` |  |  |
| M07 | CSV 正常上传 | 上传 4 行样例 CSV | 任务成功并生成 2 个产物 |  |  |
| M08 | 危险命令 | 执行拦截测试 | 返回 `COMMAND_BLOCKED` |  |  |
| M09 | 窄屏页面 | 800 px 宽度 | 无横向溢出 |  |  |
| M10 | 离线仓库体检 | 提交 `demo://python` | 报告 passed，生成报告、JSON 和测试日志 3 个产物 |  |  |
| M11 | 网页标题不匹配 | 提交错误预期标题 | 任务成功，报告 passed=false |  |  |
| M12 | 网页资料整理 | 提交本地任务页面 URL | 生成 `article.md`、整页截图和 JSON 报告 3 个产物 |  |  |
| M13 | CSV 空文件 | 上传空 CSV | 返回明确校验错误 |  |  |
| M14 | 运行中取消 | 执行期间点击取消 | 步骤边界落为 `CANCELLED` |  |  |
| M15 | 失败重试 | 失败任务点击重试 | 新任务 `attempt=2`，`retry_of` 正确 |  |  |
| M16 | 搜索与分页 | 输入关键词、切换每页条数 | 列表与总数同步，统计不变 |  |  |
| M17 | 批量清理 | 勾选混合状态任务后删除 | 活动任务跳过，终态任务删除 |  |  |
| M18 | 文本与图片预览 | 打开 `article.md` 和 `screenshot.png` | 弹窗显示正文/截图，可下载 |  |  |
| M19 | 结构化日志 | 查看 `logs/boxpilot.jsonl` | 每行合法 JSON，含 task_id 与 event |  |  |
| M20 | 仓库不可达 | 断网或提交不可达仓库地址 | 自动重试一次，中文原因、技术详情和 `clone-failure.json` |  |  |
| M21 | 步骤超时 | 把 `STEP_TIMEOUT_SECONDS` 调到 5 后提交耗时任务 | 任务落为 `TIMEOUT`，步骤错误码 `STEP_TIMEOUT`，可继续创建新任务 |  |  |
| M22 | 缺 pytest | 在无 pytest 的沙箱提交 pytest 风格仓库 | 自动改用 unittest；缺依赖时报“依赖未安装”，不显示“测试未通过” |  |  |
| M23 | 默认安装依赖 | 提交仓库体检，不带任何安装参数 | 成功时产物只有报告与测试日志；失败明确报“依赖安装失败”并保留 `install.log` |  |  |
| M24 | 按仓库补装解释器 | 提交要求 Python 3.13 的仓库，保持默认参数 | 自动装好 3.13，报告显示“解释器准备：按仓库要求现场安装”，测试用 3.13 运行 |  |  |
| M25 | 产物字段检查 | 依次跑四个模板并打开报告 | 网页整理有词数与阅读时长，CSV 有完整度与高频值，仓库报告含实际 Python 版本，验收报告含截图大小 |  |  |
| M26 | 重复提交 | 用同一参数连续提交两次 MOCK 或网页任务 | 第二次返回同一任务 ID，`deduplicated` 为 `true`，不新增任务 |  |  |
| M27 | 内网地址 | 提交 `http://127.0.0.1:8080/` 或 `http://host.docker.internal/` | 第一步校验失败，任务 `FAILED`、错误码 `VALIDATION_ERROR`，提示改用公网地址 |  |  |
| M28 | 提交限流 | 把 `RATE_LIMIT_PER_MINUTE` 调到 2，连续提交三次 | 前两次 202，第三次 429，错误码 `RATE_LIMITED`，带 `Retry-After` |  |  |
