# BoxPilot 项目进展报告

- 日期：2026-11-06
- 负责人：曾雨格
- 本阶段目标：完成 `WEB_DIGEST`、`CSV_ANALYSIS` 接口、模板注册和真实运行验证
- 当前状态：四个正式模板均已注册并通过测试

## 1. 完成内容

### 1.1 网页资料整理 WEB_DIGEST

实现了完整的网页资料整理流程：

1. 校验 HTTP/HTTPS URL。
2. 使用统一 SandboxAdapter 打开页面。
3. 提取当前页面可见文本。
4. 保存页面截图。
5. 写入网页元数据报告。

生成产物：

- `page.txt`：页面可见文本。
- `screenshot.png`：页面截图。
- `web-digest.json`：URL、标题、HTTP 状态、加载耗时和文本长度。

错误处理：

- URL 不合法时任务失败并返回 `VALIDATION_ERROR`。
- 页面无法打开时保留沙箱错误码。
- 页面文本为空时返回 `ARTIFACT_EMPTY`，避免生成伪成功任务。
- 截图文件为 0 字节时不会登记为有效产物。

### 1.2 CSV 数据分析 CSV_ANALYSIS

实现了完整的 CSV 上传与分析流程：

1. 通过 `POST /tasks/csv` 接收 multipart 文件。
2. 校验 `.csv` 扩展名、空文件和 5 MB 大小限制。
3. 将文件保存到任务目录的 `input.csv`。
4. 上传固定分析脚本到沙箱。
5. 执行 `python analyze_csv.py input.csv summary.json`。
6. 下载并解析 JSON 摘要。
7. 生成人类可读的文本报告。

生成产物：

- `summary.json`：行列数、列名、类型、缺失值、重复行和数值统计。
- `report.txt`：便于现场查看的文本报告。

分析脚本只使用 Python 标准库，不依赖 pandas，避免额外安装和网络问题。

### 1.3 调度注册与接口约束

- 在 `backend/main.py` 注册 `WEB_DIGEST` 和 `CSV_ANALYSIS`。
- 四个正式模板现在全部通过统一 TaskRunner 执行。
- CSV 任务必须使用 `POST /tasks/csv`；通过普通 JSON 接口提交会返回 `422 CSV_UPLOAD_REQUIRED`。
- 新增 `python-multipart==0.0.33` 依赖并更新 `uv.lock`。
- 所有新增代码均包含中文文件职责和关键设计注释。

## 2. 真实运行验证

### 网页资料整理

目标页面：`http://host.docker.internal:8000/ui/task_list.html`

结果：

- 任务状态：`SUCCESS`
- 产物：`page.txt`、`screenshot.png`、`web-digest.json`
- 页面文本提取成功，截图非空。

### CSV 数据分析

输入：

```csv
name,score,group
A,10,x
B,20,x
B,20,x
C,,y
```

结果：

- 任务状态：`SUCCESS`
- 行数：4
- 列数：3
- 重复行：1
- `score` 缺失值：1
- 产物：`summary.json`、`report.txt`

## 3. 自动化验证

- Python 全量测试：28 项通过。
- 真实 AIO Sandbox 集成测试：通过。
- CSV multipart 上传：通过。
- CSV 标准库分析脚本：通过。
- 网页资料整理三产物：通过。
- 原有仓库体检与网页验收回归：通过。
- 前端字段切换和 JavaScript 语法：通过。

## 4. 后续运行步骤

1. 启动 Docker Desktop。
2. 启动 AIO Sandbox：`.\start_sandbox.bat`。
3. 安装依赖：`uv sync`。
4. 启动后端：

```powershell
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

5. 打开创建任务页面：`http://127.0.0.1:8000/ui/create_task.html`。
6. 选择“网页资料整理”并填写 URL，或选择“CSV 数据分析”并上传 CSV。
7. 在任务工作台查看状态、步骤和下载产物。

## 5. 已知限制

- 当前任务和步骤仍保存在内存中，后端重启后记录丢失。
- 当前沙箱访问公开 GitHub 仓库可能出现 TLS 握手失败，仓库演示应优先使用 `demo://python`。
- 网页资料整理提取的是页面可见文本，不执行复杂正文清洗或 LLM 摘要。
- CSV 分析使用标准库，支持规则统计，不包含 pandas 高级数据清洗或图表。

## 6. 结论

本阶段完成了此前缺失的 `WEB_DIGEST` 和 `CSV_ANALYSIS` 功能，已将两个模板注册到统一调度器，并通过真实 HTTP、真实沙箱和自动化测试验证。BoxPilot 当前四个正式任务模板均具备最小可用执行闭环，下一阶段重点是 SQLite 持久化和端到端演示材料。
