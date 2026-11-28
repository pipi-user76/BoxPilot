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

更新时间：2026-11-28。工作台按功能拆成独立页面：侧栏进入“网页资料整理 / CSV 数据分析 / 代码仓库体检 / 网页应用验收 / 知识库 / 定时任务 / 账号管理”，每个页面只列本类任务；“创建任务”用两列分组悬浮框按类型收集参数，CSV 支持 1–5 个文件上传，提交后直接回到对应列表。SQLite 已迁移到 schema v10（任务、步骤、产物、用户、定时任务/通知、知识库/文档/会话/切片、标签/批注），网页整理、网页对比、CSV、仓库体检、网页验收五个模板沿用统一 API、调度器和沙箱适配器；任务按用户隔离，服务器采用 systemd + nginx，环境变量放 `/etc/boxpilot.env`。

| 模块 | 状态 | 当前实现 |
|---|---|---|
| FastAPI 与任务 API | 已完成 | 创建、搜索分页列表、详情、取消、重试、批量删除、清理、产物预览与下载 |
| AIO Sandbox 适配 | 已完成 | Shell、文件、浏览器、trafilatura/Readability 正文提取、整页截图、上传和下载 |
| 任务调度 | 已完成 | 固定步骤、状态流转、失败停止、串行执行锁、步骤边界取消、失败重试、步骤与任务超时兜底 |
| 结构化日志 | 已完成 | JSON Lines 输出到 stdout 与 `logs/boxpilot.jsonl`（5 MB × 3 轮转） |
| 网页资料整理 `WEB_DIGEST` | 已完成 | `article.md` 去噪正文、整页 `screenshot.png`、`web-digest.json`（词数、阅读时长、标题数、链接数、图片数） |
| 网页对比 `WEB_COMPARE` | 已完成 | 2–10 个 URL 的对比报告 `compare.md` / `compare.json`，每个来源的 `source-N.md`、`source-N.png`、`source-N.items.json`；详情为“来源/结论 + 产物下拉 + 固定底部对话” |
| CSV 分析 `CSV_ANALYSIS` | 已完成 | 支持 1–5 个 CSV 上传、表画像/列角色、ECharts SVG 图表、固定 5 色配色、图表点击放大与 SVG/PNG 下载、LLM 可选解读、中文 `report.md`；多文件按数据集分 tab，每个数据集生成全部可用图表 |
| 仓库体检 `REPO_CHECK` | 已完成 | 白名单仓库、固定测试命令、离线 `demo://python`、失败重试、中文错误说明与失败报告；默认用 uv 在任务目录建独立虚拟环境，按 `requires-python` 选或现场补装 3.13/3.12/3.11/3.10，装完依赖再补 pytest 并尽量把项目装成可编辑包 |
| 网页验收 `WEB_ACCEPTANCE` | 已完成 | 标题比对、整页截图、`acceptance.json`（预期/实际标题、状态码、加载耗时、截图大小、检查时间） |
| Web 工作台 | 已完成进阶版 | 功能各自成页，创建任务使用两列分组悬浮框（编号分组 + 标签/字段）；点击任务后进入产物优先布局：收起侧栏文字、任务列表自适应左缩、产物区约占半屏；对比详情的来源/结论与对话分区，输入框固定底部，任务产物改为下拉浮层；六个页面首屏内联底色，避免样式表加载前白屏；状态页签、服务端搜索分页、批量订阅、全选与批量下载、一键下载全部、取消/重试、任务结论、文本与图片预览 |
| 下载命名 | 已完成 | 任务与压缩包统一为 `类型_目标_时间_短ID`，批量包按任务名分目录 |
| SQLite 持久化 | 已完成 | schema v10：任务/步骤/产物、用户、定时任务/通知、知识库/文档/消息/切片、标签/批注；WAL 模式；`DATABASE_PATH` 应指向持久化目录 |
| 账号与数据隔离 | 已完成 | 用户名/密码登录、scrypt 哈希、签名 Cookie，任务按 `owner_id` 隔离，管理员可查看全部 |
| 知识库与 RAG | 已完成 | 独立三栏页面；文档按会话入库 `session.md`；Chroma 向量 + SQLite FTS5，RRF 融合、查询改写、SSE 流式问答、引用折叠、会话重命名/删除 |
| 定时任务与通知 | 已完成 | APScheduler + `schedules`/`notifications` 表；cron 五段规则、立即执行、历史记录、站内通知与未读数 |
| 标签、快照与导出 | 已完成 | 任务标签、知识库文档标签、批注；`GET /tasks/{id}/snapshots`；CSV/Markdown/GB7714 导出 |
| 服务器部署 | 已完成 | `deploy/bootstrap.sh` 安装依赖，systemd 单进程托管，nginx 反向代理；环境变量放 `/etc/boxpilot.env`，数据库固定到 `/var/lib/boxpilot/data/boxpilot.db` |
| 10 至 15 个系统样例 | 进行中 | 已有 153 项自动化测试，尚需形成统一验收表和统计报告 |

