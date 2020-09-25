# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import datetime
from unittest import mock
import tarfile

import git
import pytest

from cachito.workers import scm
from cachito.errors import CachitoError

url = "https://github.com/release-engineering/retrodep.git"
ref = "c50b93a32df1c9d700e3e80996845bc2e13be848"
archive_path = f"/tmp/cachito-archives/release-engineering/retrodep/{ref}.tar.gz"
scm_git = scm.Git(url, ref)
scm_git_submodule = scm.Git(url, f"{ref}-with-submodule")


def test_repo_name():
    git_obj = scm.Git(url, ref)
    assert git_obj.repo_name == "release-engineering/retrodep"


@mock.patch("tarfile.open")
@mock.patch("tempfile.TemporaryDirectory")
@mock.patch("git.repo.Repo.clone_from")
@mock.patch("cachito.workers.scm.Git.process_git_submodule")
def test_clone_and_archive(mock_pgs, mock_clone, mock_temp_dir, mock_tarfile_open):
    # Mock the archive being created
    mock_tarfile = mock.Mock()
    mock_tarfile_open.return_value.__enter__.return_value = mock_tarfile
    # Mock the commit being returned from repo.commit(self.ref)
    mock_commit = mock.Mock()
    mock_clone.return_value.commit.return_value = mock_commit
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = "/tmp/cachito-temp"
    # Mock the submodules being returned from repo.submodules
    submodules = {"fake-submodule-1", "fake-submodule-2"}
    mock_clone.return_value.submodules = submodules

    git_obj = scm.Git(url, ref)

    with mock.patch.object(git_obj.sources_dir, "archive_path", new=archive_path):
        git_obj.clone_and_archive(True)

    # Verify the tempfile.TemporaryDirectory context manager was used
    mock_temp_dir.return_value.__enter__.assert_called_once()
    # Verify the repo was cloned and checked out properly
    mock_clone.assert_called_once_with(
        url, "/tmp/cachito-temp/repo", no_checkout=True, env={"GIT_TERMINAL_PROMPT": "0"}
    )
    assert mock_clone.return_value.head.reference == mock_commit
    mock_clone.return_value.head.reset.assert_called_once_with(index=True, working_tree=True)
    # Verfiy the archive was created
    mock_tarfile.add.assert_called_once_with(mock_clone.return_value.working_dir, "app")
    # Verify the process_git_submodule has correct calls
    assert mock_pgs.call_count == 2
    mock_pgs.assert_has_calls(
        [mock.call("fake-submodule-1"), mock.call("fake-submodule-2")], any_order=True
    )


@mock.patch("tempfile.TemporaryDirectory")
@mock.patch("git.repo.Repo.clone_from")
def test_clone_and_archive_clone_failed(mock_git_clone, mock_temp_dir):
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = "/tmp/cachito-temp"
    # Mock the git clone call
    mock_git_clone.side_effect = git.GitCommandError("some error", 1)

    git_obj = scm.Git(url, ref)
    with pytest.raises(CachitoError, match="Cloning the Git repository failed"):
        git_obj.clone_and_archive(False)


@mock.patch("tempfile.TemporaryDirectory")
@mock.patch("git.repo.Repo.clone_from")
def test_clone_and_archive_checkout_failed(mock_git_clone, mock_temp_dir):
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = "/tmp/cachito-temp"
    # Mock the git calls
    mock_git_clone.return_value.commit.side_effect = git.GitCommandError("commit is invalid", 1)

    git_obj = scm.Git(url, ref)
    expected = (
        "Checking out the Git repository failed. Please verify the supplied reference of "
        f'"{ref}" is valid.'
    )
    with mock.patch.object(git_obj.sources_dir, "archive_path", new=archive_path):
        with pytest.raises(CachitoError, match=expected):
            git_obj.clone_and_archive(False)


@mock.patch("tarfile.is_tarfile", return_value=True)
def test_fetch_source_archive_exists(mock_is_tarfile):
    scm_git = scm.Git(url, ref)

    po = mock.patch.object
    with po(scm_git.sources_dir.archive_path, "exists", return_value=True):
        with po(scm_git.sources_dir.package_dir, "glob") as glob:
            scm_git.fetch_source(False)
            glob.assert_not_called()


@mock.patch("cachito.workers.scm.Git.clone_and_archive")
def test_fetch_source_clone_if_no_archive_yet(mock_clone_and_archive):
    scm_git = scm.Git(url, ref)

    po = mock.patch.object
    with po(scm_git.sources_dir.archive_path, "exists", return_value=False):
        with po(scm_git.sources_dir.package_dir, "glob", return_value=[]):
            scm_git.fetch_source(False)

    mock_clone_and_archive.assert_called_once()


@mock.patch("os.path.getctime")
@mock.patch("cachito.workers.scm.Git.update_and_archive")
def test_fetch_source_by_pull(mock_update_and_archive, mock_getctime):
    mock_getctime.side_effect = [
        datetime(2020, 3, 1, 20, 0, 0).timestamp(),
        datetime(2020, 3, 4, 10, 13, 30).timestamp(),
    ]

    scm_git = scm.Git(url, ref)
    po = mock.patch.object
    with po(scm_git.sources_dir.archive_path, "exists", return_value=False):
        with po(
            scm_git.sources_dir.package_dir,
            "glob",
            return_value=["a8c2d2.tar.gz", "a8c2d2-with-submodule.tar.gz"],
        ):
            scm_git.fetch_source(False)

    mock_update_and_archive.assert_called_once_with("a8c2d2.tar.gz", False)


