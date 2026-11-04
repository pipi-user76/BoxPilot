from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskType(str, Enum):
    MOCK = "MOCK"
    CSV_ANALYSIS = "CSV_ANALYSIS"
    WEB_DIGEST = "WEB_DIGEST"
    REPO_CHECK = "REPO_CHECK"
    WEB_ACCEPTANCE = "WEB_ACCEPTANCE"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


class StepStatus(str, Enum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class CreateTaskRequest(BaseModel):
    task_type: TaskType
    params: dict[str, Any] = Field(default_factory=dict)


class TaskStep(BaseModel):
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
    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    filename: str
    relative_path: str
    content_type: str
    size_bytes: int
    created_at: datetime = Field(default_factory=utc_now)


class TaskRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    task_type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    steps: list[TaskStep] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    app: str
    sandbox: dict[str, Any]
    store: str = "memory"
