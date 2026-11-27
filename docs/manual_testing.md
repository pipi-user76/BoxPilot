# BoxPilot 人工测试说明

本文用于开发人员在不依赖自动化测试的情况下，检查沙箱、后端、前端和错误处理是否正常。

最后更新：2026-11-27，对应 117 项自动化测试与登录、数据隔离、账号管理、定时任务、服务器部署版本。

## 1. 测试前准备

1. 启动 Docker Desktop，等待 Docker 服务进入运行状态。
2. 在项目根目录运行 `.\start_sandbox.bat`，确认 AIO Sandbox 已启动。
3. 首次运行时初始化数据库（后端启动时也会自动建表，这一步用于提前确认路径可写）：

```powershell
./.venv/Scripts/python.exe -m scripts.init_db
```

预期输出数据库路径，默认是 `data/boxpilot.db`。可用环境变量 `DATABASE_PATH` 换成其他位置。
4. 启动后端（SQLite 只支持单进程单 Worker，不要加 `--workers`）：

```powershell
./.venv/Scripts/python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

5. 打开 `http://127.0.0.1:8000/health`，应看到 `status` 为 `ok`、`store` 为 `sqlite`，且 `sandbox.success` 为 `true`。
6. 打开 `http://127.0.0.1:8000/docs`，确认 Swagger 能正常加载。
7. 确认项目根目录出现 `logs/boxpilot.jsonl`；后续每个任务都应向其中追加 JSON 行。

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

- 页面为“图标栏 + 侧栏 + 列表 + 详情”布局，顶部面包屑显示“工作台 / 任务列表”，右上角健康状态显示“环境正常”。
- 侧栏“功能”显示五个入口：网页资料整理、CSV 数据分析、代码仓库体检、网页应用验收、全部任务；底部显示当前账号与角色。
- 任务列表上方有“全部 / 运行中 / 成功 / 失败 / 已取消”页签，数字按当前功能页统计，侧栏“全部任务”显示全量数字。
- 任务列表显示刚才创建的 `MOCK` 任务，行内显示目标、短 ID、类型、步骤数、创建时间和状态徽标。
- 在搜索框输入任务 ID 前缀，300 ms 后列表自动刷新；清空后恢复全部。
- 切换功能页或状态页签，列表与“N 个任务 · 已筛选”同步变化；切换“每页 10/20/50”，页码与“共 N 项”同步变化。
- 点击任务后，右侧滑层显示任务 ID、创建/开始/结束时间、尝试次数和三个中文步骤，可用右上角关闭。
- 详情页签“任务结论 / 执行步骤 / 任务产物”可切换；“任务结论”显示“开发验证完成”和沙箱命令输出。
- 产物页签出现 `mock-result.txt` 卡片，点击可预览，卡片内提供下载。

## 5. 测试创建任务弹窗

在工作台点右上角“创建任务”，或点侧栏某一功能后点“创建任务”。

检查字段切换：

- 在“网页资料整理”页打开弹窗，只显示网页地址、批量地址和批次名称。
- 在“CSV 数据分析”页打开弹窗，只显示 CSV 文件上传。
- 在“代码仓库体检”页打开弹窗，只显示仓库地址、批量地址和批次名称。
- 在“网页应用验收”页打开弹窗，显示网页地址和预期标题。
- 在“全部任务”页打开弹窗，先选任务类型，字段随选择切换。

四个正式模板均已注册。提交时应根据字段填写有效输入，错误输入会返回结构化校验信息并显示在弹窗底部。

批量订阅：

- 选“网页资料整理”或“代码仓库体检”，在“批量地址”里每行填一个地址，可填批次名称。
- 提交后跳到任务列表并只显示该批次，标题为“类型 · 目标”，右侧带批次标签。
- 打开任一任务，详情显示“批次：…”和“重跑本批”按钮；点击后按原地址重建一批。

下载命名：

- 详情点“下载全部”，得到 `类型_目标_时间_短ID.zip`，包内文件名带同一前缀。
- 勾选多个任务点“下载所选”，批量包按每个任务的规范名分目录。

## 6. 网页资料整理人工测试

