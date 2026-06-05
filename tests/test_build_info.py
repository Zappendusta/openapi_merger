import os

import pytest

from openapi_merger.build_info import collect_build_info


@pytest.fixture(autouse=True)
def _clear_build_env(monkeypatch):
    for key in ("BUILD_GIT_SHA", "BUILD_GIT_BRANCH", "BUILD_TIME"):
        monkeypatch.delenv(key, raising=False)


def test_collect_build_info_defaults_to_unknown_outside_docker():
    info = collect_build_info()
    assert info["build_git_sha"] == "unknown"
    assert info["build_git_branch"] == "unknown"
    assert info["build_time"] == "unknown"


def test_collect_build_info_reads_env_when_baked(monkeypatch):
    monkeypatch.setenv("BUILD_GIT_SHA", "abc1234")
    monkeypatch.setenv("BUILD_GIT_BRANCH", "master")
    monkeypatch.setenv("BUILD_TIME", "2026-06-05T10:00:00Z")
    info = collect_build_info()
    assert info["build_git_sha"] == "abc1234"
    assert info["build_git_branch"] == "master"
    assert info["build_time"] == "2026-06-05T10:00:00Z"


def test_collect_build_info_includes_runtime_facts():
    info = collect_build_info()
    assert info["version"]  # installed package version
    assert info["python_version"].count(".") == 2
    assert info["platform"]
    assert info["hostname"]
    assert isinstance(info["pid"], int) and info["pid"] == os.getpid()


def test_collect_build_info_lists_tracked_dependencies():
    info = collect_build_info()
    deps = info["dependencies"]
    for expected in ("fastapi", "uvicorn", "httpx", "pydantic", "structlog", "pyyaml"):
        assert expected in deps
        assert deps[expected] != ""
