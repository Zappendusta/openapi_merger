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
