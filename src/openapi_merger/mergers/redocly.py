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
