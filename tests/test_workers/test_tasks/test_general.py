# SPDX-License-Identifier: GPL-3.0-or-later
import os
import os.path
import pathlib
import shutil
import tarfile
from unittest import mock

import pytest
from requests import Timeout

from cachito.errors import CachitoError, ValidationError
from cachito.workers import tasks
from cachito.workers.paths import RequestBundleDir, SourcesDir
from cachito.workers.tasks.general import _enforce_sandbox

from tests.helper_utils import Symlink, write_file_tree


@pytest.mark.parametrize("gitsubmodule", [True, False])
@mock.patch("cachito.workers.tasks.general.set_request_state")
@mock.patch("cachito.workers.tasks.general._enforce_sandbox")
def test_fetch_app_source(
    mock_enforce_sandbox, mock_set_request_state, fake_repo, gitsubmodule, task_passes_state_check
):
    request_id = 1

    repo_dir, repo_name = fake_repo
    tasks.fetch_app_source(f"file://{repo_dir}", "master", request_id, gitsubmodule)

    # Verify the archive file is created from fetched app source.
    sources_dir = SourcesDir(repo_name, "master")
    assert sources_dir.archive_path.name == "master.tar.gz"

    # Verify the archive file is extracted into request bundle directory.
    bundle_dir = RequestBundleDir(request_id)
    assert bundle_dir.joinpath("app", "readme.rst").exists()
    assert bundle_dir.joinpath("app", "main.py").exists()

    mock_enforce_sandbox.assert_called_once_with(bundle_dir.source_root_dir)

    # Clean up bundle dir after unpacking archive
    shutil.rmtree(bundle_dir)


@pytest.mark.parametrize(
    "file_tree, error",
    [
        ({}, None),
        ({"symlink_to_self": Symlink(".")}, None),
        ({"subdir": {"symlink_to_parent": Symlink("..")}}, None),
        ({"symlink_to_subdir": Symlink("subdir/some_file"), "subdir": {"some_file": "foo"}}, None),
        (
            {"symlink_to_parent": Symlink("..")},
            "The destination of 'symlink_to_parent' is outside of cloned repository",
        ),
        (
            {"symlink_to_root": Symlink("/")},
            "The destination of 'symlink_to_root' is outside of cloned repository",
        ),
        (
            {"subdir": {"symlink_to_parent_parent": Symlink("../..")}},
            "The destination of 'subdir/symlink_to_parent_parent' is outside of cloned repository",
        ),
        (
            {"subdir": {"symlink_to_root": Symlink("/")}},
            "The destination of 'subdir/symlink_to_root' is outside of cloned repository",
        ),
    ],
)
def test_enforce_sandbox(file_tree, error, tmp_path):
    write_file_tree(file_tree, tmp_path)
    if error is not None:
        with pytest.raises(ValidationError, match=error):
            _enforce_sandbox(tmp_path)
    else:
        _enforce_sandbox(tmp_path)


@pytest.mark.parametrize("gitsubmodule", [True, False])
@mock.patch("cachito.workers.tasks.general.set_request_state")
@mock.patch("cachito.workers.tasks.general.Git")
def test_fetch_app_source_request_timed_out(
    mock_git, mock_set_request_state, gitsubmodule, task_passes_state_check
):
    url = "https://github.com/release-engineering/retrodep.git"
    ref = "c50b93a32df1c9d700e3e80996845bc2e13be848"
    mock_git.return_value.fetch_source.side_effect = Timeout("The request timed out")
    with pytest.raises(CachitoError, match="The connection timed out while downloading the source"):
        tasks.fetch_app_source(url, ref, 1, gitsubmodule)


@mock.patch("cachito.workers.requests.requests_auth_session")
def test_set_request_state(mock_requests):
    mock_requests.patch.return_value.ok = True
    tasks.set_request_state(1, "complete", "Completed successfully")
    expected_payload = {"state": "complete", "state_reason": "Completed successfully"}
    mock_requests.patch.assert_called_once_with(
        "http://cachito.domain.local/api/v1/requests/1", json=expected_payload, timeout=60
    )


