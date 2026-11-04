# BoxPilot 人工测试说明

本文用于开发人员在不依赖自动化测试的情况下，检查沙箱、后端、前端和错误处理是否正常。

## 1. 测试前准备

1. 启动 Docker Desktop，等待 Docker 服务进入运行状态。
2. 在项目根目录运行 `.\start_sandbox.bat`，确认 AIO Sandbox 已启动。
3. 启动后端：

```powershell
./.venv/Scripts/python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

4. 打开 `http://127.0.0.1:8000/health`，应看到 `status` 为 `ok`，且 `sandbox.success` 为 `true`。
5. 打开 `http://127.0.0.1:8000/docs`，确认 Swagger 能正常加载。

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
6. 检查响应中三个步骤：`prepare_workspace`、`sandbox_echo`、`archive_result` 都应为 `SUCCESS`。
7. 检查 `artifacts` 中至少有一个 `mock-result.txt`。
8. 调用 `GET /artifacts/{artifact_id}/download`，预期下载文本：

```text
BoxPilot scheduler smoke test completed successfully.
```

## 4. 测试任务工作台

打开 `http://127.0.0.1:8000/ui/task_list.html`。

检查：

- 页面标题显示“任务工作台”。
- 右上角健康状态显示“环境正常”。
- 任务列表显示刚才创建的 `MOCK` 任务。
- 点击任务后，右侧显示任务 ID、创建时间、开始时间、结束时间和三个步骤。
- 产物区出现“下载”按钮，点击后能下载 `mock-result.txt`。
- 将状态筛选切换到“成功”，任务仍显示；切换到“失败”，任务列表为空并显示空状态。

## 5. 测试创建任务页面

打开 `http://127.0.0.1:8000/ui/create_task.html`。

检查字段切换：

- 选择“网页资料整理”时，只显示网页地址。
- 选择“CSV 数据分析”时，只显示 CSV 文件上传。
- 选择“代码仓库体检”时，只显示仓库地址。
- 选择“网页应用验收”时，显示网页地址和预期标题。

当前正式模板尚未注册，提交正式任务应返回明确的 `TEMPLATE_NOT_AVAILABLE` 或 `CSV_ROUTE_NOT_IMPLEMENTED`，不能显示伪成功。

## 6. 测试异常与安全场景

### 未注册模板

在 Swagger 调用 `POST /tasks`：

```json
{
  "task_type": "WEB_DIGEST",
  "params": {"url": "https://example.com"}
}
```

预期返回 `422`，错误码为 `TEMPLATE_NOT_AVAILABLE`。

### CSV 占位接口

在 Swagger 调用 `POST /tasks/csv`，预期返回 `501`，错误码为 `CSV_ROUTE_NOT_IMPLEMENTED`。

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

## 7. 页面响应式检查

在浏览器开发者工具中切换宽度：

- 1280 px：任务列表和详情栏左右排列。
- 800 px：详情栏移动到任务列表下方，页面不应出现横向滚动条。
- 620 px 以下：概览区和任务行切换为单列布局。

同时检查按钮文字不溢出、任务状态不遮挡任务名称、下载按钮仍可点击。

## 8. 自动化回归

无沙箱单元测试：

```powershell
./.venv/Scripts/python.exe -m unittest discover -s tests -v
```

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

## 9. 人工测试记录模板

| 编号 | 场景 | 操作 | 预期 | 实际 | 通过 |
|---|---|---|---|---|---|
| M01 | 沙箱冒烟 | 运行 `scripts/test_sandbox.py` | 全部检查成功 |  |  |
| M02 | 健康检查 | 打开 `/health` | 状态 `ok` |  |  |
| M03 | 创建任务 | Swagger 调用 `POST /tasks` | 返回 `202` |  |  |
| M04 | 任务完成 | 查询任务详情 | 状态 `SUCCESS`，3 个步骤成功 |  |  |
| M05 | 产物下载 | 点击下载按钮 | 文件内容正确 |  |  |
| M06 | 异常模板 | 提交 `WEB_DIGEST` | 返回 `422` |  |  |
| M07 | CSV 占位 | 调用 `POST /tasks/csv` | 返回 `501` |  |  |
| M08 | 危险命令 | 执行拦截测试 | 返回 `COMMAND_BLOCKED` |  |  |
| M09 | 窄屏页面 | 800 px 宽度 | 无横向溢出 |  |  |
