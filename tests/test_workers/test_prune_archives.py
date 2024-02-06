from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from unittest import mock

import pytest
import requests
from typer.testing import CliRunner

from cachito.errors import NetworkError
from cachito.workers.prune_archives import (
    _get_latest_request,
    _get_parsed_source_archives,
    _get_stale_archives,
    _ParsedArchive,
    _process_stale_archives,
    _resolve_source_archive,
    _ResolvedArchive,
    app,
)

runner = CliRunner()

LATEST_REQUEST_DATA = [
    {
        "id": 1,
        "created": "2024-01-01T00:00:00.000000",
    },
    None,
    {
        "id": 3,
        "created": "2024-01-03T00:00:00.000000",
    },
    {
        "id": 4,
        "created": "2024-01-03T00:00:00.000000",
    },
    {
        "id": 5,
        "created": "2024-01-05T00:00:00.000000",
    },
]


@pytest.fixture()
def archive_paths(tmp_path: Path) -> list[Path]:
    paths = [
        "my-org/not-an-archive.txt",
        "not-a-ref.tar.gz",
        "?ccccccccccccccccccccccccccccccccccccccc.tar.gz",
        "not-a-ref-with-submodules.tar.gz",
        "my-org/bar/cccccccccccccccccccccccccccccccccccccccc.tar.gz",
        "my-org/foo/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.tar.gz",
        "my-org/foo/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.tar.gz",
        "my-org/foo/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-with-submodules.tar.gz",
        "nested/my-org/baz/dddddddddddddddddddddddddddddddddddddddd.tar.gz",
    ]

    return [tmp_path / path for path in paths]


@pytest.fixture()
def archive_dir(tmp_path: Path, archive_paths: list[Path]) -> None:
    with mock.patch("cachito.workers.prune_archives.ARCHIVE_DIR", tmp_path):
        for path in archive_paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()

        yield tmp_path


@pytest.fixture()
def parsed_archives(archive_dir: Path):
    paths = [
        "my-org/bar/cccccccccccccccccccccccccccccccccccccccc.tar.gz",
        "my-org/foo/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.tar.gz",
        "my-org/foo/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.tar.gz",
        "my-org/foo/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-with-submodules.tar.gz",
        "nested/my-org/baz/dddddddddddddddddddddddddddddddddddddddd.tar.gz",
    ]

    return [_ParsedArchive.from_path(archive_dir / path) for path in paths]


@pytest.fixture()
def resolved_archives(archive_dir: Path):
    return [
        _ResolvedArchive(
            Path(archive_dir, "my-org/bar/cccccccccccccccccccccccccccccccccccccccc.tar.gz"),
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            1,
        ),
        None,
        _ResolvedArchive(
            Path(archive_dir, "my-org/foo/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.tar.gz"),
            datetime(2024, 1, 3, tzinfo=timezone.utc),
            3,
        ),
        _ResolvedArchive(
            Path(
                archive_dir,
                "my-org/foo/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-with-submodules.tar.gz",
            ),
            datetime(2024, 1, 3, tzinfo=timezone.utc),
            4,
        ),
        _ResolvedArchive(
            Path(archive_dir, "nested/my-org/baz/dddddddddddddddddddddddddddddddddddddddd.tar.gz"),
            datetime(2024, 1, 5, tzinfo=timezone.utc),
            5,
        ),
    ]


@pytest.fixture()
def stale_archives(archive_dir: Path):
    return [
        _ResolvedArchive(
            Path(archive_dir, "my-org/bar/cccccccccccccccccccccccccccccccccccccccc.tar.gz"),
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            1,
        ),
    ]


@mock.patch("cachito.workers.prune_archives.session.get")
def test_get_latest_request_not_found_test(mock_get_latest: mock.Mock):
    """Tests that get_latest_request returns None when the API responds with a 404."""
    mock_response = mock.MagicMock(status_code=404)
    mock_response.raise_for_status.side_effect = [requests.HTTPError()]
    mock_get_latest.return_value = mock_response
    result = _get_latest_request(mock.Mock())
    assert result is None


