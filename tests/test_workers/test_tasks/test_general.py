# SPDX-License-Identifier: GPL-3.0-or-later
import json
import logging
import os
import os.path
import pathlib
import shutil
import tarfile
from contextlib import nullcontext
from unittest import mock

import pytest
from requests import Timeout

from cachito.common.checksum import hash_file
from cachito.errors import (
    FileAccessError,
    InvalidRequestData,
    NetworkError,
    RequestErrorOrigin,
    ValidationError,
)
from cachito.workers import tasks
from cachito.workers.paths import RequestBundleDir, SourcesDir
from cachito.workers.tasks.general import _enforce_sandbox, save_bundle_archive_checksum
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

    mock_enforce_sandbox.assert_called_once_with(bundle_dir.source_root_dir, False)

    # Clean up bundle dir after unpacking archive
    shutil.rmtree(bundle_dir)


@pytest.mark.parametrize(
    "file_tree, bad_symlink",
    [
        ({}, None),
        ({"symlink_to_self": Symlink(".")}, None),
        ({"subdir": {"symlink_to_parent": Symlink("..")}}, None),
        ({"symlink_to_subdir": Symlink("subdir/some_file"), "subdir": {"some_file": "foo"}}, None),
        ({"symlink_to_parent": Symlink("..")}, "symlink_to_parent"),
        ({"symlink_to_root": Symlink("/")}, "symlink_to_root"),
        (
            {"subdir": {"symlink_to_parent_parent": Symlink("../..")}},
            "subdir/symlink_to_parent_parent",
        ),
        ({"subdir": {"symlink_to_root": Symlink("/")}}, "subdir/symlink_to_root"),
    ],
)
def test_enforce_sandbox(file_tree, bad_symlink, tmp_path):
    write_file_tree(file_tree, tmp_path)
    if bad_symlink:
        error = f"The destination of {bad_symlink!r} is outside of cloned repository"
        with pytest.raises(ValidationError, match=error):
            _enforce_sandbox(tmp_path, remove_unsafe_symlinks=False)
        assert pathlib.Path(tmp_path / bad_symlink).exists()
        _enforce_sandbox(tmp_path, remove_unsafe_symlinks=True)
        assert not pathlib.Path(tmp_path / bad_symlink).exists()
    else:
        _enforce_sandbox(tmp_path, remove_unsafe_symlinks=False)
        _enforce_sandbox(tmp_path, remove_unsafe_symlinks=True)


def test_enforce_sandbox_symlink_loop(tmp_path, caplog):
    workers_logger = logging.getLogger("cachito.workers.tasks.general")
    workers_logger.disabled = False
    workers_logger.setLevel(logging.INFO)

    file_tree = {"foo_b": Symlink("foo_a"), "foo_a": Symlink("foo_b")}
    write_file_tree(file_tree, tmp_path)
    _enforce_sandbox(tmp_path, remove_unsafe_symlinks=True)
    assert "Symlink loop from " in caplog.text


@mock.patch("pathlib.Path.resolve")
def test_enforce_sandbox_runtime_error(mock_resolve, tmp_path, caplog):
    workers_logger = logging.getLogger("cachito.workers.tasks.general")
    workers_logger.disabled = False
    workers_logger.setLevel(logging.INFO)

    error = "RuntimeError is triggered"

    def side_effect():
        raise RuntimeError(error)

    mock_resolve.side_effect = side_effect

    file_tree = {"foo_b": Symlink("foo_a"), "foo_a": Symlink("foo_b")}
    write_file_tree(file_tree, tmp_path)
    with pytest.raises(RuntimeError, match=error):
        _enforce_sandbox(tmp_path, remove_unsafe_symlinks=True)


@pytest.mark.parametrize("gitsubmodule", [True, False])
@mock.patch("cachito.workers.tasks.general.set_request_state")
@mock.patch("cachito.workers.tasks.general.Git")
def test_fetch_app_source_request_timed_out(
    mock_git, mock_set_request_state, gitsubmodule, task_passes_state_check
):
    url = "https://github.com/release-engineering/retrodep.git"
    ref = "c50b93a32df1c9d700e3e80996845bc2e13be848"
    mock_git.return_value.fetch_source.side_effect = Timeout("The request timed out")
    with pytest.raises(NetworkError, match="The connection timed out while downloading the source"):
        tasks.fetch_app_source(url, ref, 1, gitsubmodule)


