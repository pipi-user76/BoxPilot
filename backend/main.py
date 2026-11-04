"""文件职责：装配 BoxPilot 的 FastAPI 应用、依赖对象、公共路由和前端静态页面。

这里是后端唯一应用入口。任务存储、沙箱适配器、调度器都在应用启动时创建并挂到
app.state，便于 API 路由读取，也便于测试时注入 FakeSandbox 或临时目录。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterable

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.db import InMemoryTaskStore, TaskStore
from backend.models import HealthResponse, TaskType
from backend.routes_csv import router as csv_router
from backend.routes_tasks import router as tasks_router
from backend.sandbox_client import SandboxAdapter, sandbox_adapter
from backend.scheduler import TaskRunner, build_runner
from backend.storage import ArtifactStore
from backend.templates.base import TaskTemplate
from backend.templates.mock import MockTemplate


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
    ]
    selected_runner = build_runner(selected_store, selected_artifacts, selected_adapter, selected_templates)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 启动时准备产物根目录，并把共享对象放入 app.state，避免路由自行创建全局状态。
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
    )
    app.include_router(tasks_router)
    app.include_router(csv_router)
    # 前端使用原生静态文件，不需要额外启动 Node 服务；访问 /ui 即可打开工作台。
    frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
    if frontend_dir.is_dir():
        app.mount("/ui", StaticFiles(directory=frontend_dir, html=True), name="frontend")

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
