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
