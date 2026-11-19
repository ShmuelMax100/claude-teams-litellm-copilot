"""Only stable service metadata is reported; provider statistics are not invented."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Tier = Literal["standard", "high", "max"]


class FusionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    request_id: str
    tier: Tier
    status: str
    output: str = ""
    duration_ms: int = Field(default=0, ge=0)
    exit_code: int | None = None


def validate_prompt(prompt: str, maximum: int) -> None:
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > maximum:
        raise ValueError("Prompt must be nonempty and within the configured character limit.")
    if "\x00" in prompt or "@" in prompt:
        # Copilot interprets @file before running the model. No local file
        # access is authorized through this prompt-only service.
        raise ValueError("NUL and @ file-reference syntax are not permitted in prompts.")
    if prompt.lstrip().startswith(("/", "&", "!", "-")):
        # These are CLI command/delegation/shell/option prefixes, not plain
        # prompt input. Start code samples or lists with explanatory text.
        raise ValueError("Start the prompt with text, not a CLI command prefix.")
