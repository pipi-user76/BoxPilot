"""文件职责：定义 API、调度器和前端共享的数据模型与状态枚举。

所有时间使用 UTC，任务和步骤 ID 使用 UUID。后续 SQLite 实现应保持这些字段语义
不变，避免 API、调度器和数据库三处出现不同的状态名称。
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskType(str, Enum):
    """任务模板类型；正式模板接入后才会注册到调度器。"""
    MOCK = "MOCK"
    CSV_ANALYSIS = "CSV_ANALYSIS"
    WEB_DIGEST = "WEB_DIGEST"
    WEB_COMPARE = "WEB_COMPARE"
    REPO_CHECK = "REPO_CHECK"
    WEB_ACCEPTANCE = "WEB_ACCEPTANCE"


class TaskStatus(str, Enum):
    """任务级状态；前端轮询与数据库状态字段共用这一套值。"""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


TERMINAL_TASK_STATUSES = frozenset(
    {TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED}
)


class StepStatus(str, Enum):
    """步骤级状态；失败步骤必须保留错误码和信息。"""
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class CreateTaskRequest(BaseModel):
    """POST /tasks 的请求体；params 由各模板自行解释。"""
    task_type: TaskType
    params: dict[str, Any] = Field(default_factory=dict)


class TaskStep(BaseModel):
    """任务步骤快照，包含耗时、输出摘要和失败原因。"""
    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    step_index: int
    name: str
    status: StepStatus = StepStatus.RUNNING
    output: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    duration_ms: int | None = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None


class Artifact(BaseModel):
    """已登记产物；relative_path 不允许暴露宿主绝对路径。"""
    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    filename: str
    relative_path: str
    content_type: str
    size_bytes: int
    created_at: datetime = Field(default_factory=utc_now)


class TaskRecord(BaseModel):
    """任务完整记录，详情接口会把 steps 和 artifacts 一并返回。"""
    id: str = Field(default_factory=lambda: str(uuid4()))
    task_type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    attempt: int = 1
    retry_of: str | None = None
    # 归属用户；历史任务没有归属时只有管理员可见。
    owner_id: str | None = None
    # 幂等键在窗口内唯一；deduplicated 只在响应中标记，表示命中已有任务而未新建。
    dedupe_key: str | None = None
    # 列表展示用，非持久化字段：由路由按任务批量补上标签名。
    tags: list[str] = Field(default_factory=list)
    deduplicated: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    steps: list[TaskStep] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)


class UserRecord(BaseModel):
    """最小用户模型；密码哈希只在存储层出现，不进入任何响应。"""
    id: str = Field(default_factory=lambda: str(uuid4()))
    username: str
    is_admin: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    last_login_at: datetime | None = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=256)


class NewUserRequest(RegisterRequest):
    """管理员创建账号时可以顺带指定角色。"""

    is_admin: bool = False


class PasswordResetRequest(BaseModel):
    """管理员重置密码：只接收新密码，不校验旧密码。"""

    password: str = Field(min_length=8, max_length=256)


class AuthResponse(BaseModel):
    user: UserRecord
    expires_at: datetime


class TaskListResponse(BaseModel):
    """分页任务列表；counts 统计全部任务，不受当前筛选条件影响。"""
    items: list[TaskRecord]
    total: int
    page: int
    page_size: int
    pages: int
    counts: dict[str, int]


class BatchDeleteRequest(BaseModel):
    """批量清理指定任务；运行中的任务会被跳过而不是强制终止。"""
    task_ids: list[str] = Field(min_length=1, max_length=200)


class BatchCreateRequest(BaseModel):
    """批量订阅：一行一个目标地址，统一带上批次名称。"""
    task_type: TaskType
    targets: list[str] = Field(min_length=1, max_length=20)
    batch_name: str = Field(default="", max_length=40)


class BatchCreateItem(BaseModel):
    target: str
    task_id: str | None = None
    deduplicated: bool = False
    error: str | None = None


class BatchCreateResponse(BaseModel):
    batch_id: str
    batch_name: str
    created: int
    deduplicated: int
    items: list[BatchCreateItem]


class BatchDeleteResponse(BaseModel):
    deleted: int
    skipped: list[str] = Field(default_factory=list)


class CleanupRequest(BaseModel):
    """按状态和创建时间清理历史任务，默认只删除 7 天前已结束的任务。"""
    statuses: list[TaskStatus] = Field(
        default_factory=lambda: [TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED]
    )
    older_than_days: int = Field(default=7, ge=0, le=365)


class ArtifactPreviewResponse(BaseModel):
    """产物预览元数据；文本内容受长度限制，图片由前端直接加载下载地址。"""
    id: str
    filename: str
    content_type: str
    size_bytes: int
    kind: str
    text: str | None = None
    truncated: bool = False
    url: str


class TaskChatMessage(BaseModel):
    """任务对话的一条消息，role 为 user 或 assistant。"""

    id: str
    task_id: str
    role: str
    content: str
    created_at: datetime


class TaskChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class TaskChatResponse(BaseModel):
    messages: list[TaskChatMessage]
    reply: TaskChatMessage | None = None


class HealthResponse(BaseModel):
    status: str
    app: str
    sandbox: dict[str, Any]
    store: str = "memory"


class ScheduleCreate(BaseModel):
    """创建定时任务：cron 使用标准五段表达式，参数与手动创建一致。"""

    task_type: TaskType
    params: dict[str, Any] = Field(default_factory=dict)
    cron: str = Field(min_length=5, max_length=64)
    name: str = Field(default="", max_length=60)


class ScheduleUpdate(BaseModel):
    enabled: bool


class ScheduleRecord(BaseModel):
    """持久化的定时任务；next_run_at 在每次触发后向前推进。"""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = ""
    task_type: TaskType
    params: dict[str, Any] = Field(default_factory=dict)
    cron: str
    enabled: bool = True
    owner_id: str | None = None
    next_run_at: datetime
    last_run_at: datetime | None = None
    last_task_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class NotificationRecord(BaseModel):
    """站内通知：任务结束或报错时写入，按用户隔离。"""

    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    task_id: str
    task_type: TaskType
    kind: str = "success"
    title: str
    body: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    read_at: datetime | None = None


class NotificationList(BaseModel):
    items: list[NotificationRecord]
    unread: int


class KnowledgeBaseCreate(BaseModel):
    """新建知识库：名称必填，备注可选。"""

    name: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=400)


class KnowledgeBaseRename(BaseModel):
    """重命名知识库：只允许改名称。"""

    name: str = Field(min_length=1, max_length=80)


class KnowledgeBaseRecord(BaseModel):
    """知识库元信息，按 owner_id 隔离。"""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    note: str = ""
    owner_id: str
    created_at: datetime = Field(default_factory=utc_now)


class KbDocumentRecord(BaseModel):
    """知识库中的一篇网页文档，正文保留用于重新切片。"""

    id: str = Field(default_factory=lambda: str(uuid4()))
    kb_id: str
    task_id: str | None = None
    title: str
    url: str = ""
    content: str = ""
    chunk_count: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    artifact_name: str | None = None
    kind: str = "text"
    orphaned: bool = False


class KbDocumentSummary(BaseModel):
    """文档列表项：只带摘要，避免列表接口下发整篇正文。"""

    id: str
    kb_id: str
    task_id: str | None = None
    title: str
    url: str = ""
    preview: str = ""
    chunk_count: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    artifact_name: str | None = None
    kind: str = "text"
    orphaned: bool = False


class TaskKbDocument(BaseModel):
    """某个任务已入库到哪个知识库。"""

    kb_id: str
    kb_name: str
    document_id: str


class KbDocumentCreate(BaseModel):
    """入库请求：既能从任务产物导入，也能直接贴正文。"""

    task_id: str | None = None
    title: str = Field(default="", max_length=200)
    url: str = Field(default="", max_length=2048)
    content: str = Field(default="", max_length=200000)


class KbCitation(BaseModel):
    index: int
    document_id: str
    title: str
    url: str = ""
    snippet: str
    heading: str = ""
    used: bool = True


class KbTaskIngestRequest(BaseModel):
    task_id: str
    artifacts: list[str] = Field(default_factory=list)
    include_chat: bool = True


class KbAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=6, ge=1, le=10)
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)
    conversation_id: str = Field(default="default", max_length=64)
    use_history: bool = True
    document_ids: list[str] = Field(default_factory=list, max_length=50)
    kinds: list[str] = Field(default_factory=list, max_length=10)


class KbAnswer(BaseModel):
    answer: str
    citations: list[KbCitation]
    message_id: str


class KbMessageRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    kb_id: str
    question: str
    answer: str
    citations: list[KbCitation] = Field(default_factory=list)
    conversation_id: str = "default"
    created_at: datetime = Field(default_factory=utc_now)


class KbBatchCreate(BaseModel):
    """批量把多个网页整理任务加入同一知识库。"""

    task_ids: list[str] = Field(min_length=1, max_length=20)


class KbConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=80)

class TagRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    owner_id: str
    name: str = Field(min_length=1, max_length=24)
    created_at: datetime = Field(default_factory=utc_now)


class TaskTagsRequest(BaseModel):
    names: list[str] = Field(default_factory=list, max_length=8)


class DocumentTagsRequest(BaseModel):
    names: list[str] = Field(default_factory=list, max_length=8)


class AnnotationRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    owner_id: str
    task_id: str | None = None
    document_id: str | None = None
    artifact_name: str = "article.md"
    block_index: int = Field(default=0, ge=0)
    start_offset: int = Field(default=0, ge=0)
    end_offset: int = Field(default=0, ge=0)
    quote: str = Field(min_length=1, max_length=2000)
    note: str = Field(default="", max_length=2000)
    created_at: datetime = Field(default_factory=utc_now)


class AnnotationCreate(BaseModel):
    artifact_name: str = Field(default="article.md", max_length=120)
    block_index: int = Field(default=0, ge=0)
    start_offset: int = Field(default=0, ge=0)
    end_offset: int = Field(default=0, ge=0)
    quote: str = Field(min_length=1, max_length=2000)
    note: str = Field(default="", max_length=2000)


class AnnotationUpdate(BaseModel):
    note: str = Field(min_length=1, max_length=2000)
