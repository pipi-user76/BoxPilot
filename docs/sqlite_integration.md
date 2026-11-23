# SQLite 集成与验收

正式入口默认使用 SQLite，默认位置为 `data/boxpilot.db`。启动时自动建表，也可显式初始化。任务、步骤、产物索引分别写入三张表。筛选、去重、关联用独立列，模型的其余字段保存在每条记录的 JSON 中。产物文件仍在配置的 `ARTIFACT_ROOT` 下，备份或恢复要同时保留数据库和产物目录。

数据库启用 WAL 和外键，清理任务时级联清理关联步骤和产物索引，已有清理路由负责清理磁盘文件。创建任务与去重判断使用同一个写事务。以单进程、单 Worker 启动。上次未完成的任务会被标记为 `FAILED / SERVICE_RESTARTED`，可从现有入口手动重试。三张表符合申报书范围。业务日志继续写到 JSONL 文件。

陈咏忻原 Demo 和曾雨格原始压缩包可独立保留。集成版默认创建独立数据库，原 Demo 历史记录结构与统一模型不同，当前无需迁移演示记录。初始化会拒绝覆盖已有的未知表结构。

## 本机启动

在集成目录执行。如果容器已使用其他端口，设置对应的 `SANDBOX_URL`。首次使用可执行根目录的 `start_sandbox.bat`，将沙箱启动在 8080 端口。下例使用已存在且映射到 18080 的 `boxpilot-aio-demo` 容器；首次启动时将对应启动命令和 URL 换成自己的配置。

```powershell
# 先在终端进入解压后的 BoxPilot 目录
uv sync --locked
docker --context desktop-linux start boxpilot-aio-demo
$env:SANDBOX_URL = 'http://127.0.0.1:18080'
uv run --locked python -m scripts.init_db
uv run --locked uvicorn main:app --host 127.0.0.1 --port 8770
```

打开 `http://127.0.0.1:8770/`。健康检查 `/health` 应返回 `store: sqlite`。停止并重启同一个命令即可检查任务保留。

## 回归和真实沙箱复核

```powershell
$env:SANDBOX_URL = 'http://127.0.0.1:18080'
$env:BOXPILOT_RUN_SANDBOX_TESTS = '1'
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m scripts.verify_sqlite_aio
```

自动化测试覆盖数据库外键、持久化、中断任务收尾和并发去重。真实任务复核脚本启动两个先后独立的 Uvicorn 进程，使用临时数据库和产物目录。脚本通过 HTTP 提交四类任务和非法 CSV，验证旧任务详情、步骤、产物下载及下载内容哈希在重启前后一致。仓库体检使用内置示例，两个网页模板使用 example.com。

## 2026 年 11 月 16 日真实容器验收结果

- 本机开启真实 AIO 测试，70 项自动化测试全部通过。
- 四类正常任务成功，非法 CSV 正确失败。重新启动独立 Uvicorn 进程后，5 条任务详情完全一致，10 份产物的下载 SHA-256 一致。
- 验收库记录 5 条任务、22 条步骤、10 条产物，SQLite 完整性和外键检查通过。