当前已注册 `MOCK`、`CSV_ANALYSIS`、`WEB_DIGEST`、`WEB_COMPARE`、`REPO_CHECK` 和 `WEB_ACCEPTANCE`。未知任务类型会返回明确校验错误，不会生成伪成功任务。

最近验证结果：

- 153 项 Python 测试中，1 项真实沙箱集成测试默认跳过。
- 登录链路已实测：未登录访问工作台 303 跳转登录页；管理员 alice 可见全部 8 条任务，新账号 bob 登录后列表为 0，直接访问 alice 的任务详情与打包接口均返回 404。
- 真实 `WEB_DIGEST` 优先用 trafilatura 解析原始 DOM，失败时回退到沙箱 Readability/可见文本；列表页仍识别条目，`books.toscrape.com` 抽出 20 条、268 词，产出 5 个文件（正文、条目 JSON、条目 CSV、整页截图、报告）。
- 批量订阅一次提交多个地址，共享同一批次号；批次可在列表里筛选，详情里可一键重跑。
- 任务与压缩包按 `类型_目标_时间_短ID` 命名，单个压缩包内的文件也带同一前缀，下载后不会互相覆盖。
- 真实仓库体检默认建 `.venv` 并现场补装 Python 3.13.6，装完依赖后跑通本项目 44 项测试（1 项跳过）。
- 真实 CSV 分析会自动识别表型（数值时序、二维交叉、对比清单、纯文本等）、列角色与推荐视图，生成 `summary.json` + 中文 `report.md`，前端用 ECharts 5.5（SVG renderer）渲染折线/柱状/饼图/热力/散点/思维导图，并附可选的中文解读与表内问答；支持 1–5 个 CSV，多文件按数据集切换，使用固定 5 色配色。
- 真实取消任务在步骤边界落为 `CANCELLED`；失败任务重试沿用同一条任务记录，`attempt` 递增、不新增任务条，旧步骤与产物先清空再重跑；失败结论统一带 `reason` 与 `code`。沙箱不可用、页面打不开、超时这类基础设施失败会自动补跑一次（`AUTO_RETRY_ATTEMPTS`，默认 1，参数错误与登录问题不重试）。
- 列表分页、搜索、批量清理、文本预览和图片预览均已通过浏览器联调。
- 健康检查反映运行时状态；服务与 AIO Sandbox 启动后检查 `/health`。

## 产物清单

| 模板 | 产物 | 说明 |
|---|---|---|
| `WEB_DIGEST` | `article.md`、`items.json`、`items.csv`、`screenshot.png`、`web-digest.json` | 列表页抽条目成表，文章页只留正文、截图和报告；报告含词数、阅读时长、条目数与爬虫元数据 |
| `WEB_COMPARE` | `compare.md`、`compare.json`、各来源 `source-N.md` / `source-N.png` / `source-N.items.json` | 多页对比报告；详情含来源、结论和逐来源摘要，任务产物以浮层展示，对话固定在底部 |
| `CSV_ANALYSIS` | `summary.json`、`report.md` | JSON 含行列数、完整度、表画像、列角色、7 种视图的可用性与图表数据、可选 `insight`；多文件含 `datasets[]` 与 `multi_dataset` 表型，每个数据集保留全部可用图表；Markdown 含表型、推荐视图、分析结论与列概览 |
| `REPO_CHECK` | `check-report.md`、`check-report.json`、`test.log` | 成功时的最小集合；克隆重试过才有 `clone.log`，安装失败才有 `install.log`；`install-result.json` 只留在沙箱 |
| `WEB_ACCEPTANCE` | `acceptance.json`、`page.png` | JSON 含预期/实际标题、是否通过、状态码、加载耗时、截图大小、检查时间 |

