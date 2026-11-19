"""Strict environment configuration. Secrets never appear in object reprs."""
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from collections.abc import Mapping


@dataclass(frozen=True)
class Config:
    auth_mode: str = "disabled"
    subject: str = ""
    github_token: str = field(default="", repr=False)
    mcp_token: str = field(default="", repr=False)
    allowed_tiers: tuple[str, ...] = ("standard", "high", "max")
    timeout_seconds: int = 120
    max_input_chars: int = 32000
    enable_fallback: bool = False
    max_context_chars: int = 262144
    max_output_bytes: int = 262144
    max_concurrency: int = 2
    workspace_root: Path = Path("/tmp/fusion")
    copilot_bin: str = "/opt/copilot-runtime/copilot-runtime"
    port: int = 8080

    def __post_init__(self):
        if self.auth_mode not in ("disabled", "single_user"):
            raise ValueError("Fusion supports disabled or explicit single_user mode only.")
        if not self.allowed_tiers or not set(self.allowed_tiers) <= {"standard", "high", "max"}:
            raise ValueError("Invalid Fusion allowed tiers.")
        bounds = {"timeout_seconds": (1, 1800), "max_input_chars": (1, 100000),
                  "max_context_chars": (1000, 1000000),
                  "max_output_bytes": (128, 4194304), "max_concurrency": (1, 16),
                  "port": (1024, 65535)}
        for name, (low, high) in bounds.items():
            if not isinstance(getattr(self, name), int) or not low <= getattr(self, name) <= high:
                raise ValueError("Invalid Fusion limit: " + name)
        if self.auth_mode == "single_user":
            if not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,128}", self.subject):
                raise ValueError("FUSION_SINGLE_USER_SUBJECT must be a safe, nonempty owner ID.")
            if len(self.mcp_token) < 32 or any(c.isspace() for c in self.mcp_token):
                raise ValueError("FUSION_MCP_TOKEN must contain at least 32 non-whitespace characters.")
            if not self.github_token.startswith("github_pat_") or any(c.isspace() for c in self.github_token):
                raise ValueError("FUSION_GITHUB_TOKEN requires a user-owned fine-grained PAT.")
            if self.github_token == self.mcp_token:
                raise ValueError("MCP and GitHub credentials must be distinct.")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None):
        env = os.environ if env is None else env
        if "FUSION_COPILOT_BIN" in env:
            raise ValueError("FUSION_COPILOT_BIN is obsolete; set FUSION_RUNTIME_PATH to the SDK runtime bundle.")
        if env.get("FUSION_ENABLE_FALLBACK", "false") not in ("true", "false"):
            raise ValueError("FUSION_ENABLE_FALLBACK must be true or false.")
        values = {"auth_mode": env.get("FUSION_AUTH_MODE", "disabled"),
                  "subject": env.get("FUSION_SINGLE_USER_SUBJECT", ""),
                  "github_token": env.get("FUSION_GITHUB_TOKEN", ""),
                  "mcp_token": env.get("FUSION_MCP_TOKEN", ""),
                  "enable_fallback": env.get("FUSION_ENABLE_FALLBACK", "false") == "true",
                  "allowed_tiers": tuple(env.get("FUSION_ALLOWED_TIERS", "standard,high,max").split(",")),
                  "workspace_root": Path(env.get("FUSION_WORKSPACE_ROOT", "/tmp/fusion")),
                  "copilot_bin": env.get("FUSION_RUNTIME_PATH", "/opt/copilot-runtime/copilot-runtime")}
        for key in ("timeout_seconds", "max_input_chars", "max_context_chars", "max_output_bytes", "max_concurrency", "port"):
            if "FUSION_" + key.upper() in env:
                try:
                    values[key] = int(env["FUSION_" + key.upper()])
                except ValueError:
                    raise ValueError("Invalid Fusion numeric setting: " + key) from None
        return cls(**values)