在 Swagger 调用 `POST /tasks`：

```json
{
  "task_type": "WEB_DIGEST",
  "params": {"url": "books.toscrape.com"}
}
```

列表页（如 `https://books.toscrape.com`）应进入结构化模式，预期任务状态为 `SUCCESS`，生成：

- `article.md`：条目表，含序号、标题（可点击）、价格、库存、评分。
- `items.json`：结构化数组，字段为 `title`、`price`、`stock`、`rating`、`link`、`image`。
- `items.csv`：与 JSON 相同的字段，UTF-8 编码，可直接用 Excel 打开。
- `screenshot.png`：整页截图。
- `web-digest.json`：`structured`（条目数、页面选择器、字段）与 `crawler`（引擎、抓取时间、最终地址、状态码、加载耗时）。

普通文章页（如 `https://example.com`）没有重复条目，退回去噪正文模式，只生成 `article.md`、`screenshot.png`、`web-digest.json`。

详情页的“任务结论”区应显示提取方式、正文词数、预计阅读时长、HTTP 状态和截图大小；列表页还会显示“识别条目 N 条”。

如果页面无法打开、正文为空，或只剩图片链接，任务应失败，不生成伪成功任务。

## 7. CSV 数据分析人工测试

使用 Swagger 的 `POST /tasks/csv` 上传一个 `.csv` 文件，字段名为 `file`。也可以在“CSV 数据分析”页点“创建任务”上传文件。

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

需要更接近真实数据时，可上传 `samples/normal.csv`（99 行商品明细，含 3 个重复行、10 个缺失单元格），预期完整度 99.1%。

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

- 搜索框输入任务 ID 前缀、类型关键字或错误码，300 ms 后列表刷新，页签统计数字保持不变。
- 点击状态页签或侧栏功能入口，只显示对应任务；两者可以组合；切换“每页 10/20/50”，页码与总数同步更新。
- 未选中任务时，“下载所选”“批量删除”为禁用状态；勾选“全选本页”后，本页全部任务被选中，按钮变为可用，再次取消则恢复禁用。
- 勾选若干任务后点击“批量删除”，活动任务会被跳过，提示中显示删除数与跳过数。
- 点击“清理历史”，默认清理 `SUCCESS`、`FAILED`、`TIMEOUT`、`CANCELLED`，活动任务不会被删除。
- 任务详情“任务产物”右侧有“下载全部”，点击后得到 `boxpilot-<任务短 ID>.zip`；勾选多个任务后点“下载所选”，得到 `boxpilot-N-tasks.zip`，包内按任务短 ID 分目录。
- 打开一个 `WEB_DIGEST` 任务详情，产物区每个文件都有“预览”“下载”。文本预览显示截断后的正文，图片预览显示整页截图，二进制文件提示下载。
- 预览弹窗可用右上角关闭按钮或 `Esc` 关闭，弹窗内“下载文件”可用。

## 13. 页面响应式检查

在浏览器开发者工具中切换宽度：

- 1280 px：图标栏、侧栏、任务列表和详情栏四栏并排，表格列对齐。
- 1080 px 以下：侧栏保留，详情栏变成右侧滑层，选中任务后滑入，可用 `Esc` 或右上角关闭。
- 820 px 以下：侧栏隐藏，只留图标栏；任务行改为两行布局，操作按钮移到第二行。

同时检查按钮文字不溢出、任务状态不遮挡任务名称、下载按钮仍可点击。

## 14. 自动化回归

无沙箱单元测试：

```powershell
./.venv/Scripts/python.exe -m unittest discover -s tests -v
```

预期 `Ran 117 tests ... OK (skipped=1)`。真实沙箱用例默认跳过，其余 116 项使用替身沙箱。

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

## 15. 数据库持久化与重启恢复

这一节确认任务记录不再随进程退出而丢失。

### 15.1 表结构与初始化

```powershell
./.venv/Scripts/python.exe -m scripts.init_db
```

用任意 SQLite 工具打开 `data/boxpilot.db`，预期：

- 只有 `tasks`、`steps`、`artifacts` 三张业务表。
- `PRAGMA journal_mode` 返回 `wal`。
- `PRAGMA user_version` 返回 `1`。

