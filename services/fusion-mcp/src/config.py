"""Strict per-user configuration. Credentials never appear in object reprs."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from collections.abc import Mapping


@dataclass(frozen=True)
class UserCredential:
    subject: str
    token: str = field(repr=False)
    expires_at: int


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate configuration key.")
        result[key] = value
    return result


@dataclass(frozen=True)
class Config:
    service_token: str = field(default="", repr=False)
    identity_key: str = field(default="", repr=False)
    credentials: dict[str, UserCredential] = field(default_factory=dict, repr=False)
    allowed_tiers: tuple[str, ...] = ("standard", "high", "max")
    timeout_seconds: int = 120
    max_input_chars: int = 32000
    max_context_chars: int = 262144
    max_output_bytes: int = 262144
    max_concurrency: int = 2
    workspace_root: Path = Path("/tmp/fusion")
    copilot_bin: str = "/opt/copilot-runtime/copilot-runtime"
    port: int = 8080

    def __post_init__(self):
        if not self.allowed_tiers or not set(self.allowed_tiers) <= {"standard", "high", "max"}:
            raise ValueError("Invalid Fusion allowed tiers.")
        bounds = {"timeout_seconds": (1, 210), "max_input_chars": (1, 100000),
                  "max_context_chars": (1000, 1000000),
                  "max_output_bytes": (128, 4194304), "max_concurrency": (1, 16),
                  "port": (1024, 65535)}
        for name, (low, high) in bounds.items():
            if type(getattr(self, name)) is not int or not low <= getattr(self, name) <= high:
                raise ValueError("Invalid Fusion limit: " + name)
        secrets = [self.service_token, self.identity_key]
        if any(not isinstance(secret, str) or len(secret) < 32 or
               any(c.isspace() for c in secret) for secret in secrets):
            raise ValueError("Fusion service and identity secrets require at least 32 non-whitespace characters.")
        if not isinstance(self.credentials, dict) or not self.credentials:
            raise ValueError("At least one per-user GitHub credential is required.")
        for subject, credential in self.credentials.items():
            if (not isinstance(subject, str) or not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,128}", subject)
                    or not isinstance(credential, UserCredential) or credential.subject != subject):
                raise ValueError("Invalid Fusion credential subject.")
            if (not isinstance(credential.token, str) or
                    not re.fullmatch(r"github_pat_[A-Za-z0-9_]+", credential.token)):
                raise ValueError("Each user requires a fine-grained GitHub PAT.")
            if type(credential.expires_at) is not int or credential.expires_at <= 0:
                raise ValueError("Each user credential requires a Unix expiry timestamp.")
            secrets.append(credential.token)
        if len(set(secrets)) != len(secrets):
            raise ValueError("All user, service and signing credentials must be distinct.")
        if any(left != right and left.startswith(right) for left in secrets for right in secrets):
            raise ValueError("Credentials must be distinct and must not prefix-overlap.")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None):
        env = os.environ if env is None else env
        obsolete = ("FUSION_COPILOT_BIN", "FUSION_AUTH_MODE", "FUSION_SINGLE_USER_SUBJECT",
                    "FUSION_GITHUB_TOKEN", "FUSION_MCP_TOKEN", "FUSION_ENABLE_FALLBACK")
        if any(name in env for name in obsolete):
            raise ValueError("Obsolete Fusion configuration; use the per-user Fusion-only contract.")
        try:
            mapping = json.loads(env.get("FUSION_USER_CREDENTIALS_JSON", "{}"), object_pairs_hook=strict_object)
            if not isinstance(mapping, dict):
                raise ValueError()
            credentials = {}
            names = set()
            for subject, record in mapping.items():
                if not isinstance(record, dict) or set(record) != {"token_env", "expires_at"}:
                    raise ValueError()
                name = record["token_env"]
                if (not isinstance(name, str) or not re.fullmatch(r"FUSION_GITHUB_USER_[A-Za-z0-9_]+", name)
                        or name in names):
                    raise ValueError()
                names.add(name)
                credentials[subject] = UserCredential(subject, env.get(name, ""), record["expires_at"])
        except (TypeError, ValueError):
            raise ValueError("Invalid FUSION_USER_CREDENTIALS_JSON mapping.") from None
        values = {"service_token": env.get("FUSION_SERVICE_TOKEN", ""),
                  "identity_key": env.get("FUSION_IDENTITY_KEY", ""), "credentials": credentials,
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
