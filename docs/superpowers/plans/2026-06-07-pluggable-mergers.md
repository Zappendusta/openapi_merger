# Pluggable Mergers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose four merge endpoints — `/inhouse/openapi.json`, `/redocly/openapi.json`, `/speakeasy/openapi.json`, `/openapi-merge/openapi.json` — that share one fetch/transform/cache/serve pipeline but plug a different merge engine per endpoint. The root `spec_path` is aliased to a configurable default merger.

**Architecture:** A `MergerStrategy` Protocol defines the merge contract: `merge(sources: list[(name, doc)], title, version) -> dict`. Four concrete adapters live under `src/openapi_merger/mergers/`. The in-house adapter wraps the existing `merge_specs()` function so behavior at `/inhouse/...` is byte-identical to today's `/openapi.json`. The three external adapters share a subprocess helper that writes pre-transformed sources to a `TemporaryDirectory`, invokes the merger binary with timeout, and parses the result. `MergeOrchestrator` is parameterized by strategy and instantiated once per merger at startup (each gets its own cache). `/admin/cache/clear` clears all four. Binaries are probed at startup; missing binary logs a warning and the corresponding endpoint returns 503 at request time without crashing the app.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, httpx, structlog, PyYAML, pytest+respx. External binaries: Speakeasy CLI (Go), `@redocly/cli` (npm), `openapi-merge-cli` (npm). Docker base: `python:3.12-slim` extended with Node.js runtime and a pinned Speakeasy binary.

---

## File Structure

**New files:**
- `src/openapi_merger/mergers/__init__.py` — package marker + registry mapping merger key → strategy class
- `src/openapi_merger/mergers/base.py` — `MergerStrategy` Protocol and `MergerNotAvailable` exception
- `src/openapi_merger/mergers/inhouse.py` — adapter wrapping `merger.merge_specs`
- `src/openapi_merger/mergers/external.py` — shared subprocess helper (`run_external_merger`, temp-dir lifecycle, timeout, stderr capture)
- `src/openapi_merger/mergers/redocly.py` — `RedoclyMerger`
- `src/openapi_merger/mergers/speakeasy.py` — `SpeakeasyMerger`
- `src/openapi_merger/mergers/openapi_merge.py` — `OpenApiMergeMerger`
- `tests/mergers/__init__.py`
- `tests/mergers/test_inhouse.py`
- `tests/mergers/test_external.py`
- `tests/mergers/test_redocly.py`
- `tests/mergers/test_speakeasy.py`
- `tests/mergers/test_openapi_merge.py`
- `tests/e2e/test_pluggable_endpoints.py`

**Modified files:**
- `src/openapi_merger/orchestrator.py` — accept a `MergerStrategy` instance; remove direct `merge_specs` import
- `src/openapi_merger/config.py` — add `default_merger` field on `ServiceConfig`
- `src/openapi_merger/main.py` — instantiate one orchestrator per merger; register four path-prefixed routes; alias `spec_path` to the default merger; broaden `/admin/cache/clear` to clear all
- `Dockerfile` — multi-stage build with all three external binaries
- `README.md` — document endpoints, default_merger config, binary versions, per-merger conflict behaviors

---

## Conventions used in every external adapter

These are repeated rather than abstracted because YAGNI:

1. Write each pre-transformed source to `{tmpdir}/{sanitized_name}.yaml` (use `yaml.safe_dump`). Sanitize `name` with `re.sub(r"[^A-Za-z0-9_.-]", "_", name)`.
2. Run the binary with `subprocess.run([...], capture_output=True, text=True, timeout=60, check=False)`.
3. On non-zero return code: raise `RuntimeError(f"{merger_name} failed (exit {rc}): {stderr.strip()[:500]}")`. Orchestrator translates to HTTP 502.
4. On timeout: raise `RuntimeError(f"{merger_name} timed out after 60s")`.
5. Read merged output from `{tmpdir}/merged.yaml`, parse with `yaml.safe_load`.
6. Override `info.title` and `info.version` on the returned dict to match the caller's arguments (each external merger picks these inconsistently; we normalize at the boundary).
7. Log `merge.external.start`, `merge.external.ok` (with `duration_ms`, `paths_count`), `merge.external.failed` with the field `merger=<key>` for greppable parity.
8. Probe binary availability with `shutil.which(<binary_name>)` at adapter import time; expose `is_available()` classmethod for startup probe.

---

## Per-merger conflict behavior (documented, not normalized)

| Endpoint | Path collision | Component collision | Security scheme |
|---|---|---|---|
| `/inhouse/` | 502 error (current behavior) | source-prefix rename | source-prefix rename |
| `/redocly/` | exits with conflict → 502 | requires `--prefix-components-with-info-prop` (we set it) | exits → 502 |
| `/speakeasy/` | fragment paths (`#suffix`) | last-wins with warning | last-wins |
| `/openapi-merge/` | merges via dispute prefix from config | dispute prefix | first-wins |

This divergence is intentional — comparing behaviors is the point of the feature.

---

## Task 1: MergerStrategy protocol + InhouseMerger adapter

**Files:**
- Create: `src/openapi_merger/mergers/__init__.py`
- Create: `src/openapi_merger/mergers/base.py`
- Create: `src/openapi_merger/mergers/inhouse.py`
- Create: `tests/mergers/__init__.py`
- Create: `tests/mergers/test_inhouse.py`

- [ ] **Step 1.1: Write the failing test for InhouseMerger**

Create `tests/mergers/test_inhouse.py`:

```python
from openapi_merger.mergers.inhouse import InhouseMerger


def test_inhouse_merger_matches_merge_specs():
    sources = [
        ("alpha", "Alpha", {
            "openapi": "3.0.0",
            "info": {"title": "A", "version": "0.1"},
            "paths": {"/a": {"get": {"responses": {"200": {"description": "ok"}}}}},
            "components": {"schemas": {}},
        }),
        ("beta", "Beta", {
            "openapi": "3.0.0",
            "info": {"title": "B", "version": "0.1"},
            "paths": {"/b": {"get": {"responses": {"200": {"description": "ok"}}}}},
            "components": {"schemas": {}},
        }),
    ]
    merger = InhouseMerger()
    out = merger.merge(sources, title="Merged", version="9.9")
    assert out["info"] == {"title": "Merged", "version": "9.9"}
    assert "/a" in out["paths"]
    assert "/b" in out["paths"]


def test_inhouse_merger_is_available():
    assert InhouseMerger.is_available() is True
```