@mock.patch("cachito.workers.requests.requests_auth_session.patch")
def test_set_request_state_connection_failed(mock_requests_patch):
    mock_requests_patch.side_effect = Timeout("The request timed out")
    expected = 'The connection failed when setting the state to "complete" on request 1'
    with pytest.raises(CachitoError, match=expected):
        tasks.set_request_state(1, "complete", "Completed successfully")


@mock.patch("cachito.workers.requests.requests_auth_session")
def test_set_request_state_bad_status_code(mock_requests):
    mock_requests.patch.return_value.ok = False
    expected = 'Setting the state to "complete" on request 1 failed'
    with pytest.raises(CachitoError, match=expected):
        tasks.set_request_state(1, "complete", "Completed successfully")


@mock.patch("cachito.workers.tasks.general.set_request_state")
def test_failed_request_callback(mock_set_request_state, task_passes_state_check):
    exc = CachitoError("some error")
    tasks.failed_request_callback(None, exc, None, 1)
    mock_set_request_state.assert_called_once_with(1, "failed", "some error")


@mock.patch("cachito.workers.tasks.general.set_request_state")
def test_failed_request_callback_not_cachitoerror(mock_set_request_state, task_passes_state_check):
    exc = ValueError("some error")
    tasks.failed_request_callback(None, exc, None, 1)
    mock_set_request_state.assert_called_once_with(1, "failed", "An unknown error occurred")


@pytest.mark.parametrize("deps_present", (True, False))
@pytest.mark.parametrize("include_git_dir", (True, False))
@mock.patch("cachito.workers.tasks.general.set_request_state")
@mock.patch("cachito.workers.paths.get_worker_config")
def test_create_bundle_archive(
    mock_gwc, mock_set_request, deps_present, include_git_dir, tmpdir, task_passes_state_check
):
    flags = ["include-git-dir"] if include_git_dir else []
    mock_set_request.return_value = {"flags": flags}

    # Make the bundles and sources dir configs point to under the pytest managed temp dir
    bundles_dir = tmpdir.mkdir("bundles")
    mock_gwc.return_value.cachito_bundles_dir = str(bundles_dir)
    request_id = 3
    request_bundle_dir = bundles_dir.mkdir("temp").mkdir(str(request_id))

    # Create the extracted application source
    app_archive_contents = {
        "app/.git": b"some content",
        "app/pizza.go": b"Cheese Pizza",
        "app/all_systems.go": b"All Systems Go",
    }

    request_bundle_dir.mkdir("app")
    for name, data in app_archive_contents.items():
        file_path = os.path.join(str(request_bundle_dir), name)
        with open(file_path, "wb") as f:
            f.write(data)

    # Create the dependencies cache from the call to add_deps_to_bundle call from resolve_gomod
    deps_archive_contents = {
        "deps/gomod/pkg/mod/cache/download/server.com/dep1/@v/dep1.zip": b"dep1 archive",
        "deps/gomod/pkg/mod/cache/download/server.com/dep2/@v/dep2.zip": b"dep2 archive",
    }

    if deps_present:
        for name, data in deps_archive_contents.items():
            path = request_bundle_dir.join(name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").write(data)

    # Test the bundle is created when create_bundle_archive is called
    tasks.create_bundle_archive(request_id)
    bundle_archive_path = str(bundles_dir.join(f"{request_id}.tar.gz"))
    assert os.path.exists(bundle_archive_path)

    # Verify the contents of the assembled bundle archive
    with tarfile.open(bundle_archive_path, mode="r:*") as bundle_archive:
        bundle_contents = set(
            [
                path
                for path in bundle_archive.getnames()
                if pathlib.Path(path).suffix in (".go", ".zip") or os.path.basename(path) == ".git"
            ]
        )

        # Always make sure there is a deps directory. This will be empty if no deps were present.
        assert "deps" in bundle_archive.getnames()

    expected = set(app_archive_contents.keys())
    if not include_git_dir:
        # The .git folder must be excluded unless flag is used
        expected.remove("app/.git")
    if deps_present:
        expected |= set(deps_archive_contents.keys())

    assert bundle_contents == expected
    call1 = mock.call(request_id, "in_progress", "Assembling the bundle archive")
    call2 = mock.call(request_id, "complete", "Completed successfully")
    calls = [call1, call2]
    assert mock_set_request.call_count == len(calls)
    mock_set_request.assert_has_calls(calls)
