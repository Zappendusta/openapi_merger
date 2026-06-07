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
