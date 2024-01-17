import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path
from typing import Annotated, Any, Generator, NamedTuple, Optional

import requests
import typer
from ratelimit import limits, sleep_and_retry

from cachito.errors import NetworkError
from cachito.workers.config import get_worker_config
from cachito.workers.requests import get_requests_session

app = typer.Typer()
config = get_worker_config()
log = logging.getLogger(__name__)
session = get_requests_session()

ARCHIVE_DIR = Path(config.cachito_sources_dir)
ARCHIVE_PATTERN = re.compile(r"^[a-f0-9]{40}(-with-submodules)?\.tar\.gz$")
DEFAULT_AGE_DATETIME = datetime.now(timezone.utc) - timedelta(
    days=config.cachito_archives_default_age_days
)
MINIMUM_AGE_DATETIME = datetime.now(timezone.utc) - timedelta(
    days=config.cachito_archives_minimum_age_days
)
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


@dataclass(frozen=True)
class _ParsedArchive:
    """A source archive parsed from the filesystem."""

    path: Path
    repo_name: str
    ref: str

    @classmethod
    def from_path(cls, path: Path) -> "_ParsedArchive":
        repo_name = path.parent.relative_to(ARCHIVE_DIR).as_posix()
        ref = path.name[:40]
        return cls(path, repo_name, ref)


class _ResolvedArchive(NamedTuple):
    """A source archive matched to the most recent request for it."""

    path: Path
    created: datetime
    latest_request_id: int


@app.callback()
def configure_logging(verbose: bool = False):
    """Configure logging for the app."""
    log_level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    log.setLevel(log_level)
    log.addHandler(handler)


def _get_latest_request(archive: _ParsedArchive) -> Optional[dict[str, Any]]:
    """
    Find the latest request matching the _ParsedArchive via the API.

    Return None if no matching request is found.
    """
    url = f"{config.cachito_api_url.rstrip('/')}/requests/latest"
    params = {
        "repo_name": archive.repo_name,
        "ref": archive.ref,
    }

    try:
        response = session.get(url, params=params, timeout=config.cachito_api_timeout)
        response.raise_for_status()
    except requests.HTTPError:
        if response.status_code == 404:
            return None
        log.error(
            "The request to %s failed with the status code %d and the following text: %s",
            url,
            response.status_code,
            response.text,
        )
        raise NetworkError("Failed to query the cachito API")
    except requests.RequestException:
        msg = f"The connection failed when querying {url}"
        log.exception(msg)
        raise NetworkError(msg)

    return response.json()


def _get_parsed_source_archives(archive_dir: Path) -> Generator[_ParsedArchive, None, None]:
    """Return a _ParsedArchive for each source archive in ARCHIVE_DIR."""

    def is_valid_archive_filename(filename: str) -> bool:
        """Archive filename should match <sha1 hash>-<(optional)with-submodules>.tar.gz."""
        return re.match(ARCHIVE_PATTERN, filename) is not None

    for path in archive_dir.rglob("*.tar.gz"):
        if path.is_file() and is_valid_archive_filename(path.name):
            yield _ParsedArchive.from_path(path)
        else:
            log.debug("%s does not appear to be a source archive.", path)


def _resolve_source_archive(parsed_archive: _ParsedArchive) -> Optional[_ResolvedArchive]:
    """Return a _ResolvedArchive if a matching request is found via the API."""
    latest_request = _get_latest_request(parsed_archive)
    if latest_request is None:
        log.debug("Archive %s could not be resolved via the API.", parsed_archive.path)
        return None

    return _ResolvedArchive(
        parsed_archive.path,
        datetime.strptime(latest_request["created"], "%Y-%m-%dT%H:%M:%S.%f").replace(
            tzinfo=timezone.utc
        ),
        latest_request["id"],
    )


def _get_stale_archives(
    older_than: datetime, api_calls_per_second: int
) -> Generator[_ResolvedArchive, None, None]:
    """
    Return a Generator of _ResolvedArchives that are all stale.

    The API requests are ratelimited to prevent potentially overwhelming the API
    with a background maintenance task.
    """

    @sleep_and_retry
    @limits(calls=api_calls_per_second, period=1)
    def resolve_source_archive_ratelimited(archive: _ParsedArchive) -> Optional[_ResolvedArchive]:
        return _resolve_source_archive(archive)

    for parsed_archive in _get_parsed_source_archives(ARCHIVE_DIR):
        resolved_archive = resolve_source_archive_ratelimited(parsed_archive)
        if resolved_archive and resolved_archive.created < older_than:
            yield resolved_archive


def _process_stale_archives(
    older_than: datetime,
    api_calls_per_second: int,
    delete: bool = False,
    limit: Optional[int] = None,
) -> None:
    """List stale source archives up to the limit, optionally deleting them."""
    for archive in islice(_get_stale_archives(older_than, api_calls_per_second), limit):
        log.info(
            f"Archive {archive.path} is stale. The most recent request_id="
            f"{archive.latest_request_id} at {archive.created}"
        )
        if delete:
            log.info(f"Deleting {archive.path}")
            archive.path.unlink()


def _validate_older_than(older_than: Optional[datetime]) -> datetime:
    """Ensure that the value of the --older-than CLI option is not more recent than the minimum."""
    older_than_utc = (
        DEFAULT_AGE_DATETIME if older_than is None else older_than.astimezone(timezone.utc)
    )
    if older_than_utc > MINIMUM_AGE_DATETIME:
        raise typer.BadParameter(f"cannot be more recent than {MINIMUM_AGE_DATETIME}")
    return older_than_utc


@app.command("delete")
def delete_archives(
    older_than: Annotated[
        Optional[datetime],
        typer.Option(
            callback=_validate_older_than,
            formats=["%Y-%m-%d"],
            help="Deletes archives that are older than the specified date. YYYY-MM-DD",
        ),
    ] = None,
    api_calls_per_second: Annotated[
        int, typer.Option(min=1, max=5, help="The API requests-per-second limit.")
    ] = 2,
    limit: Annotated[
        Optional[int], typer.Option(min=1, help="The maximum number of stale archives to process.")
    ] = None,
    execute: Annotated[bool, typer.Option(help="Actual deletion will only occur if True.")] = False,
):
    """
    List and delete stale source archives.

    Actual deletion will not occur unless the --execute option is included.
    """
    # Needed to keep mypy happy. See the _validate_older_than callback
    if older_than is None:
        raise typer.BadParameter("--older-than cannot be None")

    _process_stale_archives(older_than, api_calls_per_second, delete=execute, limit=limit)


@app.command("list")
def list_archives(
    older_than: Annotated[
        Optional[datetime],
        typer.Option(
            callback=_validate_older_than,
            formats=["%Y-%m-%d"],
            help="Lists archives that are older than the specified date. YYYY-MM-DD",
        ),
    ] = None,
    api_calls_per_second: Annotated[
        int, typer.Option(min=1, max=5, help="The API requests-per-second limit.")
    ] = 2,
    limit: Annotated[
        Optional[int], typer.Option(min=1, help="The maximum number of stale archives to process.")
    ] = None,
):
    """List stale source archives."""
    # Needed to keep mypy happy. See the _validate_older_than callback
    if older_than is None:
        raise typer.BadParameter("--older-than cannot be None")

    _process_stale_archives(older_than, api_calls_per_second, delete=False, limit=limit)


if __name__ == "__main__":
    app()