@mock.patch("cachito.workers.prune_archives.session.get")
def test_get_latest_request_timeout(mock_get_latest: mock.Mock):
    """Tests that get_latest_request raises NetworkError on a failed connection."""
    mock_get_latest.side_effect = requests.ConnectionError()
    expected = "The connection failed when querying"
    with pytest.raises(NetworkError, match=expected):
        _get_latest_request(mock.Mock())


@mock.patch("cachito.workers.prune_archives.session.get")
def test_get_latest_request_http_error(mock_get_latest: mock.Mock):
    """Tests that get_latest_request raises NetworkError for HTTP errors other than 404."""
    mock_response = mock.MagicMock(status_code=500)
    mock_response.raise_for_status.side_effect = [requests.HTTPError()]
    mock_get_latest.return_value = mock_response
    expected = "Failed to query the cachito API"
    with pytest.raises(NetworkError, match=expected):
        _get_latest_request(mock.Mock())


@mock.patch("pathlib.Path.rglob")
def test_get_parsed_source_archives(
    mock_rglob: mock.Mock,
    archive_dir: Path,
    archive_paths: list[Path],
    parsed_archives: list[_ParsedArchive],
):
    """Tests finding source archives on the filesystem and parsing them into _ParsedArchives."""
    mock_rglob.return_value = archive_paths
    result = _get_parsed_source_archives(archive_dir)
    assert list(result) == parsed_archives


@mock.patch("cachito.workers.prune_archives._get_latest_request")
def test_resolve_source_archive(mock_request: mock.Mock):
    """Tests resolving a ParsedArchive with request data from the API."""
    path = Path("my-org/my-project/ce60002604554992203f2afe17f23724f674b411.tar.gz")
    repo_name = path.parent.as_posix()
    ref = path.name[:40]
    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    latest_request_id = 1

    mock_request.return_value = {
        "created": datetime.strftime(created, format="%Y-%m-%dT%H:%M:%S.%f"),
        "id": latest_request_id,
    }
    parsed_archive = _ParsedArchive(path, repo_name, ref)
    expected_resolved_archive = _ResolvedArchive(path, created, latest_request_id)

    resolved_archive = _resolve_source_archive(parsed_archive)
    assert resolved_archive == expected_resolved_archive


@mock.patch("cachito.workers.prune_archives._get_latest_request")
def test_resolve_source_archive_not_found(mock_request: mock.Mock):
    """Tests when we cannot resolve a ParsedArchive with request data from the API."""
    path = Path("my-org/my-project/ce60002604554992203f2afe17f23724f674b411.tar.gz")
    repo_name = path.parent.as_posix()
    ref = path.name[:40]

    mock_request.return_value = None
    parsed_archive = _ParsedArchive(path, repo_name, ref)

    resolved_archive = _resolve_source_archive(parsed_archive)
    assert resolved_archive is None


@mock.patch("cachito.workers.prune_archives._get_latest_request")
def test_resolve_source_archive_no_created_date(mock_request: mock.Mock):
    """Tests resolving a ParsedArchive when request data is missing a created date."""
    path = Path("my-org/my-project/ce60002604554992203f2afe17f23724f674b411.tar.gz")
    repo_name = path.parent.as_posix()
    ref = path.name[:40]
    updated = datetime(2024, 1, 1, tzinfo=timezone.utc)
    latest_request_id = 1

    mock_request.return_value = {
        "created": None,
        "updated": datetime.strftime(updated, format="%Y-%m-%dT%H:%M:%S.%f"),
        "id": latest_request_id,
    }
    parsed_archive = _ParsedArchive(path, repo_name, ref)
    expected_resolved_archive = _ResolvedArchive(path, updated, latest_request_id)

    resolved_archive = _resolve_source_archive(parsed_archive)
    assert resolved_archive == expected_resolved_archive


@mock.patch("cachito.workers.prune_archives._get_latest_request")
def test_resolve_source_archive_no_age(mock_request: mock.Mock):
    """Tests when we cannot resolve a ParsedArchive because we can't determine the age."""
    path = Path("my-org/my-project/ce60002604554992203f2afe17f23724f674b411.tar.gz")
    repo_name = path.parent.as_posix()
    ref = path.name[:40]

    mock_request.return_value = {
        "id": 1,
    }
    parsed_archive = _ParsedArchive(path, repo_name, ref)

    resolved_archive = _resolve_source_archive(parsed_archive)
    assert resolved_archive is None