@mock.patch("cachito.workers.tasks.general.set_request_state")
def test_failed_request_callback(mock_set_request_state):
    exc = InvalidRequestData("some error")
    tasks.failed_request_callback(None, exc, None, 1)
    mock_set_request_state.assert_called_once_with(
        1, "failed", "some error", RequestErrorOrigin.client, "InvalidRequestData"
    )


@mock.patch("cachito.workers.tasks.general.set_request_state")
def test_failed_request_callback_unknown_error(mock_set_request_state):
    exc = ValueError("some error")
    tasks.failed_request_callback(None, exc, None, 1)
    mock_set_request_state.assert_called_once_with(
        1, "failed", "An unknown error occurred", RequestErrorOrigin.server, "UnknownError"
    )


@pytest.mark.parametrize("deps_present", (True, False))
@pytest.mark.parametrize("include_git_dir", (True, False))
@mock.patch("cachito.workers.tasks.general.set_request_state")
@mock.patch("cachito.workers.paths.get_worker_config")
def test_create_bundle_archive(
    mock_gwc, mock_set_request_state, deps_present, include_git_dir, tmpdir
):
    flags = ["include-git-dir"] if include_git_dir else []

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
    tasks.create_bundle_archive(request_id, flags)

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
    assert mock_set_request_state.call_count == 1
    mock_set_request_state.assert_called_once_with(
        request_id, "in_progress", "Assembling the bundle archive"
    )


GOMOD_PKG1 = {
    "name": "pkg1",
    "version": "1.0",
    "type": "gomod",
    "dependencies": [
        {
            "name": "golang.org/x/text/internal/tag",
            "type": "go-package",
            "version": "v0.0.0-20170915032832-14c0d48ead0c",
        },
    ],
}

NPM_PKG1 = {
    "name": "npm_pkg1",
    "version": "1.0",
    "type": "npm",
    "path": "pkg1",
    "dependencies": [{"dev": False, "name": "underscore", "type": "npm", "version": "1.12.0"}],
}

GIT_SUBMODULE_PKG = {
    "name": "pkg",
    "version": "http://host/#1234",
    "type": "git-submodule",
    "path": "pkg",
    "dependencies": [],
}


