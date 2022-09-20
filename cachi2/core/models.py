from pathlib import Path

from pydantic.dataclasses import dataclass


@dataclass
class Request:
    """Holds all data needed for the processing of a single request."""

    dep_replacements: tuple = ()
    flags: tuple = ()
    source_dir: Path = Path("./source")
    output_dir: Path = Path("./output")
    packages: tuple = ()

    # This is kept here temporarily, should be refactored
    go_mod_cache_download_part = Path("pkg", "mod", "cache", "download")

    # This is kept here temporarily, should be refactored
    @property
    def gomod_download_dir(self):
        """Directory where the fetched dependencies will be placed."""
        return self.output_dir / "deps" / "gomod" / self.go_mod_cache_download_part
