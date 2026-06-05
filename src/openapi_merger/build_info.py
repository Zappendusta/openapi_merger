from __future__ import annotations

import importlib.metadata
import os
import platform
import socket
import sys

_TRACKED_DEPENDENCIES = ("fastapi", "uvicorn", "httpx", "pydantic", "structlog", "pyyaml")


def _safe_version(dist: str) -> str:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def collect_build_info() -> dict[str, str | int | dict[str, str]]:
    """Gather build-time and runtime facts for the startup banner.

    Build-time fields (`build_git_sha`, `build_git_branch`, `build_time`) come
    from env vars baked into the image via Dockerfile build args. They default
    to "unknown" for local development outside Docker.
    """
    py = sys.version_info
    return {
        "version": _safe_version("openapi-merger"),
        "build_git_sha": os.getenv("BUILD_GIT_SHA", "unknown"),
        "build_git_branch": os.getenv("BUILD_GIT_BRANCH", "unknown"),
        "build_time": os.getenv("BUILD_TIME", "unknown"),
        "python_version": f"{py.major}.{py.minor}.{py.micro}",
        "platform": platform.platform(),
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "dependencies": {dep: _safe_version(dep) for dep in _TRACKED_DEPENDENCIES},
    }