@mock.patch("cachito.workers.prune_archives._resolve_source_archive")
@mock.patch("cachito.workers.prune_archives._get_parsed_source_archives")
def test_get_stale_archives(
    mock_get_parsed_archives: mock.Mock,
    mock_resolve_archive: mock.Mock,
    parsed_archives: list[_ParsedArchive],
    resolved_archives: list[_ResolvedArchive],
    stale_archives: list[_ResolvedArchive],
):
    """Tests that get_stale_archives returns a list of _ResolvedArchives that are stale."""
    older_than = datetime(2024, 1, 3, 0, 0, 0, 0, tzinfo=timezone.utc)
    api_calls_per_second = 100
    mock_get_parsed_archives.return_value = parsed_archives
    mock_resolve_archive.side_effect = resolved_archives

    result = _get_stale_archives(older_than, api_calls_per_second)
    assert list(result) == stale_archives


@pytest.mark.parametrize("limit, expected_deletions", [(None, 2), (1, 1)])
@mock.patch("pathlib.Path.unlink")
@mock.patch("cachito.workers.prune_archives._get_stale_archives")
def test_process_stale_archives_limit(
    mock_get_stale: mock.Mock, mock_unlink: mock.Mock, limit: Optional[int], expected_deletions: int
):
    """Tests that _process_stale_archives adheres to the `--limit` CLI option."""
    archives = [
        _ResolvedArchive(Path("aaa"), datetime.now(), 1),
        _ResolvedArchive(Path("bbb"), datetime.now(), 2),
    ]
    mock_get_stale.return_value = archives
    older_than = datetime(2024, 1, 1)
    api_calls_per_second = 100

    _process_stale_archives(older_than, api_calls_per_second, delete=True, limit=limit)
    mock_get_stale.assert_called_once_with(older_than, api_calls_per_second)
    assert mock_unlink.call_count == expected_deletions


@mock.patch(
    "cachito.workers.prune_archives.MINIMUM_AGE_DATETIME", datetime(2024, 1, 3, tzinfo=timezone.utc)
)
@mock.patch("cachito.workers.prune_archives._get_latest_request")
@mock.patch("pathlib.Path.rglob")
def test_process_stale_archives_delete_e2e(
    mock_rglob: mock.Mock,
    mock_get_latest: mock.Mock,
    archive_dir: Path,
    archive_paths: list[Path],
    stale_archives: list[_ResolvedArchive],
):
    """Tests _process_stale_archives e2e and ensures that only stale archives are deleted."""
    mock_rglob.return_value = archive_paths
    mock_get_latest.side_effect = LATEST_REQUEST_DATA
    _process_stale_archives(
        datetime(2024, 1, 3, tzinfo=timezone.utc), api_calls_per_second=100, delete=True, limit=None
    )

    deleted_paths = {archive.path for archive in stale_archives}
    all_paths = {archive_dir / path for path in archive_paths}
    remaining_paths = all_paths - deleted_paths

    # Ensure that stale paths have been deleted and all others remain
    for path in deleted_paths:
        assert not path.exists()
    for path in remaining_paths:
        assert path.exists()


@mock.patch(
    "cachito.workers.prune_archives.MINIMUM_AGE_DATETIME", datetime(2024, 1, 3, tzinfo=timezone.utc)
)
@mock.patch("cachito.workers.prune_archives._get_latest_request")
@mock.patch("pathlib.Path.rglob")
def test_process_stale_archives_list_only_e2e(
    mock_rglob: mock.Mock,
    mock_get_latest: mock.Mock,
    archive_dir: Path,
    archive_paths: list[Path],
    stale_archives: list[_ResolvedArchive],
):
    """Tests _process_stale_archives e2e and ensures no archives are deleted if delete=False."""
    mock_rglob.return_value = archive_paths
    mock_get_latest.side_effect = LATEST_REQUEST_DATA
    _process_stale_archives(
        datetime(2024, 1, 3, tzinfo=timezone.utc),
        api_calls_per_second=100,
        delete=False,
        limit=None,
    )

    # Ensure that all archive paths still exist
    for path in (archive_dir / path for path in archive_paths):
        assert path.exists()