失败任务同样会留下能解释原因的产物，例如仓库克隆失败时的 `clone-failure.json`。

下载时的命名规则：`web-digest_books.toscrape.com_20261123-2204_e24e56ee.zip`。单任务包内是 `web-digest_..._article.md` 这类带前缀的文件；批量包按任务名建目录，目录内保留原文件名。

## 下一步计划

| 优先级 | 时间 | 工作内容 | 完成标准 |
|---|---|---|---|
| 已完成 | SQLite schema v10 | 默认持久化 `TaskStore` 已接入 | 重启后任务、步骤、产物、知识库与调度记录仍可查询 |
| 已完成 | 2026-11-16 | 五模板持久化真实沙箱回归 | 网页整理、网页对比、CSV、仓库体检与验收任务成功，重启后详情与产物内容一致 |
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

预期结果：`Ran 153 tests ... OK (skipped=1)`。真实沙箱集成测试默认跳过，其余 152 项测试可在未启动 Docker 时执行。

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
| `POST` | `/tasks/{id}/retry` | 在原任务记录上重试失败、超时或已取消的任务，`attempt` 递增 |
| `POST` | `/tasks/batch-delete` | 批量删除已结束任务 |
| `POST` | `/tasks/cleanup` | 按状态和天数清理历史任务 |
| `GET` | `/tasks/{id}/artifacts` | 任务产物清单 |
| `GET` | `/tasks/{id}/artifacts/archive` | 一键打包下载，文件名形如 `web-digest_quotes.toscrape.com_20261123-2204_d999048a.zip` |
| `GET` | `/tasks/artifacts/archive?task_ids=a,b` | 批量打包下载，最多 50 个任务，按任务规范名分目录 |
| `GET` | `/artifacts/{id}/download` | 下载已登记产物，`?inline=1` 供浏览器内联显示 |
| `GET` | `/artifacts/{id}/preview` | 文本截断预览或图片预览地址 |
| `GET` | `/schedules` | 当前用户可见的定时任务 |
| `POST` | `/schedules` | 创建定时任务：`task_type`、`params`、五段 `cron`、`name` |
| `PUT` | `/schedules/{id}` | 编辑名称、类型、参数与规则 |
| `PATCH` | `/schedules/{id}` | 启用或暂停，重新启用时按当前时间计算下一次执行 |
| `DELETE` | `/schedules/{id}` | 删除定时任务，不影响已创建的历史任务 |
| `POST` | `/schedules/{id}/run` | 立即执行一次，返回新建任务 |
| `GET` | `/schedules/{id}/runs` | 该定时任务的历史执行记录 |
| `GET` | `/notifications` | 站内通知列表与未读数 |
| `POST` | `/notifications/{id}/read` | 标记单条已读 |
| `POST` | `/notifications/read-all` | 全部标记已读 |
| `GET` | `/kb` | 当前用户的知识库列表 |
| `POST` | `/kb` | 新建知识库：`{name, note}` |
| `PATCH` | `/kb/{id}` | 重命名知识库：`{name}` |
| `DELETE` | `/kb/{id}` | 删除知识库及其文档、问答历史与向量 |
| `GET/POST` | `/kb/{id}/documents` | 文档摘要列表（含 `preview`）/ 从任务或文本入库 |
| `GET` | `/tasks/{task_id}/kb-documents` | 该任务已入库到哪些知识库 |
| `POST` | `/kb/{id}/documents/batch` | 从多个任务批量入库（最多 20） |
| `DELETE` | `/kb/{id}/documents/{doc_id}` | 删除文档并清理向量 |
| `POST` | `/kb/{id}/documents/{doc_id}/refresh` | 重新切片并刷新向量 |
| `POST` | `/kb/{id}/ask` | RAG 问答：`{question, top_k}` → 回答与引用 |
| `GET` | `/kb/{id}/messages` | 该知识库的问答历史 |

