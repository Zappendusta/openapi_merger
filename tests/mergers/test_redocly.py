from unittest.mock import patch

import pytest
import yaml

from openapi_merger.mergers.redocly import RedoclyMerger


def test_redocly_merger_constructs_command_correctly(tmp_path):
    captured = {}

    def fake_run_subprocess(cmd, timeout=60):
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        out_idx = cmd.index("-o") + 1
        with open(cmd[out_idx], "w") as f:
            yaml.safe_dump({"openapi": "3.0.0", "info": {"title": "X", "version": "0"}, "paths": {}, "components": {}}, f)
        return (0, "", "")

    with patch("openapi_merger.mergers.redocly.run_subprocess", side_effect=fake_run_subprocess):
        with patch("openapi_merger.mergers.redocly.shutil.which", return_value="/usr/bin/redocly"):
            merger = RedoclyMerger()
            sources = [
                ("alpha", "P1", {"openapi": "3.0.0", "info": {"title": "A", "version": "0"}, "paths": {}, "components": {}}),
                ("beta", "P2", {"openapi": "3.0.0", "info": {"title": "B", "version": "0"}, "paths": {}, "components": {}}),
            ]
            out = merger.merge(sources, title="T", version="V")
    assert out["info"] == {"title": "T", "version": "V"}
    assert captured["cmd"][0] == "/usr/bin/redocly"
    assert captured["cmd"][1] == "join"
    assert "-o" in captured["cmd"]
    assert "--prefix-components-with-info-prop" in captured["cmd"]


def test_redocly_merger_unavailable_raises():
    with patch("openapi_merger.mergers.redocly.shutil.which", return_value=None):
        merger = RedoclyMerger()
        with pytest.raises(RuntimeError, match="redocly binary not found"):
            merger.merge([], title="T", version="V")


def test_redocly_merger_is_available_reflects_shutil_which():
    with patch("openapi_merger.mergers.redocly.shutil.which", return_value="/usr/bin/redocly"):
        assert RedoclyMerger.is_available() is True
    with patch("openapi_merger.mergers.redocly.shutil.which", return_value=None):
        assert RedoclyMerger.is_available() is False