如果数据库里已经有其他表或版本号不是 0/1，启动会报“无法使用此数据库结构”，换一个 `DATABASE_PATH` 即可。

### 15.2 重启后任务仍在

1. 提交一个 MOCK 任务，等待成功，记录任务 ID。
2. 停止后端进程，再按第 1 节命令重新启动。
3. 调用 `GET /tasks/{id}`，预期状态仍为 `SUCCESS`，步骤、结论和产物索引都在。
4. 下载产物，文件内容和重启前一致。

自动化脚本 `scripts/verify_sqlite_aio.py` 用两个先后独立的进程做了同样的检查，可作为对照。

### 15.3 中断任务收尾

1. 提交仓库体检或网页整理任务，等它进入 `RUNNING`。
2. 直接结束后端进程，再重新启动。
3. 查询该任务，预期状态为 `FAILED`，错误码 `SERVICE_RESTARTED`，不会永远停在运行中。
4. 点击“重试任务”，应生成 `attempt=2` 的新任务。

### 15.4 提交去重

1. 同一个参数连续提交两次 MOCK 任务。
2. 预期两次返回同一个任务 ID，第二次响应里 `deduplicated` 为 `true`，列表总数不增加。
3. 等超过 `DEDUPE_WINDOW_SECONDS`（默认 300 秒）再提交，预期生成新任务。
4. 失败或取消的任务不参与去重，可以直接重试。

注意：同一个数据库不要同时被多个后端进程写入；单进程单 Worker 是当前设计前提。

## 16. 登录与数据隔离

1. 用一个空数据库启动后端，打开 `http://127.0.0.1:8000/ui/task_list.html`，应跳转到 `/ui/login.html`。
2. 首次进入时页面显示“创建管理员”，提交用户名与密码后自动登录，回到任务列表。
3. 再打开一次登录页，应只剩登录表单；直接调用 `POST /auth/register` 返回 403 `REGISTRATION_CLOSED`。
4. 管理员从侧栏“账号管理”进入 `/ui/users.html`，页面应与其他功能页共用同一套外壳；点“创建账号”用弹窗建第二个账号，退出后用新账号登录。
5. 新账号的任务列表应为空；直接访问第一个账号的任务详情、产物下载或打包接口，均返回 404。
6. 在账号管理页给该账号点“重置密码”，保存后旧密码登录应失败、新密码可以登录。
7. 点“删除”并确认后，账号从列表消失且无法再登录；当前登录账号的“删除”按钮应禁用。
8. 退出登录后访问 `/tasks`，返回 401；页面访问会跳回登录页。

## 17. 服务器部署检查

部署步骤见 [deployment.md](deployment.md)。在服务器上按顺序确认：

```bash
systemctl status boxpilot --no-pager
curl -s http://127.0.0.1:8000/health
docker ps --filter name=boxpilot-sandbox
```

1. `/health` 返回 `status=ok`、`store=sqlite`、`sandbox.success=true`。
2. `curl -I http://127.0.0.1:8000/ui/task_list.html` 返回 303，`Location` 指向 `/ui/login.html`。
3. 通过域名访问时证书有效，登录后能创建任务并下载产物。
4. `journalctl -u boxpilot -n 50` 无重复崩溃；`/var/lib/boxpilot/logs/boxpilot.jsonl` 有任务事件。
5. 重启服务后任务仍在，运行中的旧任务变成 `FAILED / SERVICE_RESTARTED`，可以重试。
6. 备份命令可执行，数据库与 `artifacts/` 能在另一台机器上恢复。

## 18. 定时任务与通知

1. 侧栏点“定时任务”，确认页面与工作台同一套外壳，右上角有铃铛。
2. 点“新建定时任务”，填写名称、选择模板与参数，把周期切成“每天 08:00”，确认下方 cron 预览为 `0 8 * * *`，提交后列表出现该记录。
3. 点任务名称打开历史抽屉，确认“暂无执行记录”；点“立即执行”，几秒后刷新，抽屉出现一条记录并可跳到任务详情。
4. 点“暂停”，状态变灰且不再有下次执行；再点“启用”，下次执行时间重新计算。
5. 任务完成或失败后，右上角铃铛出现红点；展开后能看到“任务完成/任务失败”，点“全部已读”红点消失。
6. 重启服务后回到页面，定时任务仍在，说明 APScheduler 的持久化生效。

