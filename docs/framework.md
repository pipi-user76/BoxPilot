# BoxPilot 项目框架

本框架按照 2026-11-04 最新版《BoxPilot 项目一个月双人开发详细计划》调整。

## 目录结构

```text
BoxPilot/
├── backend/
│   ├── main.py                 FastAPI 应用、静态页面挂载和公共路由装配
│   ├── db.py                   SQLite 连接、TaskStore 契约和内存实现
│   ├── scheduler.py            状态流转、固定步骤调度
│   ├── sandbox_client.py       Shell / File / Browser 统一调用
│   ├── routes_tasks.py         任务查询、步骤和产物下载接口
│   ├── routes_csv.py           CSV 上传接口占位，陈咏忻负责
│   ├── models.py               Pydantic 数据模型
│   ├── storage.py              任务产物存储
│   ├── config.py               运行配置
│   ├── validation.py           路径与 URL 校验
│   └── templates/
│       ├── base.py             模板公共契约
│       ├── csv_analysis.py     陈咏忻负责
│       ├── web_collect.py      陈咏忻负责
│       ├── repo_check.py       曾雨格负责
│       ├── web_accept.py       曾雨格负责
│       └── mock.py             开发期调度验证模板
├── frontend/
│   ├── create_task.html        创建任务页面，陈咏忻负责
│   ├── create_task.js          创建任务交互，陈咏忻负责
│   ├── task_list.html          任务工作台，曾雨格负责
│   ├── task_list.js            轮询、详情和下载，曾雨格负责
│   └── styles.css              共用样式
├── tests/                      单元测试和真实沙箱集成测试
├── samples/                    样例输入
├── artifacts/                  程序运行产物
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
- 沙箱任务目录：`/home/gem/boxpilot/<task_id>`

## 当前状态

- 已迁移原 `app/` 下的沙箱、调度、任务接口、存储和测试功能。
- 旧 `app/` 包已删除，不再保留双入口。
- `MOCK` 模板可完整执行并下载产物。
- 四个正式模板文件和 API 占位已建立，具体业务由对应负责人继续实现。
- `routes_csv.py` 当前返回 `CSV_ROUTE_NOT_IMPLEMENTED`，等待陈咏忻接入。
