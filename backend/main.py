"""文件职责：装配 BoxPilot 的 FastAPI 应用、依赖对象、公共路由和前端静态页面。

这里是后端唯一应用入口。任务存储、沙箱适配器、调度器都在应用启动时创建并挂到
app.state，便于 API 路由读取，也便于测试时注入 FakeSandbox 或临时目录。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import time
from typing import Iterable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.db import InMemoryTaskStore, TaskStore
from backend.logging_config import configure_logging
from backend.models import HealthResponse, TaskType
from backend.routes_csv import router as csv_router
from backend.routes_tasks import router as tasks_router
from backend.sandbox_client import SandboxAdapter, sandbox_adapter
from backend.scheduler import TaskRunner, build_runner
from backend.storage import ArtifactStore
from backend.templates.base import TaskTemplate
from backend.templates.csv_analysis import CsvAnalysisTemplate
from backend.templates.mock import MockTemplate
from backend.templates.repo_check import RepoCheckTemplate
from backend.templates.web_accept import WebAcceptTemplate
from backend.templates.web_collect import WebDigestTemplate


class UTF8JSONResponse(JSONResponse):
    """显式声明 UTF-8，避免旧客户端按单字节编码解析中文错误信息。"""

    media_type = "application/json; charset=utf-8"


def create_app(
    *,
    task_store: TaskStore | None = None,
    artifact_store: ArtifactStore | None = None,
    adapter: SandboxAdapter | None = None,
    templates: Iterable[tuple[TaskType, TaskTemplate]] | None = None,
) -> FastAPI:
    # 所有依赖都允许从参数注入：生产环境使用真实实现，单元测试使用轻量替身。
    selected_store = task_store or InMemoryTaskStore()
    selected_adapter = adapter or sandbox_adapter
    selected_artifacts = artifact_store or ArtifactStore(
        Path(settings.ARTIFACT_ROOT),
        max_bytes=settings.MAX_ARTIFACT_BYTES,
    )
    selected_templates = list(templates) if templates is not None else [
        (TaskType.MOCK, MockTemplate(selected_adapter)),
        (TaskType.CSV_ANALYSIS, CsvAnalysisTemplate(selected_adapter)),
        (TaskType.WEB_DIGEST, WebDigestTemplate(selected_adapter)),
        (TaskType.REPO_CHECK, RepoCheckTemplate(selected_adapter)),
        (TaskType.WEB_ACCEPTANCE, WebAcceptTemplate(selected_adapter)),
    ]
    selected_runner = build_runner(selected_store, selected_artifacts, selected_adapter, selected_templates)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 启动时准备产物根目录，并把共享对象放入 app.state，避免路由自行创建全局状态。
        configure_logging()
        selected_artifacts.root.mkdir(parents=True, exist_ok=True)
        app.state.task_store = selected_store
        app.state.artifact_store = selected_artifacts
        app.state.sandbox_adapter = selected_adapter
        app.state.task_runner = selected_runner
        yield

    app = FastAPI(
        title=settings.PROJECT_NAME,
        description="BoxPilot task execution workbench",
        version=settings.PROJECT_VERSION,
        lifespan=lifespan,
        default_response_class=UTF8JSONResponse,
    )
    app.include_router(tasks_router)
    app.include_router(csv_router)
    # 前端使用原生静态文件，不需要额外启动 Node 服务；访问 /ui 即可打开工作台。
    frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
    if frontend_dir.is_dir():
        app.mount("/ui", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    @app.middleware("http")
    async def disable_ui_cache(request: Request, call_next):
        # 页面和脚本改动后必须立刻生效，否则用户会看到旧表单、提交出缺参数的任务。
        response = await call_next(request)
        if request.url.path.startswith("/ui/"):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response

    # 仅限制“创建任务”的入口；列表轮询和健康检查不占用额度。
    _rate_buckets: dict[str, tuple[float, int]] = {}

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        if settings.RATE_LIMIT_PER_MINUTE > 0 and request.method == "POST" and request.url.path in {
            "/tasks",
            "/tasks/csv",
        }:
            client = request.client.host if request.client else "unknown"
            now = time.monotonic()
            window_start, count = _rate_buckets.get(client, (now, 0))
            if now - window_start >= 60:
                window_start, count = now, 0
            count += 1
            _rate_buckets[client] = (window_start, count)
            if count > settings.RATE_LIMIT_PER_MINUTE:
                retry_after = max(1, int(60 - (now - window_start)))
                return UTF8JSONResponse(
                    status_code=429,
                    content={
                        "detail": {
                            "code": "RATE_LIMITED",
                            "message": "请求过于频繁，请稍后再试",
                        }
                    },
                    headers={"Retry-After": str(retry_after)},
                )
        return await call_next(request)

    @app.get("/")
    def read_root() -> RedirectResponse:
        return RedirectResponse("/ui/task_list.html")

    @app.get("/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        # 健康检查同时探测沙箱；沙箱不可用时返回 degraded，而不是让整个进程崩溃。
        sandbox = request.app.state.sandbox_adapter
        sandbox_status = sandbox.health()
        overall = "ok" if sandbox_status["success"] else "degraded"
        return HealthResponse(status=overall, app=settings.PROJECT_VERSION, sandbox=sandbox_status, store="memory")

    return app


app = create_app()
