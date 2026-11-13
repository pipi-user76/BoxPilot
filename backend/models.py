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
    # 幂等键在窗口内唯一；deduplicated 只在响应中标记，表示命中已有任务而未新建。
    dedupe_key: str | None = None
    deduplicated: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    steps: list[TaskStep] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)


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


class HealthResponse(BaseModel):
    status: str
    app: str
    sandbox: dict[str, Any]
    store: str = "memory"
