import json
import os
from unittest.mock import patch

import pytest
import yaml

from openapi_merger.mergers.openapi_merge import OpenApiMergeMerger


def test_openapi_merge_writes_config_and_invokes_binary():
    captured = {}

    def fake_run_subprocess(cmd, timeout=60):
        captured["cmd"] = cmd
        cfg_path = cmd[cmd.index("--config") + 1]
        workdir = os.path.dirname(cfg_path)
        with open(cfg_path) as f:
            captured["config"] = json.load(f)
        output_path = captured["config"]["output"]
        if not os.path.isabs(output_path):
            output_path = os.path.join(workdir, output_path)
        with open(output_path, "w") as f:
            yaml.safe_dump({"openapi": "3.0.0", "info": {"title": "X", "version": "0"}, "paths": {}, "components": {}}, f)
        return (0, "", "")

    with patch("openapi_merger.mergers.openapi_merge.run_subprocess", side_effect=fake_run_subprocess):
        with patch("openapi_merger.mergers.openapi_merge.shutil.which", return_value="/usr/bin/openapi-merge-cli"):
            merger = OpenApiMergeMerger()
            sources = [
                ("alpha", "P1", {"openapi": "3.0.0", "info": {"title": "A", "version": "0"}, "paths": {}, "components": {}}),
                ("beta", "P2", {"openapi": "3.0.0", "info": {"title": "B", "version": "0"}, "paths": {}, "components": {}}),
            ]
            out = merger.merge(sources, title="T", version="V")
    assert out["info"] == {"title": "T", "version": "V"}
    assert captured["cmd"][0] == "/usr/bin/openapi-merge-cli"
    assert "inputs" in captured["config"]
    assert len(captured["config"]["inputs"]) == 2
    assert all("inputFile" in entry for entry in captured["config"]["inputs"])
    for entry in captured["config"]["inputs"]:
        assert not os.path.isabs(entry["inputFile"]), f"inputFile must be relative, got {entry['inputFile']!r}"
    assert not os.path.isabs(captured["config"]["output"]), f"output must be relative, got {captured['config']['output']!r}"


def test_openapi_merge_unavailable_raises():
    with patch("openapi_merger.mergers.openapi_merge.shutil.which", return_value=None):
        merger = OpenApiMergeMerger()
        with pytest.raises(RuntimeError, match="openapi-merge-cli binary not found"):
            merger.merge([], title="T", version="V")


def test_openapi_merge_is_available_reflects_shutil_which():
    with patch("openapi_merger.mergers.openapi_merge.shutil.which", return_value="/x"):
        assert OpenApiMergeMerger.is_available() is True
    with patch("openapi_merger.mergers.openapi_merge.shutil.which", return_value=None):
        assert OpenApiMergeMerger.is_available() is False