@pytest.mark.parametrize(
    "packages_data,expected",
    [
        [{"gomod": {"packages": []}}, {"packages": []}],
        [{"gomod": {"packages": [GOMOD_PKG1]}}, {"packages": [GOMOD_PKG1]}],
        [
            {"gomod": {"packages": [GOMOD_PKG1]}, "npm": {"packages": [NPM_PKG1]}},
            {"packages": [GOMOD_PKG1, NPM_PKG1]},
        ],
        [{"git-submodule": {"packages": [GIT_SUBMODULE_PKG]}}, {"packages": [GIT_SUBMODULE_PKG]}],
    ],
)
@mock.patch("cachito.workers.tasks.general.set_request_state")
@mock.patch("cachito.workers.paths.get_worker_config")
def test_aggregate_packages_data(
    get_worker_config, set_request_state, packages_data, expected, tmpdir
):
    get_worker_config.return_value.cachito_bundles_dir = tmpdir

    request_id = 1
    bundle_dir: RequestBundleDir = RequestBundleDir(request_id)

    for pkg_manager, data in packages_data.items():
        data_file = getattr(bundle_dir, f"{pkg_manager.replace('-', '_')}_packages_data")
        with open(data_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

    tasks.aggregate_packages_data(request_id, list(packages_data.keys()))

    set_request_state.assert_called_once_with(
        request_id, "in_progress", "Aggregating packages data"
    )

    with open(bundle_dir.packages_data, "r", encoding="utf-8") as f:
        assert expected == json.load(f)


@mock.patch("cachito.workers.tasks.general.get_request")
@mock.patch("cachito.workers.tasks.general.create_bundle_archive")
@mock.patch("cachito.workers.tasks.general.aggregate_packages_data")
@mock.patch("cachito.workers.tasks.general.set_packages_and_deps_counts")
@mock.patch("cachito.workers.tasks.general.save_bundle_archive_checksum")
def test_process_fetched_sources(
    mock_save_bundle_archive_checksum,
    mock_set_counts,
    mock_aggregate_data,
    mock_create_archive,
    mock_get_request,
    task_passes_state_check,
):
    pkg = {"name": "foo", "version": "1.0", "type": "pip"}
    mock_get_request.return_value = {
        "flags": ["some-flag"],
        "pkg_managers": ["pip"],
        "packages": [pkg],
        "dependencies": [pkg, pkg],
    }

    mock_aggregate_data.return_value = mock.Mock(packages=[pkg], all_dependencies=[pkg, pkg])

    tasks.process_fetched_sources(42)

    mock_get_request.assert_called_once_with(42)
    mock_create_archive.assert_called_once_with(42, ["some-flag"])
    mock_save_bundle_archive_checksum.assert_called_once_with(42)
    mock_aggregate_data.assert_called_once_with(42, ["pip"])
    mock_set_counts.assert_called_once_with(42, 1, 2)


@mock.patch("cachito.workers.tasks.general.get_request_packages_and_dependencies")
@mock.patch("cachito.workers.tasks.general.set_request_state")
@pytest.mark.parametrize("expected_counts,raise_error", [[(1, 2), False], [(2, 3), True]])
def test_finalize_request(
    mock_set_state,
    mock_get_request_packages_and_dependencies,
    task_passes_state_check,
    expected_counts,
    raise_error,
):
    request_id = 42
    pkg = {"name": "foo", "version": "1.0", "type": "pip"}
    packages_data = {
        "packages": [pkg],
        "dependencies": [pkg, pkg],
    }

    error_message = f"Error checking packages data for request {request_id}."

    with raise_error and pytest.raises(InvalidRequestData, match=error_message) or nullcontext():
        mock_get_request_packages_and_dependencies.return_value = packages_data
        tasks.finalize_request(expected_counts, request_id)

    mock_get_request_packages_and_dependencies.assert_called_once_with(request_id)

    if not raise_error:
        mock_set_state.assert_called_once_with(request_id, "complete", "Completed successfully")


@mock.patch("cachito.workers.tasks.general.get_request_packages_and_dependencies")
@mock.patch("cachito.workers.tasks.general.set_request_state")
def test_finalize_request_with_error_when_fetching_api(
    mock_set_state, mock_get_request_packages_and_dependencies, task_passes_state_check,
):
    request_id = 42
    error_message = f"Packages file could not be loaded for request {request_id}"

    def side_effect(*args):
        raise NetworkError(error_message)

    mock_get_request_packages_and_dependencies.side_effect = side_effect

    with pytest.raises(NetworkError, match=error_message):
        tasks.finalize_request((1, 2), request_id)

    mock_get_request_packages_and_dependencies.assert_called_once_with(42)
    mock_set_state.assert_not_called()


@pytest.mark.parametrize("bundle_archive_exists", [True, False])
@mock.patch("cachito.workers.paths.get_worker_config")
def test_save_bundle_archive_checksum(get_worker_config, bundle_archive_exists, tmpdir):
    request_id = 1
    get_worker_config.return_value = mock.Mock(cachito_bundles_dir=str(tmpdir))

    if bundle_archive_exists:
        bundle_dir = RequestBundleDir(request_id)
        file_content = b"1234"
        bundle_dir.bundle_archive_file.write_bytes(file_content)

        save_bundle_archive_checksum(request_id)

        expected_checksum = hash_file(bundle_dir.bundle_archive_file).hexdigest()
        assert expected_checksum == bundle_dir.bundle_archive_checksum.read_text(encoding="utf-8")
    else:
        with pytest.raises(FileAccessError, match=r"Bundle archive .+ does not exist"):
            save_bundle_archive_checksum(request_id)