## 定时任务与通知

侧栏“定时任务”进入 `/ui/schedules.html`，和工作台同一套外壳：

- 新建或编辑时选择模板、填写参数，再用“每 N 分钟 / 每小时 / 每天 / 每周”拼出 cron，下方实时显示五段表达式。
- 列表展示任务名称、类型、规则、下次执行、最近状态与操作；点击名称打开右侧抽屉查看历史执行，每条都可以跳到原任务详情。
- 调度使用 APScheduler，任务存进 SQLite 的 `schedules` 表，服务重启后继续；触发时先推进 `next_run_at` 再创建任务，避免长任务重复触发。
- 任务结束、失败或超时都会写一条站内通知；右上角铃铛显示未读数，可单条或全部标记已读。

## 知识库

侧栏“知识库”是一个独立页面，与网页资料整理同级，检索范围是当前知识库里的全部文档，可跨任务、跨批次：

- 新建知识库（按主题区分），每个账号只能看到自己的知识库，管理员也不跨用户；任务详情有“添加到知识库”，可选已有库或当场新建，并勾选是否连同对话一起存入。
- 默认不自动入库：在任务详情点“添加到知识库”，选择已有知识库或当场新建，并决定是否连同对话一起存入。一次任务只落一条会话记录（`session.md`，含结论、报告、对话与产物清单），不会按产物拆成多条；文档列表用勾选框多选、右侧 × 单个删除。删除任务不会删除文档，而是标记“来源任务已删除”。
- 入库按标题切片（约 600 字、重叠 80），同时写入 Chroma 向量与 SQLite FTS5；提问时向量与关键词各取 20 条做 RRF 融合，必要时自动改写追问。
- 支持上传 PDF / TXT / Markdown（默认 10 MB 上限），对话可导出 Markdown。顶部是 42px 高的知识库选择器与操作组（新建/重命名/删除/刷新）；宽屏为「会话 / 对话 / 文档」三栏，窄屏文档栏改为覆盖层。输入框左侧的 ＋ 用于上传，文档栏可从对话头部“文档 N”打开。
- 连续追问：回答以 SSE 流式返回，可随时停止、重生成或复制；来源在回答下方折叠展示，`[n]` 与来源一一对应。消息带角色标签与卡片边框；左侧会话栏可切换、重命名、删除会话；文档栏展示正文摘要、类型标签，支持搜索、筛选与批量删除。
- 知识库可删除：确认框展示文档数，删除会清理文档、问答历史与向量；删除后任务详情的“已添加到知识库”灰态会自动恢复。
- 计划与实现细节见 `docs/kb_upgrade_plan.md`。

## 网页精读、对比与字段采集

- **目标精读**：创建网页整理任务时选预设目标（学习笔记、步骤教程、优缺点对比、参数清单、风险局限）或自定义目标，模型按目标产出 `reading.md`，每条结论标注原文段落；正文超过 10000 字会截断并提示。
- **网页对比**：在“网页资料整理”的创建弹窗顶部切到“多页对比”，一次填 2–10 个链接，按预设或自定义维度生成一份自然语言分析报告（总体判断、关键差异、适用场景、结论），正文用 `[n]` 标注来源，资料没覆盖的地方会直接说明。产物包括 `compare.md`、`compare.json`，以及每个来源的 `source-N.md`（正文）、`source-N.png`（截图）和 `source-N.items.json`。整次对比只创建一个任务，分析由大模型完成：创建阶段未配置模型会返回 `LLM_NOT_CONFIGURED`；如果已经进入分析但模型连续请求失败，会写入降级来源摘要并标记 `degraded=true`，任务仍可成功，但没有结论和语义标题，需要修复 LLM 配置后重试。打开对比任务时右侧面板会加宽，上方是来源与结论，任务产物收进下拉浮层，下方是固定底部的对话区，开场消息会列出每个来源的标题。
- **自定义字段**：网页整理任务可声明最多 8 个字段（字段名 + 说明），结果输出 `fields.csv` / `fields.json`，缺失值统一写“无数据”；字段组合可保存为本地模板复用。
- 问答用当前知识库检索前 6 段，拼进提示词交给 LLM，回答下方可展开引用片段和原网页链接；问答历史随知识库保存。检索条数与阈值固定为默认值，界面不再提供滑块。
- 除网页任务外，也可以直接上传 PDF、TXT、Markdown 入库；PDF 用 `pypdf` 提取文字，单文件上限 10 MB。
- 问答区提供“导出对话”，把当前知识库的问答与引用整理成 Markdown 下载。
- 文档支持删除、批量删除与“刷新向量”（重新切片并重建索引）。

