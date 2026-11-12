"""文件职责：定义所有任务模板共用的上下文、步骤和返回值契约。

模板不直接修改 TaskStore 或任务状态，只描述固定步骤和产物；统一的状态记录由调度器
负责，这样四个模板的行为可以保持一致并便于审查。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from backend.storage import ArtifactSpec, ArtifactStore


class StepExecutionError(RuntimeError):
    """模板步骤遇到可预期失败时抛出，调度器会将其落库并停止后续步骤。

    message 面向使用者，写清“发生了什么、可以怎么办”；detail 保存原始命令输出，
    供排查时查看，不直接占据错误提示位置。
    """
    def __init__(
        self,
        code: str,
        message: str,
        detail: str = "",
        artifacts: tuple[ArtifactSpec, ...] = (),
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        # 失败前已经写好的产物仍然登记，让用户至少能拿到一份失败说明。
        self.artifacts = artifacts


@dataclass(frozen=True)
class TaskContext:
    """模板执行上下文；只暴露任务 ID、参数和产物存储。"""
    task_id: str
    params: dict[str, Any]
    artifact_store: ArtifactStore
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StepOutcome:
    """步骤成功后的输出摘要、可选业务结论和产物清单。"""
    output: str = ""
    artifacts: list[ArtifactSpec] = field(default_factory=list)
    result: dict[str, Any] | None = None


@dataclass(frozen=True)
class StepDefinition:
    """一个可执行步骤的名称和动作函数；timeout_seconds 留空时使用全局默认值。"""
    name: str
    action: Callable[[TaskContext], StepOutcome]
    timeout_seconds: int | None = None


class TaskTemplate(Protocol):
    """任务模板协议；name 用于展示，steps 决定固定执行顺序。"""
    name: str
    steps: tuple[StepDefinition, ...]
