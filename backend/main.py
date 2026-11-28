"""文件职责：装配 BoxPilot 的 FastAPI 应用、依赖对象、公共路由和前端静态页面。

这里是后端唯一应用入口。任务存储、沙箱适配器、调度器都在应用启动时创建并挂到
app.state，便于 API 路由读取，也便于测试时注入 FakeSandbox 或临时目录。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import secrets
import time
from typing import Iterable
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.auth import SESSION_COOKIE, read_session
from backend.auth_store import AuthStore, InMemoryAuthStore, SQLiteAuthStore
from backend.config import settings
from backend.db import TaskStore
from backend.sqlite_store import SQLiteTaskStore
from backend.logging_config import configure_logging
from backend.models import HealthResponse, TaskType, UserRecord
from backend.kb import ChromaIndex, KnowledgeBaseService
from backend.task_chat import TaskChatService
from backend.llm import LLMClient, llm_client
from backend.routes_auth import router as auth_router
from backend.routes_csv import router as csv_router
from backend.routes_kb import router as kb_router
from backend.routes_tags import router as tags_router
from backend.routes_notifications import router as notifications_router
from backend.routes_schedules import router as schedules_router
from backend.routes_tasks import router as tasks_router
from backend.sandbox_client import SandboxAdapter, sandbox_adapter
from backend.scheduler import TaskRunner, build_runner
from backend.schedules import ScheduleRunner
from backend.storage import ArtifactStore
from backend.templates.base import TaskTemplate
from backend.templates.csv_analysis import CsvAnalysisTemplate
from backend.templates.mock import MockTemplate
from backend.templates.repo_check import RepoCheckTemplate
from backend.templates.web_accept import WebAcceptTemplate
from backend.templates.web_collect import WebDigestTemplate
from backend.templates.web_compare import WebCompareTemplate


class UTF8JSONResponse(JSONResponse):
    """显式声明 UTF-8，避免旧客户端按单字节编码解析中文错误信息。"""

    media_type = "application/json; charset=utf-8"


def create_app(
    *,
    task_store: TaskStore | None = None,
    database_path: Path | None = None,
    artifact_store: ArtifactStore | None = None,
    adapter: SandboxAdapter | None = None,
    auth_store: AuthStore | None = None,
    auth_required: bool | None = None,
    templates: Iterable[tuple[TaskType, TaskTemplate]] | None = None,
) -> FastAPI:
    # 所有依赖都允许从参数注入：生产环境使用真实实现，单元测试使用轻量替身。
    selected_store = task_store if task_store is not None else SQLiteTaskStore(database_path or settings.DATABASE_PATH)
    if auth_store is not None:
        selected_auth = auth_store
    elif isinstance(selected_store, SQLiteTaskStore):
        selected_auth = SQLiteAuthStore(selected_store.path)
    else:
        selected_auth = InMemoryAuthStore()
    enforce_auth = settings.AUTH_REQUIRED if auth_required is None else auth_required
    # 关闭认证时（仅测试或本地单用户）给一个合成管理员，路由仍走同一套归属代码。
    local_user = UserRecord(id="local", username="local", is_admin=True)
    # 未显式配置时随机生成；重启即失效，生产环境应通过 SESSION_SECRET 固定。
    session_secret = settings.SESSION_SECRET or secrets.token_urlsafe(32)
    selected_adapter = adapter or sandbox_adapter
    selected_artifacts = artifact_store or ArtifactStore(
        Path(settings.ARTIFACT_ROOT),
        max_bytes=settings.MAX_ARTIFACT_BYTES,
    )
    selected_templates = list(templates) if templates is not None else [
        (TaskType.MOCK, MockTemplate(selected_adapter)),
        (TaskType.CSV_ANALYSIS, CsvAnalysisTemplate(selected_adapter, llm=llm_client)),
        (TaskType.WEB_DIGEST, WebDigestTemplate(selected_adapter)),
        (TaskType.WEB_COMPARE, WebCompareTemplate(selected_adapter)),
        (TaskType.REPO_CHECK, RepoCheckTemplate(selected_adapter)),
        (TaskType.WEB_ACCEPTANCE, WebAcceptTemplate(selected_adapter)),
    ]
    selected_runner = build_runner(selected_store, selected_artifacts, selected_adapter, selected_templates)
    selected_schedules: ScheduleRunner | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 启动时准备产物根目录，并把共享对象放入 app.state，避免路由自行创建全局状态。
        configure_logging()
        if isinstance(selected_store, SQLiteTaskStore):
            selected_store.init_db()
            selected_store.recover_interrupted()
        selected_artifacts.root.mkdir(parents=True, exist_ok=True)
        app.state.task_store = selected_store
        app.state.auth_store = selected_auth
        app.state.session_secret = session_secret
        app.state.auth_required = enforce_auth
        app.state.local_user = local_user
        app.state.artifact_store = selected_artifacts
        app.state.sandbox_adapter = selected_adapter
        app.state.task_runner = selected_runner
        app.state.templates = dict(selected_templates)
        # 知识库：Chroma 不可用时降级为“只存文档、不提供问答”，不影响其余功能。
        try:
            kb_index = ChromaIndex(settings.CHROMA_PATH)
        except Exception:  # pragma: no cover - 环境缺依赖时走降级
            kb_index = None
        kb_answer_llm = LLMClient(model=settings.KB_LLM_MODEL) if settings.KB_LLM_MODEL and settings.KB_LLM_MODEL != settings.LLM_MODEL else None
        app.state.kb_service = KnowledgeBaseService(
            selected_store, selected_artifacts, llm_client, kb_index, kb_answer_llm,
        )
        app.state.kb_index = kb_index
        selected_runner.kb = app.state.kb_service
        app.state.task_chat = TaskChatService(selected_store, selected_artifacts, llm_client)
        # 定时任务线程随应用启动/停止，避免测试或重启后残留后台扫描。
        nonlocal selected_schedules
        selected_schedules = ScheduleRunner(selected_store, selected_runner)
        selected_schedules.start()
        app.state.schedule_runner = selected_schedules
        try:
            yield
        finally:
            selected_schedules.stop()

    app = FastAPI(
        title=settings.PROJECT_NAME,
        description="BoxPilot task execution workbench",
        version=settings.PROJECT_VERSION,
        lifespan=lifespan,
        default_response_class=UTF8JSONResponse,
    )
    app.include_router(tasks_router)
    app.include_router(csv_router)
    app.include_router(kb_router)
    app.include_router(tags_router)
    app.include_router(auth_router)
    app.include_router(schedules_router)
    app.include_router(notifications_router)
    # 中间件可能早于 lifespan 读取，先在对象上放一份；lifespan 会覆盖为同一值。
    app.state.session_secret = session_secret
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
    _login_buckets: dict[str, tuple[float, int]] = {}

    def _hit(key: str, buckets: dict[str, tuple[float, int]], limit: int) -> int | None:
        """滑动一分钟计数；超限时返回需要等待的秒数。"""
        now = time.monotonic()
        window_start, count = buckets.get(key, (now, 0))
        if now - window_start >= 60:
            window_start, count = now, 0
        count += 1
        buckets[key] = (window_start, count)
        if limit and count > limit:
            return max(1, int(60 - (now - window_start)))
        return None

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        client = request.client.host if request.client else "unknown"
        retry_after = None
        if request.method == "POST" and request.url.path in {"/tasks", "/tasks/csv"}:
            retry_after = _hit(client, _rate_buckets, settings.RATE_LIMIT_PER_MINUTE)
        elif request.method == "POST" and request.url.path == "/auth/login":
            # 登录单独计数，避免影响正常创建任务的额度。
            retry_after = _hit(f"login:{client}", _login_buckets, 10)
        if retry_after is not None:
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

    @app.middleware("http")
    async def require_auth(request: Request, call_next):
        """登录校验放在最外层：未登录既看不到页面，也调不到任务接口。"""
        path = request.url.path
        if request.method == "OPTIONS":
            return await call_next(request)
        if not enforce_auth:
            request.state.user = local_user
            return await call_next(request)
        token = request.cookies.get(SESSION_COOKIE)
        user_id = read_session(token, session_secret)
        user = selected_auth.get_user(user_id) if user_id else None
        if user is not None:
            request.state.user = user
        public = (
            path in {"/", "/health", "/docs", "/openapi.json", "/redoc", "/favicon.ico",
                     "/auth/login", "/auth/register", "/auth/logout", "/auth/me", "/auth/bootstrap"}
            or path.startswith("/ui/login")
            # 静态资源本身不含数据，登录页也必须能加载自己的 css/js，否则会白屏。
            or (path.startswith("/ui/") and not path.endswith((".html", "/")))
        )
        if public:
            return await call_next(request)
        if user is None:
            if path.startswith("/ui/"):
                target = "/ui/login.html"
                if path != "/ui/task_list.html":
                    target += f"?next={quote(path)}"
                return RedirectResponse(target, status_code=303)
            return UTF8JSONResponse(
                status_code=401,
                content={"detail": {"code": "UNAUTHORIZED", "message": "请先登录"}},
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
        return HealthResponse(status=overall, app=settings.PROJECT_VERSION, sandbox=sandbox_status, store=getattr(selected_store, "name", "custom"))

    return app


app = create_app()