## AI 能力底座

四块基础设施已经就位，具体 AI 功能可以在模板里按需调用：

- **LLM 网关**：`backend/llm.py` 提供 `chat`、`chat_json`、`embed`，兼容 OpenAI 协议，带超时、重试、JSON 校验和 token 用量日志。未配置 `LLM_API_KEY` 时 `available()` 为假，调用方应降级。`LLM_TEMPERATURE` 可留空，推理类模型（如 kimi-k3）不接受该参数。
- **定时任务**：`schedules` 与 `notifications` 表随 SQLite 迁移到 schema v10；后台线程每 `SCHEDULE_POLL_SECONDS` 秒扫描一次，到期后创建普通任务并推进 `next_run_at`，进程重启不丢。
- **向量检索**：`backend/rag.py` 把文本分块、调用嵌入接口，向量写入 Chroma；SQLite FTS5 负责关键词检索，问答时两路结果用 RRF 融合，可按 `task_id` 过滤。
- **沙箱依赖**：`scripts/sandbox_extras.sh` 在容器内建独立虚拟环境并安装 `pandas`、`matplotlib`、`scikit-learn`，默认不随部署执行。

对应环境变量：`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_EMBEDDING_MODEL`、`LLM_TIMEOUT_SECONDS`、`LLM_MAX_RETRIES`、`SCHEDULE_POLL_SECONDS`、`SCHEDULE_MAX_PER_TICK`。

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

CSV 分析使用 `POST /tasks/csv` 的 multipart 表单上传，字段名为 `files`（可重复，兼容旧字段 `file`），一次 1–5 个 `.csv`，单个不超过 5 MB、总计不超过 20 MB。单个文件生成完整画像与多张图表；多个文件按数据集分别分析，详情里用 tab 切换，每个数据集生成全部可用图表。

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
  config.py          .env / 环境变量与持久化路径
  db.py              TaskStore 协议与内存测试实现
  sqlite_store.py    SQLite schema v10（任务、用户、KB、标签、调度、通知）
  scheduler.py       状态机、固定步骤调度、取消与重试
  task_chat.py       任务上下文对话与 CSV/对比问答
  csv_analyzer.py    CSV 画像、列角色、视图与图表数据
  llm.py             OpenAI 兼容 LLM/Embedding 网关与 fallback
  rag.py             切片、Chroma 向量、FTS5 与 RRF 检索
  logging_config.py  JSON Lines 结构化日志
  sandbox_client.py  Shell/File/Browser 统一调用
  routes_tasks.py    任务查询、取消/重试、清理、快照与导出
  routes_csv.py      CSV multipart 上传接口
  routes_kb.py       知识库、文档、RAG 问答与 SSE
  routes_schedules.py 定时任务与通知
  routes_tags.py     标签、批注与任务导出
  models.py          Pydantic 数据契约
  storage.py         本地任务产物存储
  templates/         网页整理/对比、CSV、仓库体检、验收模板
frontend/
  login.*            登录与首次创建管理员
  users.*            管理员账号管理页
  kb.*               知识库与 RAG 问答页
  schedules.*        定时任务页
  create_task.*      旧创建页，已改为跳转到功能页
  task_list.*        任务列表、创建弹窗、CSV/对比详情与产物预览
  vendor/            ECharts 5.5.1 本地资源与许可证
