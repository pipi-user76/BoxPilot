# BoxPilot 项目框架

本框架按照 2026-11-04 最新版《BoxPilot 项目一个月双人开发详细计划》调整。

## 目录结构

```text
BoxPilot/
├── backend/
│   ├── main.py                 FastAPI 应用、静态页面挂载和公共路由装配
│   ├── db.py                   SQLite 连接、TaskStore 契约和内存实现
│   ├── scheduler.py            状态流转、固定步骤调度、取消与重试
│   ├── logging_config.py       JSON Lines 结构化日志
│   ├── sandbox_client.py       Shell / File / Browser（含 Readability 正文）统一调用
│   ├── routes_tasks.py         任务查询分页、取消/重试、批量清理和产物预览
│   ├── routes_csv.py           CSV multipart 上传与参数校验
│   ├── models.py               Pydantic 数据模型
│   ├── storage.py              任务产物存储
│   ├── config.py               运行配置
│   ├── validation.py           路径与 URL 校验
│   └── templates/
│       ├── base.py             模板公共契约（含 StepOutcome.result）
│       ├── csv_analysis.py     陈咏忻负责
│       ├── web_collect.py      陈咏忻负责
│       ├── repo_check.py       曾雨格负责
│       ├── web_accept.py       曾雨格负责
│       └── mock.py             开发期调度验证模板
├── frontend/
│   ├── create_task.html        创建任务页面，陈咏忻负责
│   ├── create_task.js          创建任务交互，陈咏忻负责
│   ├── task_list.html          任务工作台，曾雨格负责
│   ├── task_list.js            搜索分页、批量清理、取消/重试、预览，曾雨格负责
│   └── styles.css              共用样式
├── tests/                      单元测试、进阶用例和真实沙箱集成测试
│   └── support.py              工作区临时目录辅助（避开 Windows 沙箱权限限制）
├── samples/                    样例输入
├── artifacts/                  程序运行产物
├── logs/                       结构化日志输出（不提交）
├── docs/                       接口、框架和验收文档
├── main.py                     兼容入口，指向 backend.main
└── README.md                   启动与使用说明
```

## 入口与路径

- 后端入口：`backend.main:app`
- 根目录 `main.py`：仅保留兼容导出，避免出现第二套实现
- 任务工作台：`/ui/task_list.html`
- 创建任务页：`/ui/create_task.html`
- API 文档：`/docs`
- 本地产物根目录：`artifacts/`
- 日志文件：`logs/boxpilot.jsonl`
- 沙箱任务目录：`/home/gem/boxpilot/<task_id>`

## 当前状态

- 已迁移原 `app/` 下的沙箱、调度、任务接口、存储和测试功能。
- 旧 `app/` 包已删除，不再保留双入口。
- `MOCK` 模板可完整执行并下载产物。
- `WEB_DIGEST`、`CSV_ANALYSIS`、`REPO_CHECK`、`WEB_ACCEPTANCE` 四个正式模板均已接入统一调度器。
- `routes_csv.py` 已实现 multipart CSV 上传、大小/扩展名校验和统一调度接入。
- 任务支持取消、失败重试、批量删除和历史清理；运行中取消在步骤边界生效。
- 列表查询改为服务端分页与搜索，产物支持文本/图片预览和内联下载。
- `WEB_DIGEST` 使用沙箱 Readability 提取正文，输出 `article.md` 和整页截图。
- 调度器输出 JSON Lines 日志，前端展示“任务结论”和重试来源。
- 自动化测试 42 项，其中真实沙箱集成测试默认跳过，需显式开启。
