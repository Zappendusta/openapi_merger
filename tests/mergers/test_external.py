import pytest

from openapi_merger.mergers.external import run_external_merger


def test_run_external_merger_invokes_callable_with_paths(tmp_path):
    captured = {}

    def fake_invoke(input_files: list[str], output_file: str, workdir: str) -> tuple[int, str, str]:
        captured["inputs"] = input_files
        captured["output"] = output_file
        captured["workdir"] = workdir
        # Write a valid merged YAML so the helper can read it back.
        import yaml
        with open(output_file, "w") as f:
            yaml.safe_dump(
                {"openapi": "3.0.0", "info": {"title": "X", "version": "0"}, "paths": {}, "components": {}},
                f,
            )
        return (0, "", "")

    sources = [
        ("alpha", "P1", {"openapi": "3.0.0", "info": {"title": "A", "version": "0.1"}, "paths": {}, "components": {}}),
        ("beta", "P2", {"openapi": "3.0.0", "info": {"title": "B", "version": "0.1"}, "paths": {}, "components": {}}),
    ]
    out = run_external_merger("test-merger", sources, title="Merged", version="9.9", invoke=fake_invoke)
    assert out["info"] == {"title": "Merged", "version": "9.9"}
    assert len(captured["inputs"]) == 2
    assert captured["inputs"][0].endswith("alpha.yaml")
    assert captured["inputs"][1].endswith("beta.yaml")


def test_run_external_merger_raises_on_nonzero_exit():
    def fake_invoke(input_files, output_file, workdir):
        return (1, "", "boom on line 3")

    sources = [("a", "", {"openapi": "3.0.0", "info": {"title": "A", "version": "0"}, "paths": {}, "components": {}})]
    with pytest.raises(RuntimeError, match="test-merger failed.*boom on line 3"):
        run_external_merger("test-merger", sources, title="T", version="V", invoke=fake_invoke)


def test_run_external_merger_sanitizes_filenames():
    captured = {}

    def fake_invoke(input_files, output_file, workdir):
        captured["inputs"] = input_files
        import yaml
        with open(output_file, "w") as f:
            yaml.safe_dump({"openapi": "3.0.0", "info": {"title": "X", "version": "0"}, "paths": {}, "components": {}}, f)
        return (0, "", "")

    sources = [("a/b c", "", {"openapi": "3.0.0", "info": {"title": "A", "version": "0"}, "paths": {}, "components": {}})]
    run_external_merger("m", sources, title="T", version="V", invoke=fake_invoke)
    assert captured["inputs"][0].endswith("a_b_c.yaml")
