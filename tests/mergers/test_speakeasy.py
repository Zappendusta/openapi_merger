from unittest.mock import patch

import pytest
import yaml

from openapi_merger.mergers.speakeasy import SpeakeasyMerger


def test_speakeasy_merger_constructs_command_correctly():
    captured = {}

    def fake_run_subprocess(cmd, timeout=60):
        captured["cmd"] = cmd
        out_idx = cmd.index("-o") + 1
        with open(cmd[out_idx], "w") as f:
            yaml.safe_dump({"openapi": "3.0.0", "info": {"title": "X", "version": "0"}, "paths": {}, "components": {}}, f)
        return (0, "", "")

    with patch("openapi_merger.mergers.speakeasy.run_subprocess", side_effect=fake_run_subprocess):
        with patch("openapi_merger.mergers.speakeasy.shutil.which", return_value="/usr/local/bin/speakeasy"):
            merger = SpeakeasyMerger()
            sources = [
                ("alpha", "P1", {"openapi": "3.0.0", "info": {"title": "A", "version": "0"}, "paths": {}, "components": {}}),
                ("beta", "P2", {"openapi": "3.0.0", "info": {"title": "B", "version": "0"}, "paths": {}, "components": {}}),
            ]
            out = merger.merge(sources, title="T", version="V")
    assert out["info"] == {"title": "T", "version": "V"}
    assert captured["cmd"][0] == "/usr/local/bin/speakeasy"
    assert captured["cmd"][1] == "merge"
    s_flags = [i for i, v in enumerate(captured["cmd"]) if v == "-s"]
    assert len(s_flags) == 2


def test_speakeasy_merger_unavailable_raises():
    with patch("openapi_merger.mergers.speakeasy.shutil.which", return_value=None):
        merger = SpeakeasyMerger()
        with pytest.raises(RuntimeError, match="speakeasy binary not found"):
            merger.merge([], title="T", version="V")


def test_speakeasy_merger_is_available_reflects_shutil_which():
    with patch("openapi_merger.mergers.speakeasy.shutil.which", return_value="/x"):
        assert SpeakeasyMerger.is_available() is True
    with patch("openapi_merger.mergers.speakeasy.shutil.which", return_value=None):
        assert SpeakeasyMerger.is_available() is False