## 19. 知识库

1. 进入网页资料整理页，点顶部“我的知识库”，确认任务列表被隐藏、文档与问答区出现。
2. 点“新建知识库”，填名称“竞赛资料”与备注，创建后下拉自动切到该库。
3. 回到“任务列表”，勾选一个成功的网页整理任务，点“存入所选任务”，选择目标库；文档卡片出现标题、来源链接、入库时间与分段数。
4. 在右侧输入问题并提问，回答应有正文，并在下方出现“引用资料 N 条”，展开可看到片段与原始链接。
5. 点“刷新向量”后分段数更新；删除文档后卡片消失，再次提问不会再引用它。
6. 用另一个账号登录，确认看不到第一个账号的知识库；管理员也不跨用户查看。

## 20. 人工测试记录模板

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
| M12 | 网页资料整理 | 提交列表页 URL（books.toscrape.com） | 生成条目表 `article.md`、`items.json`、`items.csv`、整页截图和 JSON 报告 5 个产物，条目数为 20 |  |  |
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
| M29 | 数据库初始化 | 运行 `python -m scripts.init_db` | 三张业务表、`journal_mode=wal`、`user_version=1` |  |  |
| M30 | 重启持久化 | 建 MOCK 任务成功后重启后端再查询 | 状态、步骤、结论和产物索引仍在，产物可下载 |  |  |
| M31 | 中断任务恢复 | 运行中直接结束后端再启动 | 任务落为 `FAILED`、错误码 `SERVICE_RESTARTED`，可重试 |  |  |
| M32 | 提交去重 | 同参数连续提交两次，再等过窗口期提交 | 前两次同 ID 且 `deduplicated=true`，过窗口后生成新任务 |  |  |
| M33 | 一键与批量下载 | 任务详情点“下载全部”；勾选两个任务点“下载所选” | 分别得到单任务 zip 与按任务名分目录的批量 zip，文件可解压 |  |  |
| M34 | 批量订阅 | 创建页填两行地址、批次名“每周巡检”后提交 | 列表只出现该批次，标题显示“类型 · 目标”，带批次标签；详情可“重跑本批” |  |  |
| M35 | 下载命名 | 下载任一单任务 zip，解压并查看文件名 | 压缩包与内部文件均为 `类型_目标_时间_短ID`，时间与界面一致 |  |  |
| M36 | 登录与隔离 | 注册管理员、创建第二个账号并登录；用第二个账号访问第一个账号的任务 | 未登录跳转登录页或 401；第二个账号列表为空，越权详情/下载/打包均为 404 |  |  |
| M37 | 服务器部署 | 在服务器执行部署脚本，访问域名并重启一次服务 | `/health` 正常、HTTPS 证书有效、任务与产物在重启后仍可查询和下载 |  |  |
| M38 | 定时任务 | 新建“每天 08:00”的网页整理任务，暂停、启用、立即执行各一次 | cron 预览为 `0 8 * * *`；状态与下次执行随启停变化；立即执行生成任务并在历史里可见 |  |  |
| M39 | 站内通知 | 等一个任务完成或失败，打开右上角铃铛 | 出现“任务完成/任务失败”，红点计数正确，单条和全部已读后红点消失 |  |  |
| M40 | 知识库入库 | 新建“竞赛资料”知识库，把一篇网页整理任务存入 | 文档卡片显示标题、来源、入库时间与分段数；Chroma 目录出现索引文件 |  |  |
| M41 | RAG 问答 | 在知识库提问一个正文能回答的问题 | 回答引用资料，展开可见片段与链接；刷新页面后问答历史仍在 |  |  |
| M42 | 文档上传与检索参数 | 在知识库上传一个 TXT；把相似度阈值拉到 90% 后提问 | 文档列表出现新文档；高阈值时回答提示“没有检索到足够相关”，导出对话得到 Markdown |  |  |
