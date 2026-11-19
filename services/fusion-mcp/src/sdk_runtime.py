"""The only dependency on the released Copilot SDK's construction API."""
from importlib.metadata import version
from pathlib import Path
import shutil

SDK_VERSION = "2.0.1"
RUNTIME_VERSION = "1.0.114"
PROTOCOL_VERSION = 4


def deny_permission(request, invocation):
    from copilot.rpc import PermissionDecisionReject
    return PermissionDecisionReject(feedback="This service permits prompt text only.")


def create_sdk_client(*, runtime_path, environment, working_directory, base_directory):
    # An explicit installed bundle is required. Never invoke the SDK's downloader.
    if version("microsoft-copilot-sdk") != SDK_VERSION:
        raise RuntimeError("Unsupported Copilot SDK version")
    binary = shutil.which(runtime_path)
    if binary is None or not Path(binary).is_file():
        raise FileNotFoundError("Copilot runtime bundle is missing")
    from copilot import CopilotClient, RuntimeConnection
    return CopilotClient(
        mode="empty",
        runtime=RuntimeConnection.for_spawned_stdio(
            path=binary, env=environment, working_directory=working_directory,
            base_directory=base_directory, session_idle_timeout_seconds=300),
        github={"use_logged_in_user": False},
        observability={"log_level": "none"},
    )