def test_prune_archives_app_no_command():
    """Tests invoking the app without a command."""
    result = runner.invoke(app, [])
    assert result.exit_code == 2
    assert "Error: Missing command." in result.stdout


@pytest.mark.parametrize("command", ["list", "delete"])
def test_prune_archives_app_invalid_date(command: mock.Mock):
    """Tests processing archives that are more recent than the minimum."""
    today = datetime.strftime(datetime.now(timezone.utc), "%Y-%m-%d")
    result = runner.invoke(app, [command, "--older-than", today])
    assert result.exit_code == 2
    assert "'--older-than': cannot be more recent than" in result.stdout


@mock.patch(
    "cachito.workers.prune_archives.MINIMUM_AGE_DATETIME", datetime(2024, 1, 3, tzinfo=timezone.utc)
)
@mock.patch(
    "cachito.workers.prune_archives.DEFAULT_AGE_DATETIME", datetime(2024, 1, 1, tzinfo=timezone.utc)
)
@mock.patch("cachito.workers.prune_archives._process_stale_archives")
def test_prune_archives_app_list(mock_process_stale: mock.Mock):
    """Tests the `list` CLI command with no options."""
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    mock_process_stale.assert_called_once_with(
        datetime(2024, 1, 1, tzinfo=timezone.utc), 2, delete=False, limit=None
    )


@mock.patch(
    "cachito.workers.prune_archives.MINIMUM_AGE_DATETIME", datetime(2024, 1, 3, tzinfo=timezone.utc)
)
@mock.patch(
    "cachito.workers.prune_archives.DEFAULT_AGE_DATETIME", datetime(2024, 1, 1, tzinfo=timezone.utc)
)
@mock.patch("cachito.workers.prune_archives._process_stale_archives")
def test_prune_archives_app_list_with_options(mock_process_stale: mock.Mock):
    """Tests the `list` CLI command with all options set to non-defaults."""
    older_than_local = datetime(2024, 1, 2)
    older_than_utc = older_than_local.astimezone(timezone.utc)

    result = runner.invoke(
        app,
        [
            "list",
            "--older-than",
            older_than_local.strftime("%Y-%m-%d"),
            "--api-calls-per-second",
            "1",
            "--limit",
            "4",
        ],
    )

    assert result.exit_code == 0
    mock_process_stale.assert_called_once_with(older_than_utc, 1, delete=False, limit=4)


@mock.patch(
    "cachito.workers.prune_archives.MINIMUM_AGE_DATETIME", datetime(2024, 1, 3, tzinfo=timezone.utc)
)
@mock.patch(
    "cachito.workers.prune_archives.DEFAULT_AGE_DATETIME", datetime(2024, 1, 1, tzinfo=timezone.utc)
)
@mock.patch("cachito.workers.prune_archives._process_stale_archives")
def test_prune_archives_app_delete(mock_process_stale: mock.Mock):
    """Tests the `delete` CLI command with no options."""
    result = runner.invoke(app, ["delete"])
    assert result.exit_code == 0
    mock_process_stale.assert_called_once_with(
        datetime(2024, 1, 1, tzinfo=timezone.utc), 2, delete=False, limit=None
    )


@mock.patch(
    "cachito.workers.prune_archives.MINIMUM_AGE_DATETIME", datetime(2024, 1, 3, tzinfo=timezone.utc)
)
@mock.patch(
    "cachito.workers.prune_archives.DEFAULT_AGE_DATETIME", datetime(2024, 1, 1, tzinfo=timezone.utc)
)
@mock.patch("cachito.workers.prune_archives._process_stale_archives")
def test_prune_archives_app_delete_with_options(mock_process_stale: mock.Mock):
    """Tests the `delete` CLI command with all options set to non-defaults."""
    older_than_local = datetime(2024, 1, 2)
    older_than_utc = older_than_local.astimezone(timezone.utc)

    result = runner.invoke(
        app,
        [
            "delete",
            "--older-than",
            older_than_local.strftime("%Y-%m-%d"),
            "--api-calls-per-second",
            "1",
            "--limit",
            "4",
            "--execute",
        ],
    )

    assert result.exit_code == 0
    mock_process_stale.assert_called_once_with(older_than_utc, 1, delete=True, limit=4)
