"""Internal handoff boundary; conversation history is context, not a native replay."""
from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from schemas import FusionResult


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict


@dataclass(frozen=True)
class CompletionRequest:
    tier: str
    prompt: str
    system_message: str
    tools: tuple[ToolDefinition, ...] = ()


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    arguments: dict


class CompletionResult(FusionResult):
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: dict[str, int] | None = None
    finish_reason: Literal["stop", "tool_calls", "length"] = "stop"