- [ ] **Step 1.2: Run the test to verify it fails**

Run: `pytest tests/mergers/test_inhouse.py -v`
Expected: FAIL — module `openapi_merger.mergers.inhouse` not found.

- [ ] **Step 1.3: Create the package marker**

Create `src/openapi_merger/mergers/__init__.py`:

```python
```

Create `tests/mergers/__init__.py`:

```python
```

- [ ] **Step 1.4: Create the protocol in base.py**

Create `src/openapi_merger/mergers/base.py`:

```python
from __future__ import annotations
from typing import Protocol, runtime_checkable


class MergerNotAvailable(RuntimeError):
    """Raised when a merger's underlying binary is not installed."""


@runtime_checkable
class MergerStrategy(Protocol):
    """Contract for an OpenAPI merger implementation.

    Sources are tuples of (source_name, schema_prefix, pre_transformed_doc).
    The schema_prefix is honored only by InhouseMerger; external mergers ignore it.
    """

    key: str
    display_name: str

    def merge(self, sources: list[tuple[str, str, dict]], title: str, version: str) -> dict:
        ...

    @classmethod
    def is_available(cls) -> bool:
        ...
```

- [ ] **Step 1.5: Implement InhouseMerger**

Create `src/openapi_merger/mergers/inhouse.py`:

```python
from __future__ import annotations

from openapi_merger.merger import merge_specs


class InhouseMerger:
    key = "inhouse"
    display_name = "in-house"

    def merge(self, sources: list[tuple[str, str, dict]], title: str, version: str) -> dict:
        return merge_specs(sources, title=title, version=version)

    @classmethod
    def is_available(cls) -> bool:
        return True
```

- [ ] **Step 1.6: Run test to verify it passes**

Run: `pytest tests/mergers/test_inhouse.py -v`
Expected: PASS (2 tests).

- [ ] **Step 1.7: Commit**

```bash
git add src/openapi_merger/mergers/ tests/mergers/
git commit -m "feat: introduce MergerStrategy protocol with InhouseMerger adapter"
```

---

## Task 2: Refactor MergeOrchestrator to accept a strategy

**Files:**
- Modify: `src/openapi_merger/orchestrator.py`
- Modify: `tests/test_orchestrator.py`

- [ ] **Step 2.1: Write the failing test**

Append to `tests/test_orchestrator.py`:

```python
from openapi_merger.mergers.base import MergerStrategy
from openapi_merger.orchestrator import MergeOrchestrator
from openapi_merger.config import ServiceConfig, InfoConfig, SourcesConfig


class _StubMerger:
    key = "stub"
    display_name = "stub"

    def __init__(self):
        self.calls = []

    def merge(self, sources, title, version):
        self.calls.append((len(sources), title, version))
        return {"openapi": "3.0.0", "info": {"title": title, "version": version}, "paths": {}, "components": {}}

    @classmethod
    def is_available(cls):
        return True


async def test_orchestrator_delegates_to_strategy(monkeypatch):
    async def _fake_fetch(source):
        return {
            "openapi": "3.0.0",
            "info": {"title": source.name, "version": "0.1"},
            "paths": {},
            "components": {},
        }

    monkeypatch.setattr("openapi_merger.orchestrator.fetch_spec", _fake_fetch)

    from openapi_merger.config import SourceConfig
    svc = ServiceConfig(info=InfoConfig(title="T", version="V"))
    srcs = SourcesConfig(sources=[
        SourceConfig(name="s1", url="http://x", schema_prefix="P1"),
    ])
    strategy = _StubMerger()
    orch = MergeOrchestrator(svc, srcs, strategy=strategy)
    result = await orch.get_merged()
    assert result["info"]["title"] == "T"
    assert strategy.calls == [(1, "T", "V")]
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator.py::test_orchestrator_delegates_to_strategy -v`
Expected: FAIL — `MergeOrchestrator.__init__()` does not accept `strategy=`.

- [ ] **Step 2.3: Modify the orchestrator**

In `src/openapi_merger/orchestrator.py` replace the existing class with:

```python
import asyncio
import time

import structlog

from openapi_merger.config import ServiceConfig, SourcesConfig
from openapi_merger.fetcher import fetch_spec
from openapi_merger.mergers.base import MergerStrategy
from openapi_merger.transformer import transform_paths

log = structlog.get_logger()


class MergeOrchestrator:
    def __init__(
        self,
        service_config: ServiceConfig,
        sources_config: SourcesConfig,
        strategy: MergerStrategy,
    ):
        self._service = service_config
        self._sources = sources_config
        self._strategy = strategy
        self._cache: dict | None = None

    @property
    def merger_key(self) -> str:
        return self._strategy.key

    async def get_merged(self, refresh: bool = False) -> dict:
        if self._cache is not None and not refresh:
            log.info("merge.cache.hit", merger=self._strategy.key)
            return self._cache
        log.info("merge.cache.miss", merger=self._strategy.key, refresh=refresh, cached=self._cache is not None)
        self._cache = await self._build()
        return self._cache

    def clear_cache(self) -> None:
        had_cache = self._cache is not None
        self._cache = None
        log.info("merge.cache.clear", merger=self._strategy.key, had_cache=had_cache)

    async def _build(self) -> dict:
        log.info(
            "merge.build.start",
            merger=self._strategy.key,
            sources_count=len(self._sources.sources),
            source_names=[s.name for s in self._sources.sources],
        )
        start = time.perf_counter()
        try:
            docs = await asyncio.gather(
                *[fetch_spec(s) for s in self._sources.sources]
            )
            processed = []
            for source, doc in zip(self._sources.sources, docs):
                paths_before = len(doc.get("paths", {}))
                doc["paths"] = transform_paths(
                    doc.get("paths", {}),
                    source.route_transforms,
                    discard_paths=source.discard_paths,
                )
                paths_after = len(doc["paths"])
                log.info(
                    "spec.transform.ok",
                    merger=self._strategy.key,
                    source=source.name,
                    paths_in=paths_before,
                    paths_out=paths_after,
                    discarded=paths_before - paths_after,
                    transforms_applied=len(source.route_transforms),
                )
                processed.append((source.name, source.schema_prefix, doc))

            merged = self._strategy.merge(
                processed,
                title=self._service.info.title,
                version=self._service.info.version,
            )
        except Exception as e:
            duration_ms = int((time.perf_counter() - start) * 1000)
            log.error(
                "merge.build.failed",
                merger=self._strategy.key,
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=duration_ms,
            )
            raise

        duration_ms = int((time.perf_counter() - start) * 1000)
        log.info(
            "merge.build.ok",
            merger=self._strategy.key,
            duration_ms=duration_ms,
            paths_count=len(merged.get("paths", {})),
            schemas_count=len(merged.get("components", {}).get("schemas", {})),
        )
        return merged
```