samples/             测试输入与样例
artifacts/           程序运行产物，不提交真实数据
logs/                结构化日志，不提交
scripts/
  init_db.py         schema 初始化入口（用 python -m scripts.init_db 运行）
  create_user.py     创建管理员或普通账号
  reindex_kb.py      重建知识库检索索引
  sync_document_titles.py 同步文档语义标题
  test_sandbox.py    真实沙箱冒烟测试
tests/               单元测试、进阶用例与可选沙箱集成测试
docs/                接口契约、部署与验收记录
```

## 账号与登录

- 每个任务记录归属用户（`owner_id`）。普通用户只能看到、取消、重试、下载自己的任务；管理员可以查看全部任务。
- 登录使用 HttpOnly + SameSite=Lax 的签名 Cookie，默认 7 天；密码用标准库 `scrypt` 加盐哈希，数据库里没有明文。
- 首次部署打开 `/ui/login.html`，点“创建管理员”即可；之后管理员从侧栏“账号管理”进入 `/ui/users.html` 创建普通用户或管理员，也可以运行 `python -m scripts.create_user <用户名> <密码> [--admin]`。
- 账号管理页与任务/知识库/定时任务页共用同一套外壳，管理员可以在行内重置密码或删除账号；不能删除当前登录账号，也不能删掉最后一个管理员。重置密码后旧密码立即失效，删除账号不会连带删除其历史任务。
- 生产环境必须设置 `SESSION_SECRET`，否则每次重启都会让所有会话失效；开启 HTTPS 后再把 `SESSION_COOKIE_SECURE` 设为 `true`。
- 数据库 schema 当前为 v10：包含任务/步骤/产物、用户、定时任务/通知、知识库/文档/消息/切片、标签/批注，以及 `tasks.owner_id` 数据隔离。迁移由 `backend/sqlite_store.py` 在启动时按 `PRAGMA user_version` 执行。

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

服务器部署步骤见 [docs/deployment.md](docs/deployment.md)；仓库里的脚本与文档示例可能仍以 Caddy 为例，生产服务器当前使用 nginx，反向代理配置请按实际环境替换。一键脚本：

```bash
sudo REPO_URL=git@github.com:you/BoxPilot.git bash deploy/bootstrap.sh
```

脚本会把沙箱、后端和 systemd 服务装好，默认只监听 `127.0.0.1:8000`，再由 nginx 对外提供 HTTP/HTTPS。生产环境注意：

- 环境变量放在 `/etc/boxpilot.env`，由 systemd `EnvironmentFile` 读取；源码包不包含 `.env`。
- 必须显式设置 `DATABASE_PATH=/var/lib/boxpilot/data/boxpilot.db`。不设置时默认相对路径 `data/boxpilot.db` 会落到 `/opt/boxpilot/data/`，升级后看起来像“数据库被重置”。
- 数据库、产物放在 `/var/lib/boxpilot`，不要随代码目录覆盖；升级前备份该目录。
- `SESSION_SECRET` 必须固定；HTTPS 下再设置 `SESSION_COOKIE_SECURE=true`。

## 已知限制

- SQLite 默认运行于单进程、单 Worker。重启时中断任务标记为失败，可手动重试。详见 [SQLite 集成与验收](docs/sqlite_integration.md)。
- 运行中任务的取消在步骤边界生效，正在执行的 Shell 或浏览器动作不会被强制终止。
- 当前公开仓库体检可能受沙箱网络出口影响：克隆会失败重试，装依赖也可能超时；离线演示使用 `demo://python`。
- 沙箱自带 Python 3.10 与 3.12；3.13 在任务开始时按需补装，镜像不可达时才会降级试跑并在报告里标注。
- 网页对比的结论和语义标题依赖外部模型。未配置或连续请求失败时会降级为来源摘要（`degraded=true`），不会伪造结论；修复 `/etc/boxpilot.env` 中的模型配置并重启后重试任务即可。
