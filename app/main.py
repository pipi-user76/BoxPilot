from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterable

from fastapi import FastAPI, Request

from app.adapters.sandbox import SandboxAdapter, sandbox_adapter
from app.api.endpoints.tasks import router as tasks_router
from app.core.config import settings
from app.executor.runner import TaskRunner, build_runner
from app.executor.templates.base import TaskTemplate
from app.executor.templates.mock import MockTemplate
from app.schemas.task import HealthResponse, TaskType
from app.services.artifact_store import ArtifactStore
from app.services.task_store import InMemoryTaskStore, TaskStore


def create_app(
    *,
    task_store: TaskStore | None = None,
    artifact_store: ArtifactStore | None = None,
    adapter: SandboxAdapter | None = None,
    templates: Iterable[tuple[TaskType, TaskTemplate]] | None = None,
) -> FastAPI:
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

    @app.get("/")
    def read_root() -> dict[str, str]:
        return {"message": "Welcome to BoxPilot", "docs": "/docs"}

    @app.get("/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        sandbox = request.app.state.sandbox_adapter
        sandbox_status = sandbox.health()
        overall = "ok" if sandbox_status["success"] else "degraded"
        return HealthResponse(status=overall, app=settings.PROJECT_VERSION, sandbox=sandbox_status, store="memory")

    return app


app = create_app()