- [ ] **Step 2.4: Update existing orchestrator test sites**

Find every existing instantiation of `MergeOrchestrator(...)` in `tests/test_orchestrator.py` and `tests/test_integration.py` and pass `strategy=InhouseMerger()`. Add this import to those test files:

```python
from openapi_merger.mergers.inhouse import InhouseMerger
```

- [ ] **Step 2.5: Run full test suite**

Run: `pytest tests/ -v`
Expected: PASS — including the new `test_orchestrator_delegates_to_strategy` and all preexisting tests after adding the `strategy=` kwarg.

- [ ] **Step 2.6: Commit**

```bash
git add src/openapi_merger/orchestrator.py tests/test_orchestrator.py tests/test_integration.py
git commit -m "refactor: orchestrator delegates merging to a MergerStrategy"
```

---

## Task 3: Shared external-merger subprocess helper

**Files:**
- Create: `src/openapi_merger/mergers/external.py`
- Create: `tests/mergers/test_external.py`

- [ ] **Step 3.1: Write the failing test**

Create `tests/mergers/test_external.py`:

```python
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
```

- [ ] **Step 3.2: Run test to verify it fails**

Run: `pytest tests/mergers/test_external.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3.3: Implement the helper**

Create `src/openapi_merger/mergers/external.py`:

```python
from __future__ import annotations
import os
import re
import subprocess
import tempfile
import time
from typing import Callable

import structlog
import yaml

log = structlog.get_logger()

InvokeFn = Callable[[list[str], str, str], tuple[int, str, str]]
"""Callable signature: (input_files, output_file, workdir) -> (returncode, stdout, stderr)."""


_NAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _sanitize(name: str) -> str:
    return _NAME_SANITIZE_RE.sub("_", name)


def run_external_merger(
    merger_key: str,
    sources: list[tuple[str, str, dict]],
    title: str,
    version: str,
    invoke: InvokeFn,
) -> dict:
    """Drive an external merger via a caller-supplied invoke function.

    Writes one YAML file per source into a TemporaryDirectory, calls `invoke`,
    reads `merged.yaml` from the same directory, and rewrites `info.title` and
    `info.version` to the requested values before returning.
    """
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"merger-{merger_key}-") as workdir:
        input_files: list[str] = []
        for name, _prefix, doc in sources:
            safe = _sanitize(name)
            path = os.path.join(workdir, f"{safe}.yaml")
            with open(path, "w") as f:
                yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)
            input_files.append(path)

        output_file = os.path.join(workdir, "merged.yaml")

        log.info("merge.external.start", merger=merger_key, inputs=len(input_files))
        rc, stdout, stderr = invoke(input_files, output_file, workdir)
        duration_ms = int((time.perf_counter() - start) * 1000)

        if rc != 0:
            log.error(
                "merge.external.failed",
                merger=merger_key,
                returncode=rc,
                duration_ms=duration_ms,
                stderr=stderr.strip()[:500],
            )
            raise RuntimeError(f"{merger_key} failed (exit {rc}): {stderr.strip()[:500]}")

        if not os.path.exists(output_file):
            log.error("merge.external.failed", merger=merger_key, reason="missing_output", duration_ms=duration_ms)
            raise RuntimeError(f"{merger_key} produced no output file")

        with open(output_file) as f:
            merged = yaml.safe_load(f)

        if not isinstance(merged, dict):
            raise RuntimeError(f"{merger_key} produced non-mapping output")

        merged.setdefault("info", {})
        merged["info"]["title"] = title
        merged["info"]["version"] = version

        log.info(
            "merge.external.ok",
            merger=merger_key,
            duration_ms=duration_ms,
            paths_count=len(merged.get("paths") or {}),
        )
        return merged