@mock.patch("cachito.workers.scm.Git.clone_and_archive")
@mock.patch("cachito.workers.scm.SourcesDir")
def test_fetch_source_by_pull_gitsubmodule_true(mock_scr, mock_clone_and_archive):
    mock_scr.return_value = scm_git_submodule.sources_dir
    scm_git.fetch_source(True)

    po = mock.patch.object
    with po(scm_git_submodule.sources_dir.archive_path, "exists", return_value=False):
        scm_git_submodule.fetch_source(True)

    mock_clone_and_archive.assert_called_once_with(True)


@pytest.mark.parametrize("all_corrupt", [True, False])
@mock.patch("os.path.getctime")
@mock.patch("cachito.workers.scm.Git.update_and_archive")
@mock.patch("cachito.workers.scm.Git.clone_and_archive")
def test_fetch_source_by_pull_corrupt_archive(
    mock_clone_and_archive, mock_update_and_archive, mock_getctime, all_corrupt
):
    if all_corrupt:
        mock_update_and_archive.side_effect = [git.exc.InvalidGitRepositoryError, OSError]
    else:
        mock_update_and_archive.side_effect = [tarfile.ExtractError, None]

    mock_getctime.side_effect = [
        datetime(2020, 3, 1, 20, 0, 0).timestamp(),
        datetime(2020, 3, 4, 10, 13, 30).timestamp(),
        datetime(2020, 3, 1, 20, 0, 0).timestamp(),
    ]

    scm_git = scm.Git(url, ref)

    po = mock.patch.object
    with po(scm_git.sources_dir.archive_path, "exists", return_value=False):
        with po(
            scm_git.sources_dir.package_dir, "glob", return_value=["29eh2a.tar.gz", "a8c2d2.tar.gz"]
        ):
            scm_git.fetch_source(False)

    assert mock_update_and_archive.call_count == 2
    calls = [mock.call("a8c2d2.tar.gz", False), mock.call("29eh2a.tar.gz", False)]
    mock_update_and_archive.assert_has_calls(calls)
    if all_corrupt:
        mock_clone_and_archive.assert_called_once()
    else:
        mock_clone_and_archive.assert_not_called()


@mock.patch("tarfile.open")
@mock.patch("tempfile.TemporaryDirectory")
@mock.patch("git.Repo")
def test_update_and_archive(mock_repo, mock_temp_dir, mock_tarfile_open):
    # Mock the archive being created
    mock_tarfile = mock.Mock()
    mock_tarfile_open.return_value.__enter__.return_value = mock_tarfile

    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = "/tmp/cachito-temp"

    # Test does not really extract this archive file. The filename could be arbitrary.
    scm.Git(url, ref).update_and_archive("/tmp/1234567.tar.gz", True)

    # Verify the tempfile.TemporaryDirectory context manager was used
    mock_temp_dir.return_value.__enter__.assert_called_once()

    repo = mock_repo.return_value
    # Verify the changes are pulled.
    repo.remote.return_value.fetch.assert_called_once_with(refspec=ref)
    # Verify the repo is reset to specific ref
    repo.commit.assert_called_once_with(ref)
    assert repo.commit.return_value == repo.head.reference
    repo.head.reset.assert_called_once_with(index=True, working_tree=True)

    mock_tarfile.add.assert_called_once_with(mock_repo.return_value.working_dir, "app")


@mock.patch("tarfile.open")
@mock.patch("git.Repo")
def test_update_and_archive_pull_error(mock_repo, mock_tarfile_open):
    repo = mock_repo.return_value
    repo.remote.return_value.fetch.side_effect = OSError

    with pytest.raises(CachitoError, match="Failed to fetch from the remote Git repository"):
        scm.Git(url, ref).update_and_archive("/tmp/1234567.tar.gz", False)


@mock.patch("git.repo.Repo.clone_from")
def test_process_git_submodule(mock_clone):
    # Mock the commit being returned from repo.commit(self.ref)
    mock_commit = mock.Mock()
    mock_clone.return_value.commit.return_value = mock_commit

    git_obj = scm.Git(url, ref)

    submodule = mock.Mock()
    submodule.url = "fake-url"
    submodule.abspath = "/tmp/cachito-temp/repo/submodule_path"
    submodule.hexsha = "#abc123"

    with mock.patch.object(git_obj.sources_dir, "archive_path", new=archive_path):
        git_obj.process_git_submodule(submodule)

    # Verify the repo was cloned and checked out properly
    mock_clone.assert_called_once_with(
        "fake-url",
        "/tmp/cachito-temp/repo/submodule_path",
        no_checkout=True,
        env={"GIT_TERMINAL_PROMPT": "0"},
    )
    assert mock_clone.return_value.head.reference == mock_commit
    mock_clone.return_value.head.reset.assert_called_once_with(index=True, working_tree=True)


@mock.patch("git.repo.Repo.clone_from")
def test_process_git_submodule_failed(mock_git_clone):
    # Mock the git clone call
    mock_git_clone.side_effect = git.GitCommandError("some error", 1)

    git_obj = scm.Git(url, ref)
    with pytest.raises(CachitoError, match="Cloning the Git repository failed"):
        git_obj.process_git_submodule(True)
