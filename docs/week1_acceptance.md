# 第 1 周沙箱与后端验收记录

日期：2026-11-04。负责人：曾雨格。

## 环境

- AIO Sandbox：v1.11.0
- 服务地址：`http://127.0.0.1:8080`
- 沙箱系统：Ubuntu 22.04.5 LTS
- 沙箱用户：`gem`
- 可写工作区：`/home/gem`
- 项目 Python：3.13.3

## 沙箱能力验证

| 检查项 | 结果 | 证据 |
|---|---|---|
| Shell 执行 | 通过 | `pwd`、`mkdir`、`python --version` 返回退出码 0 |
| 文件写入 | 通过 | 写入 `/home/gem/boxpilot/smoke/sdk_test.txt`，返回 19 bytes |
| 文件读取 | 通过 | 读回 `BoxPilot smoke test` |
| 浏览器导航 | 通过 | 打开 `http://127.0.0.1:8080`，标题为 `AIO Sandbox`，HTTP 200 |
| 页面文本 | 通过 | 成功提取页面可见文本 |
| 页面截图 | 通过 | 生成 `artifacts/smoke/smoke.png`，截图非空 |

## 后端端到端验证

使用全新内存任务存储执行 `MOCK` 任务：

1. `GET /health` 返回 200，状态为 `ok`。
2. `POST /tasks` 返回 202，初始状态为 `PENDING`。
3. 后台调度后任务状态为 `SUCCESS`。
4. 三个步骤均为 `SUCCESS`：`prepare_workspace`、`sandbox_echo`、`archive_result`。
5. 生成 `mock-result.txt`，大小 54 bytes。
6. `GET /artifacts/{id}/download` 返回 200，内容为预期文本。

同一测试使用全新应用实例重复执行，结果一致。

## 当前边界

- `InMemoryTaskStore` 仅用于第 1 周契约验证，服务重启后数据丢失。
- 当前只注册 `MOCK` 模板，正式四模板尚未接入。
- 沙箱黑名单只作为基础防线，不等同于完整不可信代码隔离。