def run_subprocess(
    cmd: list[str],
    timeout: int = 60,
) -> tuple[int, str, str]:
    """Convenience wrapper around subprocess.run with timeout. Returns (rc, stdout, stderr)."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        return 124, "", f"timed out after {timeout}s: {e.cmd}"
```

- [ ] **Step 3.4: Run test to verify it passes**

Run: `pytest tests/mergers/test_external.py -v`
Expected: PASS (3 tests).

- [ ] **Step 3.5: Commit**

```bash
git add src/openapi_merger/mergers/external.py tests/mergers/test_external.py
git commit -m "feat: add shared subprocess helper for external mergers"
```

---

## Task 4: RedoclyMerger adapter

**Files:**
- Create: `src/openapi_merger/mergers/redocly.py`
- Create: `tests/mergers/test_redocly.py`

- [ ] **Step 4.1: Write the failing test**

Create `tests/mergers/test_redocly.py`:

```python
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
```

- [ ] **Step 4.2: Run test to verify it fails**

Run: `pytest tests/mergers/test_redocly.py -v`
Expected: FAIL — module not found.

- [ ] **Step 4.3: Implement RedoclyMerger**

Create `src/openapi_merger/mergers/redocly.py`:

```python
from __future__ import annotations
import shutil

from openapi_merger.mergers.external import run_external_merger, run_subprocess


class RedoclyMerger:
    """Adapter for `redocly join`.

    Hard-fails on path or operationId conflicts (the redocly join contract).
    Uses `--prefix-components-with-info-prop title` to disambiguate components.
    """

    key = "redocly"
    display_name = "Redocly CLI"
    binary = "redocly"

    def merge(self, sources: list[tuple[str, str, dict]], title: str, version: str) -> dict:
        binary_path = shutil.which(self.binary)
        if binary_path is None:
            raise RuntimeError(f"{self.binary} binary not found in PATH")

        def invoke(input_files, output_file, workdir):
            cmd = [
                binary_path,
                "join",
                *input_files,
                "-o", output_file,
                "--prefix-components-with-info-prop", "title",
            ]
            return run_subprocess(cmd, timeout=60)

        return run_external_merger(self.key, sources, title=title, version=version, invoke=invoke)

    @classmethod
    def is_available(cls) -> bool:
        return shutil.which(cls.binary) is not None
```

- [ ] **Step 4.4: Run test to verify it passes**

Run: `pytest tests/mergers/test_redocly.py -v`
Expected: PASS (3 tests).

- [ ] **Step 4.5: Commit**

```bash
git add src/openapi_merger/mergers/redocly.py tests/mergers/test_redocly.py
git commit -m "feat: add RedoclyMerger adapter"
```

---

## Task 5: SpeakeasyMerger adapter

**Files:**
- Create: `src/openapi_merger/mergers/speakeasy.py`
- Create: `tests/mergers/test_speakeasy.py`

- [ ] **Step 5.1: Write the failing test**

Create `tests/mergers/test_speakeasy.py`:

```python
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
```

- [ ] **Step 5.2: Run test to verify it fails**

Run: `pytest tests/mergers/test_speakeasy.py -v`
Expected: FAIL — module not found.

- [ ] **Step 5.3: Implement SpeakeasyMerger**

Create `src/openapi_merger/mergers/speakeasy.py`:

```python
from __future__ import annotations
import shutil

from openapi_merger.mergers.external import run_external_merger, run_subprocess


class SpeakeasyMerger:
    """Adapter for `speakeasy merge`.

    Uses last-wins semantics. Same-method/same-path/different-content collisions
    produce fragment paths (`/users#suffix`). OAuth2 scopes are unioned.
    """

    key = "speakeasy"
    display_name = "Speakeasy CLI"
    binary = "speakeasy"

    def merge(self, sources: list[tuple[str, str, dict]], title: str, version: str) -> dict:
        binary_path = shutil.which(self.binary)
        if binary_path is None:
            raise RuntimeError(f"{self.binary} binary not found in PATH")

        def invoke(input_files, output_file, workdir):
            cmd = [binary_path, "merge"]
            for path in input_files:
                cmd.extend(["-s", path])
            cmd.extend(["-o", output_file])
            return run_subprocess(cmd, timeout=60)

        return run_external_merger(self.key, sources, title=title, version=version, invoke=invoke)

    @classmethod
    def is_available(cls) -> bool:
        return shutil.which(cls.binary) is not None
```

- [ ] **Step 5.4: Run test to verify it passes**

Run: `pytest tests/mergers/test_speakeasy.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5.5: Commit**

```bash
git add src/openapi_merger/mergers/speakeasy.py tests/mergers/test_speakeasy.py
git commit -m "feat: add SpeakeasyMerger adapter"
```

---

## Task 6: OpenApiMergeMerger adapter

The CLI is `openapi-merge-cli` and is config-file driven (no direct file-list flags). The adapter writes a temporary `openapi-merge.json` config that points at the input files, then invokes the binary in that workdir.

**Files:**
- Create: `src/openapi_merger/mergers/openapi_merge.py`
- Create: `tests/mergers/test_openapi_merge.py`

- [ ] **Step 6.1: Write the failing test**

Create `tests/mergers/test_openapi_merge.py`:

```python
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
        workdir = cmd[-1] if cmd[-2] == "--config" else os.path.dirname(cmd[-1])
        cfg_path = cmd[cmd.index("--config") + 1] if "--config" in cmd else os.path.join(workdir, "openapi-merge.json")
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
```

- [ ] **Step 6.2: Run test to verify it fails**

Run: `pytest tests/mergers/test_openapi_merge.py -v`
Expected: FAIL — module not found.

- [ ] **Step 6.3: Implement OpenApiMergeMerger**

Create `src/openapi_merger/mergers/openapi_merge.py`:

```python
from __future__ import annotations
import json
import os
import shutil

from openapi_merger.mergers.external import run_external_merger, run_subprocess


class OpenApiMergeMerger:
    """Adapter for `openapi-merge-cli`.

    The CLI is config-file driven. We synthesize a minimal openapi-merge config
    that points at the pre-written input files in the workdir, then invoke the
    binary with `--config <path>`.
    """

    key = "openapi-merge"
    display_name = "openapi-merge"
    binary = "openapi-merge-cli"

    def merge(self, sources: list[tuple[str, str, dict]], title: str, version: str) -> dict:
        binary_path = shutil.which(self.binary)
        if binary_path is None:
            raise RuntimeError(f"{self.binary} binary not found in PATH")

        def invoke(input_files, output_file, workdir):
            config = {
                "inputs": [{"inputFile": path} for path in input_files],
                "output": output_file,
            }
            cfg_path = os.path.join(workdir, "openapi-merge.json")
            with open(cfg_path, "w") as f:
                json.dump(config, f)
            cmd = [binary_path, "--config", cfg_path]
            return run_subprocess(cmd, timeout=60)

        return run_external_merger(self.key, sources, title=title, version=version, invoke=invoke)

    @classmethod
    def is_available(cls) -> bool:
        return shutil.which(cls.binary) is not None
```

- [ ] **Step 6.4: Run test to verify it passes**

Run: `pytest tests/mergers/test_openapi_merge.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6.5: Commit**

```bash
git add src/openapi_merger/mergers/openapi_merge.py tests/mergers/test_openapi_merge.py
git commit -m "feat: add OpenApiMergeMerger adapter"
```

---

## Task 7: Merger registry + default_merger config

**Files:**
- Modify: `src/openapi_merger/mergers/__init__.py`
- Modify: `src/openapi_merger/config.py`
- Modify: `tests/test_config.py`
- Create: `tests/mergers/test_registry.py`

- [ ] **Step 7.1: Write the failing registry test**

Create `tests/mergers/test_registry.py`:

```python
from openapi_merger.mergers import MERGER_REGISTRY, get_merger


def test_registry_contains_all_four_mergers():
    assert set(MERGER_REGISTRY.keys()) == {"inhouse", "redocly", "speakeasy", "openapi-merge"}


def test_get_merger_returns_instance():
    inhouse = get_merger("inhouse")
    assert inhouse.key == "inhouse"


def test_get_merger_raises_on_unknown_key():
    import pytest
    with pytest.raises(KeyError):
        get_merger("not-a-merger")
```

- [ ] **Step 7.2: Write the failing config test**

Append to `tests/test_config.py`:

```python
import pytest
from openapi_merger.config import ServiceConfig, InfoConfig


def test_service_config_default_merger_defaults_to_inhouse():
    svc = ServiceConfig(info=InfoConfig(title="T", version="V"))
    assert svc.default_merger == "inhouse"


def test_service_config_default_merger_accepts_known_keys():
    for key in ("inhouse", "redocly", "speakeasy", "openapi-merge"):
        svc = ServiceConfig(info=InfoConfig(title="T", version="V"), default_merger=key)
        assert svc.default_merger == key


def test_service_config_default_merger_rejects_unknown():
    with pytest.raises(ValueError):
        ServiceConfig(info=InfoConfig(title="T", version="V"), default_merger="bogus")
```

- [ ] **Step 7.3: Run the tests to verify they fail**

Run: `pytest tests/mergers/test_registry.py tests/test_config.py -v -k "default_merger or registry"`
Expected: FAIL — registry attributes and config field do not exist yet.

- [ ] **Step 7.4: Implement the registry**

Replace `src/openapi_merger/mergers/__init__.py` with:

```python
from __future__ import annotations

from openapi_merger.mergers.base import MergerStrategy
from openapi_merger.mergers.inhouse import InhouseMerger
from openapi_merger.mergers.openapi_merge import OpenApiMergeMerger
from openapi_merger.mergers.redocly import RedoclyMerger
from openapi_merger.mergers.speakeasy import SpeakeasyMerger

MERGER_REGISTRY: dict[str, type] = {
    InhouseMerger.key: InhouseMerger,
    RedoclyMerger.key: RedoclyMerger,
    SpeakeasyMerger.key: SpeakeasyMerger,
    OpenApiMergeMerger.key: OpenApiMergeMerger,
}


def get_merger(key: str) -> MergerStrategy:
    cls = MERGER_REGISTRY[key]
    return cls()


__all__ = ["MERGER_REGISTRY", "get_merger", "MergerStrategy"]
```

- [ ] **Step 7.5: Add default_merger to ServiceConfig**

In `src/openapi_merger/config.py` replace the `ServiceConfig` class with:

```python
class ServiceConfig(BaseModel):
    port: int = 8080
    spec_path: str = "/openapi.json"
    default_merger: str = "inhouse"
    auth: AuthConfig | None = None
    info: InfoConfig

    @classmethod
    def _validate_default_merger(cls, v: str) -> str:
        valid = {"inhouse", "redocly", "speakeasy", "openapi-merge"}
        if v not in valid:
            raise ValueError(f"default_merger must be one of {sorted(valid)}, got '{v}'")
        return v

    def model_post_init(self, __context) -> None:
        self._validate_default_merger(self.default_merger)
```

Note: `model_post_init` performs the validation; pydantic v2 calls it after standard validation, which gives us a clear error message. Verify the exact pydantic v2 idiom against the existing codebase — if other models use `@field_validator`, use that style instead for consistency.

If consistency with other validators in this codebase prefers `@field_validator`, replace with:

```python
from pydantic import field_validator

class ServiceConfig(BaseModel):
    port: int = 8080
    spec_path: str = "/openapi.json"
    default_merger: str = "inhouse"
    auth: AuthConfig | None = None
    info: InfoConfig

    @field_validator("default_merger")
    @classmethod
    def _validate_default_merger(cls, v: str) -> str:
        valid = {"inhouse", "redocly", "speakeasy", "openapi-merge"}
        if v not in valid:
            raise ValueError(f"default_merger must be one of {sorted(valid)}, got '{v}'")
        return v
```

Use the `@field_validator` form — it integrates with pydantic's error machinery and the test `pytest.raises(ValueError)` will trigger via `ValidationError`.

- [ ] **Step 7.6: Run tests to verify they pass**

Run: `pytest tests/mergers/test_registry.py tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 7.7: Commit**

```bash
git add src/openapi_merger/mergers/__init__.py src/openapi_merger/config.py tests/mergers/test_registry.py tests/test_config.py
git commit -m "feat: register merger strategies and add default_merger config"
```

---

## Task 8: Wire endpoints in main.py

**Files:**
- Modify: `src/openapi_merger/main.py`
- Create: `tests/test_endpoints_per_merger.py`

- [ ] **Step 8.1: Write the failing integration test**

Create `tests/test_endpoints_per_merger.py`:

```python
import pytest
from fastapi.testclient import TestClient

from openapi_merger import main as main_module


@pytest.fixture
def app_with_stub_mergers(monkeypatch, tmp_path):
    """Boot the FastAPI app with stub mergers so all four endpoints succeed without real binaries."""
    svc_yaml = tmp_path / "service.yaml"
    svc_yaml.write_text(
        "spec_path: /openapi.json\n"
        "default_merger: inhouse\n"
        "info:\n  title: T\n  version: V\n"
    )
    src_yaml = tmp_path / "sources.yaml"
    src_yaml.write_text(
        "sources:\n"
        "  - name: alpha\n"
        "    url: http://alpha.invalid/spec\n"
        "    schema_prefix: A\n"
    )
    monkeypatch.setenv("SERVICE_CONFIG", str(svc_yaml))
    monkeypatch.setenv("SOURCES_CONFIG", str(src_yaml))

    async def _fake_fetch(source):
        return {
            "openapi": "3.0.0",
            "info": {"title": source.name, "version": "0.1"},
            "paths": {"/x": {"get": {"responses": {"200": {"description": "ok"}}}}},
            "components": {"schemas": {}},
        }
    monkeypatch.setattr("openapi_merger.orchestrator.fetch_spec", _fake_fetch)

    class _StubExternal:
        @classmethod
        def is_available(cls):
            return True

        def merge(self, sources, title, version):
            return {
                "openapi": "3.0.0",
                "info": {"title": title, "version": version},
                "paths": {name: {"get": {"responses": {"200": {"description": "ok"}}}} for name, _, _ in sources},
                "components": {"schemas": {}},
            }

    class _Redocly(_StubExternal):
        key = "redocly"
        display_name = "Redocly"

    class _Speakeasy(_StubExternal):
        key = "speakeasy"
        display_name = "Speakeasy"

    class _OpenApiMerge(_StubExternal):
        key = "openapi-merge"
        display_name = "openapi-merge"

    from openapi_merger.mergers.inhouse import InhouseMerger
    fake_registry = {
        "inhouse": InhouseMerger,
        "redocly": _Redocly,
        "speakeasy": _Speakeasy,
        "openapi-merge": _OpenApiMerge,
    }
    monkeypatch.setattr("openapi_merger.main.MERGER_REGISTRY", fake_registry)

    with TestClient(main_module.app) as client:
        yield client


def test_all_four_endpoints_return_200(app_with_stub_mergers):
    for path in ("/inhouse/openapi.json", "/redocly/openapi.json", "/speakeasy/openapi.json", "/openapi-merge/openapi.json"):
        r = app_with_stub_mergers.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code} {r.text[:200]}"
        body = r.json()
        assert body["info"]["title"] == "T"


def test_root_spec_path_aliases_to_default_merger(app_with_stub_mergers):
    r = app_with_stub_mergers.get("/openapi.json")
    assert r.status_code == 200
    body = r.json()
    assert body["info"]["title"] == "T"


def test_admin_clear_clears_all_caches(app_with_stub_mergers):
    for path in ("/inhouse/openapi.json", "/redocly/openapi.json"):
        app_with_stub_mergers.get(path)
    r = app_with_stub_mergers.post("/admin/cache/clear")
    assert r.status_code == 204
```

- [ ] **Step 8.2: Run test to verify it fails**

Run: `pytest tests/test_endpoints_per_merger.py -v`
Expected: FAIL — endpoints not yet registered.

- [ ] **Step 8.3: Modify main.py**

Replace the lifespan and module-level state in `src/openapi_merger/main.py`. The full replacement of `main.py`:

```python
import importlib.metadata
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager

import structlog
import yaml
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from openapi_merger.build_info import collect_build_info
from openapi_merger.config import ServiceConfig, load_service_config, load_sources_config
from openapi_merger.logging_config import configure_logging
from openapi_merger.mergers import MERGER_REGISTRY
from openapi_merger.orchestrator import MergeOrchestrator

log = structlog.get_logger()

_REQUEST_ID_RE = re.compile(r"[^A-Za-z0-9._\-]")


def _sanitize_request_id(raw: str | None) -> str:
    if not raw:
        return uuid.uuid4().hex
    cleaned = _REQUEST_ID_RE.sub("", raw.strip())[:64]
    return cleaned or uuid.uuid4().hex


_security = HTTPBasic(auto_error=False)

_service_config: ServiceConfig | None = None
_orchestrators: dict[str, MergeOrchestrator] = {}


def _check_auth(service_config: ServiceConfig, credentials: HTTPBasicCredentials | None) -> None:
    if service_config.auth is None:
        return
    if credentials is None:
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})
    valid = secrets.compare_digest(credentials.username, service_config.auth.username) and \
            secrets.compare_digest(credentials.password, service_config.auth.password)
    if not valid:
        raise HTTPException(status_code=401)


def _make_spec_handler(merger_key: str):
    async def _get_spec(
        format: str = Query("json"),
        refresh: bool = Query(False),
        credentials: HTTPBasicCredentials | None = Depends(_security),
    ):
        _check_auth(_service_config, credentials)
        orch = _orchestrators.get(merger_key)
        if orch is None:
            raise HTTPException(status_code=503, detail=f"merger '{merger_key}' is not available (binary missing?)")
        if format not in ("json", "yaml"):
            raise HTTPException(status_code=400, detail=f"Unknown format '{format}'. Use 'json' or 'yaml'.")
        try:
            merged = await orch.get_merged(refresh=refresh)
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e))
        if format == "yaml":
            return Response(content=yaml.dump(merged, allow_unicode=True), media_type="text/yaml")
        return merged
    return _get_spec


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _service_config, _orchestrators
    configure_logging()
    log.info("app.build_info", **collect_build_info())
    svc_path = os.getenv("SERVICE_CONFIG", "/config/service.yaml")
    src_path = os.getenv("SOURCES_CONFIG", "/config/sources.yaml")
    _service_config = load_service_config(svc_path)
    sources_config = load_sources_config(src_path)

    _orchestrators = {}
    for key, cls in MERGER_REGISTRY.items():
        available = cls.is_available()
        if not available:
            log.warning("merger.unavailable", merger=key, binary=getattr(cls, "binary", None))
            continue
        _orchestrators[key] = MergeOrchestrator(_service_config, sources_config, strategy=cls())

    if _service_config.default_merger not in _orchestrators:
        log.warning(
            "default_merger.unavailable",
            default_merger=_service_config.default_merger,
            available=sorted(_orchestrators.keys()),
        )

    log.info(
        "app.startup",
        service_config=svc_path,
        sources_config=src_path,
        spec_path=_service_config.spec_path,
        default_merger=_service_config.default_merger,
        available_mergers=sorted(_orchestrators.keys()),
        sources_count=len(sources_config.sources),
        auth_enabled=_service_config.auth is not None,
        version=importlib.metadata.version("openapi-merger"),
    )

    for key in MERGER_REGISTRY:
        path = f"/{key}{_service_config.spec_path}"
        app.add_api_route(path, _make_spec_handler(key), methods=["GET"])

    async def _default_handler(
        format: str = Query("json"),
        refresh: bool = Query(False),
        credentials: HTTPBasicCredentials | None = Depends(_security),
    ):
        handler = _make_spec_handler(_service_config.default_merger)
        return await handler(format=format, refresh=refresh, credentials=credentials)

    app.add_api_route(_service_config.spec_path, _default_handler, methods=["GET"])

    async def _clear_cache(
        credentials: HTTPBasicCredentials | None = Depends(_security),
    ):
        _check_auth(_service_config, credentials)
        for orch in _orchestrators.values():
            orch.clear_cache()
        return Response(status_code=204)

    app.add_api_route("/admin/cache/clear", _clear_cache, methods=["POST"])
    yield
    log.info("app.shutdown")


app = FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)


@app.middleware("http")
async def _request_log_middleware(request: Request, call_next):
    request_id = _sanitize_request_id(request.headers.get("x-request-id"))
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = int((time.perf_counter() - start) * 1000)
        log.exception("request.failed", method=request.method, path=request.url.path, duration_ms=duration_ms)
        raise
    duration_ms = int((time.perf_counter() - start) * 1000)
    log.info("request.completed", method=request.method, path=request.url.path, status=response.status_code, duration_ms=duration_ms)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 8.4: Run the new test + the full suite**

Run: `pytest tests/test_endpoints_per_merger.py -v`
Expected: PASS (3 tests).

Run: `pytest tests/ -v --ignore=tests/e2e`
Expected: PASS — all preexisting tests continue to work (the only behavior change is the addition of new endpoints; the existing `spec_path` still serves the default merger, which defaults to `inhouse`, which delegates to `merge_specs` — same output as today).

- [ ] **Step 8.5: Commit**

```bash
git add src/openapi_merger/main.py tests/test_endpoints_per_merger.py
git commit -m "feat: register per-merger endpoints and alias spec_path to default"
```

---

## Task 9: Multi-stage Dockerfile with all three external binaries

**Files:**
- Modify: `Dockerfile`

Pin these versions (update if newer stable releases exist at execution time, but keep version pinning):
- Speakeasy CLI: `v1.605.0` (Go binary from GitHub releases)
- `@redocly/cli`: `1.34.5` (npm)
- `openapi-merge-cli`: `1.3.3` (npm — last released version; stale upstream is acceptable for an experiment)

- [ ] **Step 9.1: Replace the Dockerfile**

Replace `Dockerfile` with:

```dockerfile
# syntax=docker/dockerfile:1.6

# Stage 1: download Speakeasy binary.
FROM debian:bookworm-slim AS speakeasy
ARG SPEAKEASY_VERSION=1.605.0
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) SE_ARCH=linux_amd64 ;; \
      arm64) SE_ARCH=linux_arm64 ;; \
      *) echo "unsupported arch ${TARGETARCH}"; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/speakeasy-api/speakeasy/releases/download/v${SPEAKEASY_VERSION}/speakeasy_${SPEAKEASY_VERSION}_${SE_ARCH}.zip" -o /tmp/speakeasy.zip; \
    apt-get update && apt-get install -y --no-install-recommends unzip && rm -rf /var/lib/apt/lists/*; \
    unzip /tmp/speakeasy.zip -d /tmp/speakeasy; \
    install -m 0755 /tmp/speakeasy/speakeasy /usr/local/bin/speakeasy; \
    /usr/local/bin/speakeasy --version

# Stage 2: runtime image.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app

ARG REDOCLY_VERSION=1.34.5
ARG OPENAPI_MERGE_CLI_VERSION=1.3.3

# Install Node.js (for redocly + openapi-merge-cli) and the two CLIs.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g "@redocly/cli@${REDOCLY_VERSION}" "openapi-merge-cli@${OPENAPI_MERGE_CLI_VERSION}" \
 && apt-get purge -y --auto-remove curl gnupg \
 && rm -rf /var/lib/apt/lists/* /root/.npm

# Speakeasy binary from stage 1.
COPY --from=speakeasy /usr/local/bin/speakeasy /usr/local/bin/speakeasy

# Verify all three binaries are on PATH.
RUN redocly --version && openapi-merge-cli --version && speakeasy --version

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir -e .

ARG BUILD_GIT_SHA=unknown
ARG BUILD_GIT_BRANCH=unknown
ARG BUILD_TIME=unknown
ENV BUILD_GIT_SHA=${BUILD_GIT_SHA}
ENV BUILD_GIT_BRANCH=${BUILD_GIT_BRANCH}
ENV BUILD_TIME=${BUILD_TIME}

EXPOSE 8080
ENV SERVICE_CONFIG=/config/service.yaml
ENV SOURCES_CONFIG=/config/sources.yaml
CMD ["uvicorn", "openapi_merger.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

- [ ] **Step 9.2: Build the image locally**

Run: `docker build -t openapi-merger:pluggable-mergers --build-arg BUILD_TIME="$(date -u +%FT%TZ)" .`
Expected: build succeeds and the final `RUN redocly --version && openapi-merge-cli --version && speakeasy --version` prints three version strings.

- [ ] **Step 9.3: Smoke-test the image starts**

Run:
```bash
docker run --rm -e SERVICE_CONFIG=/tmp/svc.yaml -e SOURCES_CONFIG=/tmp/src.yaml \
  -v "$PWD/example:/config" openapi-merger:pluggable-mergers \
  /bin/sh -c "redocly --version && speakeasy --version && openapi-merge-cli --version"
```
Expected: three version strings, exit 0.

- [ ] **Step 9.4: Commit**

```bash
git add Dockerfile
git commit -m "feat: bundle redocly, speakeasy, and openapi-merge binaries in Docker image"
```

---

## Task 10: E2E test exercising all four endpoints with real binaries

**Files:**
- Create: `tests/e2e/test_pluggable_endpoints.py`

These tests run only when the binaries are installed locally; they are skipped otherwise so CI without binaries does not fail.

- [ ] **Step 10.1: Create the e2e test file**

Create `tests/e2e/test_pluggable_endpoints.py`:

```python
import shutil
import pytest
from fastapi.testclient import TestClient

from openapi_merger import main as main_module


_SPEC_A = {
    "openapi": "3.0.0",
    "info": {"title": "A", "version": "0.1"},
    "paths": {"/users": {"get": {"operationId": "listUsers", "responses": {"200": {"description": "ok"}}}}},
    "components": {"schemas": {"User": {"type": "object", "properties": {"id": {"type": "string"}}}}},
}
_SPEC_B = {
    "openapi": "3.0.0",
    "info": {"title": "B", "version": "0.1"},
    "paths": {"/orders": {"get": {"operationId": "listOrders", "responses": {"200": {"description": "ok"}}}}},
    "components": {"schemas": {"Order": {"type": "object", "properties": {"id": {"type": "string"}}}}},
}


@pytest.fixture
def app_with_real_mergers(monkeypatch, tmp_path):
    svc_yaml = tmp_path / "service.yaml"
    svc_yaml.write_text(
        "spec_path: /openapi.json\n"
        "default_merger: inhouse\n"
        "info:\n  title: Merged\n  version: 1.0.0\n"
    )
    src_yaml = tmp_path / "sources.yaml"
    src_yaml.write_text(
        "sources:\n"
        "  - name: alpha\n    url: http://alpha.invalid\n    schema_prefix: A\n"
        "  - name: beta\n    url: http://beta.invalid\n    schema_prefix: B\n"
    )
    monkeypatch.setenv("SERVICE_CONFIG", str(svc_yaml))
    monkeypatch.setenv("SOURCES_CONFIG", str(src_yaml))

    async def _fake_fetch(source):
        return _SPEC_A if source.name == "alpha" else _SPEC_B
    monkeypatch.setattr("openapi_merger.orchestrator.fetch_spec", _fake_fetch)

    with TestClient(main_module.app) as client:
        yield client


def _skip_if_missing(binary: str) -> None:
    if shutil.which(binary) is None:
        pytest.skip(f"{binary} binary not installed; skipping e2e test")


def test_inhouse_endpoint_real(app_with_real_mergers):
    r = app_with_real_mergers.get("/inhouse/openapi.json")
    assert r.status_code == 200
    assert "/users" in r.json()["paths"]
    assert "/orders" in r.json()["paths"]


def test_redocly_endpoint_real(app_with_real_mergers):
    _skip_if_missing("redocly")
    r = app_with_real_mergers.get("/redocly/openapi.json")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["info"] == {"title": "Merged", "version": "1.0.0"}
    assert "/users" in body["paths"]
    assert "/orders" in body["paths"]


def test_speakeasy_endpoint_real(app_with_real_mergers):
    _skip_if_missing("speakeasy")
    r = app_with_real_mergers.get("/speakeasy/openapi.json")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["info"] == {"title": "Merged", "version": "1.0.0"}
    assert "/users" in body["paths"]
    assert "/orders" in body["paths"]


def test_openapi_merge_endpoint_real(app_with_real_mergers):
    _skip_if_missing("openapi-merge-cli")
    r = app_with_real_mergers.get("/openapi-merge/openapi.json")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["info"] == {"title": "Merged", "version": "1.0.0"}
    assert "/users" in body["paths"]
    assert "/orders" in body["paths"]
```

- [ ] **Step 10.2: Run the e2e tests**

If you have the binaries installed locally: `pytest tests/e2e/test_pluggable_endpoints.py -v`
Expected: 4 pass; or any binary-gated test is skipped if that binary is missing.

Inside the Docker image (preferred for full coverage):
```bash
docker build -t openapi-merger:test .
docker run --rm openapi-merger:test pytest tests/e2e/test_pluggable_endpoints.py -v
```
Expected: 4 pass.

- [ ] **Step 10.3: Commit**

```bash
git add tests/e2e/test_pluggable_endpoints.py
git commit -m "test: e2e coverage for all four merger endpoints"
```

---

## Task 11: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 11.1: Document the new endpoints and behaviors**

Update `README.md` to add the following sections (place them after the existing "What it does" section):

```markdown
## Merge endpoints

The service exposes four merge endpoints. Each runs the same fetch + transform + cache pipeline but plugs a different merge engine:

| Endpoint | Engine | Path collision | Component collision | Security scheme |
|---|---|---|---|---|
| `/inhouse/openapi.json` | built-in | 502 error | source-prefix rename | source-prefix rename |
| `/redocly/openapi.json` | `redocly join` | 502 error | `--prefix-components-with-info-prop title` | 502 error |
| `/speakeasy/openapi.json` | `speakeasy merge` | fragment paths (`#suffix`) | last-wins + warning | last-wins |
| `/openapi-merge/openapi.json` | `openapi-merge-cli` | dispute prefix | dispute prefix | first-wins |

The root `spec_path` (default `/openapi.json`) is aliased to the engine named by `default_merger` in `service.yaml`.

### Switching the default engine

In `service.yaml`:

```yaml
spec_path: /openapi.json
default_merger: speakeasy   # one of: inhouse | redocly | speakeasy | openapi-merge
info:
  title: My Merged API
  version: 1.0.0
```

If `default_merger` is missing it defaults to `inhouse`.

### Binary availability

External engines are probed at startup. If a binary is missing, the corresponding endpoint returns HTTP 503; the rest of the service is unaffected. The bundled Docker image contains all three; if you run from source you can install on demand:

```bash
npm install -g @redocly/cli openapi-merge-cli
curl -fsSL https://go.speakeasy.com/cli-install.sh | sh
```
```

- [ ] **Step 11.2: Commit**

```bash
git add README.md
git commit -m "docs: document pluggable merger endpoints and default_merger config"
```

---

## Final verification

- [ ] **Step F.1: Run the full test suite**

Run: `pytest tests/ -v`
Expected: all pass; e2e tests for missing binaries are skipped.

- [ ] **Step F.2: Build the Docker image**

Run: `docker build -t openapi-merger:final .`
Expected: build succeeds.

- [ ] **Step F.3: Verify all four endpoints in the image**

Boot the image against the `example/` config and `curl` each endpoint:

```bash
docker run --rm -d --name oapi -p 8080:8080 \
  -v "$PWD/example:/config" openapi-merger:final
sleep 2
for ep in inhouse redocly speakeasy openapi-merge; do
  echo "--- /$ep/openapi.json ---"
  curl -fsS "http://localhost:8080/$ep/openapi.json" | head -3
done
docker stop oapi
```

Expected: each endpoint returns OpenAPI JSON whose `info.title` matches the configured value.

---

## Out of scope (deliberately deferred)

- **Migrating the in-house merger to one of the externals.** This plan only adds new endpoints; the in-house engine stays intact for comparison.
- **Per-merger advanced config flags** (Speakeasy namespaces, Redocly tag prefixing, openapi-merge dispute prefix/suffix). Reused `sources.yaml` only; advanced flags can be added in a follow-up phase if a chosen engine wins.
- **Cross-merger diff endpoint.** Comparing outputs is a manual exercise for the experiment.
- **Per-merger cache invalidation.** `/admin/cache/clear` clears all four; per-merger granularity is not needed at this scope.
