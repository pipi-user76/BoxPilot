from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from app.services.artifact_store import ArtifactSpec, ArtifactStore


class StepExecutionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class TaskContext:
    task_id: str
    params: dict[str, Any]
    artifact_store: ArtifactStore


@dataclass(frozen=True)
class StepOutcome:
    output: str = ""
    artifacts: list[ArtifactSpec] = field(default_factory=list)


@dataclass(frozen=True)
class StepDefinition:
    name: str
    action: Callable[[TaskContext], StepOutcome]


class TaskTemplate(Protocol):
    name: str
    steps: tuple[StepDefinition, ...]
