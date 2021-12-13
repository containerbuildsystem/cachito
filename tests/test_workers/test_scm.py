# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
import re
import subprocess
import tarfile
import zlib
from datetime import datetime
from unittest import mock

import git
import pytest

from cachito.errors import CachitoError
from cachito.workers import scm

url = "https://github.com/release-engineering/retrodep.git"
ref = "c50b93a32df1c9d700e3e80996845bc2e13be848"
archive_path = f"/tmp/cachito-archives/release-engineering/retrodep/{ref}.tar.gz"


def setup_module():
    """Re-enable logging that was disabled at some point in previous tests."""
    scm.log.disabled = False
    scm.log.setLevel(logging.DEBUG)


def test_repo_name():
    git_obj = scm.Git(url, ref)
    assert git_obj.repo_name == "release-engineering/retrodep"


@pytest.mark.parametrize(
    "gitsubmodule, shallow", [(True, False), (False, False), (True, True), (False, True)]
)
@mock.patch("tarfile.open")
@mock.patch("tempfile.TemporaryDirectory")
@mock.patch("tempfile.NamedTemporaryFile")
@mock.patch("git.repo.Repo.clone_from")
@mock.patch("cachito.workers.scm.run_cmd")
@mock.patch("os.path.exists")
@mock.patch("cachito.workers.scm.Git.update_git_submodules")
@mock.patch("os.link")
@mock.patch("os.fsync")
def test_clone_and_archive(
    mock_fsync,
    mock_link,
    mock_ugs,
    mock_exists,
    mock_fsck,
    mock_clone,
    mock_temp_file,
    mock_temp_dir,
    mock_tarfile_open,
    gitsubmodule,
    shallow,
):
    # Mock the archive being created
    mock_exists.return_value = True
    mock_tarfile = mock.Mock()
    mock_tarfile_open.return_value.__enter__.return_value = mock_tarfile
    # Mock the commit being returned from repo.commit(self.ref)
    mock_commit = mock.Mock()
    mock_clone.return_value.commit.return_value = mock_commit
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = "/tmp/cachito-temp"
    # Mock the tempfile.NamedTemporaryFile context manager
    mock_temp_file.return_value.__enter__.return_value = mock.Mock(
        name="/dev/null", fileno=lambda: -1
    )

    git_obj = scm.Git(url, ref)

    with mock.patch.object(git_obj.sources_dir, "archive_path", new=archive_path):
        git_obj.clone_and_archive(gitsubmodule, shallow)

    kwargs = {"depth": 1} if shallow else {}

    # Verify the tempfile.TemporaryDirectory context manager was used twice:
    # once for _clone_and_archive and once for _verify_archive
    assert mock_temp_dir.return_value.__enter__.call_count == 2
    # Verify the repo was cloned and checked out properly
    mock_clone.assert_called_once_with(
        url, "/tmp/cachito-temp/repo", no_checkout=True, env={"GIT_TERMINAL_PROMPT": "0"}, **kwargs
    )
    assert mock_clone.return_value.head.reference == mock_commit
    mock_clone.return_value.head.reset.assert_called_once_with(index=True, working_tree=True)
    # Verfiy the archive was created
    mock_tarfile.add.assert_called_once_with(mock_clone.return_value.working_dir, "app")
    # Verify the archive was verified
    mock_fsck.assert_called_once()
    # Verify the update_git_submodules was called correctly(if applicable)
    if gitsubmodule:
        mock_ugs.assert_called_once_with(mock_clone.return_value)
    else:
        mock_ugs.assert_not_called()
    # In case a shallow clone was made, we also need to fetch the exact commit needed
    if shallow:
        mock_clone.return_value.remote().fetch.assert_called_once_with(refspec=ref, **kwargs)

    mock_clone.return_value.git.gc.assert_called_once_with("--prune=now")


@pytest.mark.parametrize("gitsubmodule", [True, False])
@mock.patch("tempfile.TemporaryDirectory")
@mock.patch("git.repo.Repo.clone_from")
def test_clone_and_archive_clone_failed(mock_git_clone, mock_temp_dir, gitsubmodule):
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = "/tmp/cachito-temp"
    # Mock the git clone call
    mock_git_clone.side_effect = git.GitCommandError("some error", 1)

    git_obj = scm.Git(url, ref)
    with pytest.raises(CachitoError, match="Cloning the Git repository failed"):
        git_obj.clone_and_archive(gitsubmodule)


@pytest.mark.parametrize("gitsubmodule", [True, False])
@mock.patch("tempfile.TemporaryDirectory")
@mock.patch("git.repo.Repo.clone_from")
def test_clone_and_archive_checkout_failed(mock_git_clone, mock_temp_dir, gitsubmodule):
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
            git_obj.clone_and_archive(gitsubmodule)


@pytest.mark.parametrize("gitsubmodule", [True, False])
@mock.patch("cachito.workers.scm.Git._verify_archive")
def test_fetch_source_archive_exists(mock_verify, gitsubmodule):
    scm_git = scm.Git(url, ref)

    with mock.patch("pathlib.Path.exists", return_value=True):
        with mock.patch("pathlib.Path.glob") as glob:
            scm_git.fetch_source(gitsubmodule)
            glob.assert_not_called()


@pytest.mark.parametrize("gitsubmodule", [True, False])
@mock.patch("cachito.workers.scm.Git.clone_and_archive")
def test_fetch_source_clone_if_no_archive_yet(mock_clone_and_archive, gitsubmodule):
    scm_git = scm.Git(url, ref)

    po = mock.patch.object
    if gitsubmodule:
        with mock.patch("cachito.workers.scm.SourcesDir") as mock_scr:
            scm_git_submodule = scm.Git(url, f"{ref}-with-submodules")
            mock_scr.return_value = scm_git_submodule.sources_dir
            with po(scm_git_submodule.sources_dir.archive_path, "exists", return_value=False):
                with po(scm_git_submodule.sources_dir.package_dir, "glob", return_value=[]):
                    scm_git_submodule.fetch_source(gitsubmodule)
    else:
        with po(scm_git.sources_dir.archive_path, "exists", return_value=False):
            with po(scm_git.sources_dir.package_dir, "glob", return_value=[]):
                scm_git.fetch_source(gitsubmodule)

    mock_clone_and_archive.assert_called_once_with(gitsubmodule=gitsubmodule, shallow=False)


@pytest.mark.parametrize("gitsubmodule", [True, False])
@mock.patch("os.path.getctime")
@mock.patch("cachito.workers.scm.Git.update_and_archive")
def test_fetch_source_by_pull(mock_update_and_archive, mock_getctime, gitsubmodule):
    mock_getctime.side_effect = [
        datetime(2020, 3, 1, 20, 0, 0).timestamp(),
        datetime(2020, 3, 4, 10, 13, 30).timestamp(),
        datetime(2020, 3, 6, 10, 13, 30).timestamp(),
    ]

    scm_git = scm.Git(url, ref)

    po = mock.patch.object
    if gitsubmodule:
        with mock.patch("cachito.workers.scm.SourcesDir") as mock_scr:
            scm_git_submodule = scm.Git(url, f"{ref}-with-submodules")
            mock_scr.return_value = scm_git_submodule.sources_dir
            with po(scm_git_submodule.sources_dir.archive_path, "exists", return_value=False):
                with po(
                    scm_git_submodule.sources_dir.package_dir,
                    "glob",
                    return_value=[
                        "29eh2a.tar.gz",
                        "a8c2d2.tar.gz",
                        "a8c2d2-with-submodules.tar.gz",
                    ],
                ):
                    scm_git_submodule.fetch_source(gitsubmodule)
    else:
        with po(scm_git.sources_dir.archive_path, "exists", return_value=False):
            with po(
                scm_git.sources_dir.package_dir,
                "glob",
                return_value=["29eh2a.tar.gz", "a8c2d2.tar.gz", "a8c2d2-with-submodules.tar.gz"],
            ):
                scm_git.fetch_source(gitsubmodule)
    mock_update_and_archive.assert_called_once_with(
        "a8c2d2.tar.gz", gitsubmodule=gitsubmodule, shallow=False
    )


@pytest.mark.parametrize(
    "gitsubmodule, all_corrupt", ((True, True), (True, False), (False, True), (False, False))
)
@mock.patch("os.path.getctime")
@mock.patch("cachito.workers.scm.Git.update_and_archive")
@mock.patch("cachito.workers.scm.Git.clone_and_archive")
def test_fetch_source_by_pull_corrupt_archive(
    mock_clone_and_archive, mock_update_and_archive, mock_getctime, all_corrupt, gitsubmodule
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
    if gitsubmodule:
        with mock.patch("cachito.workers.scm.SourcesDir") as mock_scr:
            scm_git_submodule = scm.Git(url, f"{ref}-with-submodules")
            mock_scr.return_value = scm_git_submodule.sources_dir
            with po(scm_git_submodule.sources_dir.archive_path, "exists", return_value=False):
                with po(
                    scm_git_submodule.sources_dir.package_dir,
                    "glob",
                    return_value=["29eh2a.tar.gz", "a8c2d2.tar.gz"],
                ):
                    scm_git_submodule.fetch_source(gitsubmodule)
    else:
        with po(scm_git.sources_dir.archive_path, "exists", return_value=False):
            with po(
                scm_git.sources_dir.package_dir,
                "glob",
                return_value=["29eh2a.tar.gz", "a8c2d2.tar.gz"],
            ):
                scm_git.fetch_source(gitsubmodule)

    assert mock_update_and_archive.call_count == 2
    calls = [
        mock.call("a8c2d2.tar.gz", gitsubmodule=gitsubmodule, shallow=False),
        mock.call("29eh2a.tar.gz", gitsubmodule=gitsubmodule, shallow=False),
    ]
    mock_update_and_archive.assert_has_calls(calls)
    if all_corrupt:
        mock_clone_and_archive.assert_called_once_with(gitsubmodule=gitsubmodule, shallow=False)
    else:
        mock_clone_and_archive.assert_not_called()


@pytest.mark.parametrize(
    "gitsubmodule, shallow", [(True, False), (False, False), (True, True), (False, True)]
)
@mock.patch("tarfile.open")
@mock.patch("tempfile.TemporaryDirectory")
@mock.patch("git.Repo")
@mock.patch("cachito.workers.scm.run_cmd")
@mock.patch("os.path.exists")
@mock.patch("cachito.workers.scm.Git.update_git_submodules")
def test_update_and_archive(
    mock_ugs,
    mock_exists,
    mock_fsck,
    mock_repo,
    mock_temp_dir,
    mock_tarfile_open,
    gitsubmodule,
    shallow,
):
    # Mock the archive being created
    mock_exists.return_value = True
    mock_tarfile = mock.Mock()
    mock_tarfile_open.return_value.__enter__.return_value = mock_tarfile

    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = "/tmp/cachito-temp"

    kwargs = {"depth": 1} if shallow else {}

    # Test does not really extract this archive file. The filename could be arbitrary.
    scm.Git(url, ref).update_and_archive("/tmp/1234567.tar.gz", gitsubmodule, shallow)

    # Verify the tempfile.TemporaryDirectory context manager was used twice:
    # once for _update_and_archive and once for _verify_archive
    assert mock_temp_dir.return_value.__enter__.call_count == 2

    repo = mock_repo.return_value
    # Verify the changes are pulled.
    repo.remote.return_value.fetch.assert_called_once_with(refspec=ref, **kwargs)
    # Verify the repo is reset to specific ref
    repo.commit.assert_called_once_with(ref)
    assert repo.commit.return_value == repo.head.reference
    repo.head.reset.assert_called_once_with(index=True, working_tree=True)

    mock_tarfile.add.assert_called_once_with(mock_repo.return_value.working_dir, "app")
    # Verify the archive was verified
    mock_fsck.assert_called_once()
    # Verify the update_git_submodules was called correctly(if applicable)
    if gitsubmodule:
        mock_ugs.assert_called_once_with(repo)
    else:
        mock_ugs.assert_not_called()

    mock_repo.return_value.git.gc.assert_called_once_with("--prune=now")


@pytest.mark.parametrize("gitsubmodule", [True, False])
@mock.patch("tarfile.open")
@mock.patch("git.Repo")
def test_update_and_archive_pull_error(mock_repo, mock_tarfile_open, gitsubmodule):
    repo = mock_repo.return_value
    repo.remote.return_value.fetch.side_effect = OSError

    with pytest.raises(CachitoError, match="Failed to fetch from the remote Git repository"):
        scm.Git(url, ref).update_and_archive("/tmp/1234567.tar.gz", gitsubmodule)


def test_create_and_verify_archive(fake_repo, caplog):
    repo_dir, _ = fake_repo
    git_obj = scm.Git(f"file://{repo_dir}", "master")
    verify_log_msg = f"Verifying the archive at {git_obj.sources_dir.archive_path}"
    already_created_log_msg = (
        f"{git_obj.sources_dir.archive_path} was created while this task was running. "
        "Will proceed with that archive"
    )
    git_obj._create_archive(repo_dir)
    assert verify_log_msg in caplog.text
    assert already_created_log_msg not in caplog.text
    caplog.clear()
    # create archive again to simulate race condition. This should not generate errors
    git_obj._create_archive(repo_dir)
    assert verify_log_msg in caplog.text
    assert already_created_log_msg in caplog.text


def test_clone_and_verify_archive(fake_repo, caplog):
    repo_dir, _ = fake_repo
    git_obj = scm.Git(f"file://{repo_dir}", "master")
    git_obj.clone_and_archive()
    assert f"Verifying the archive at {git_obj.sources_dir.archive_path}" in caplog.text


def test_update_and_verify_archive(fake_repo, caplog):
    repo_dir, _ = fake_repo
    git_obj = scm.Git(f"file://{repo_dir}", "master")
    git_obj.clone_and_archive()
    caplog.clear()
    git_obj.update_and_archive(git_obj.sources_dir.archive_path)
    assert f"Verifying the archive at {git_obj.sources_dir.archive_path}" in caplog.text


@mock.patch("tarfile.is_tarfile")
def test_verify_invalid_archive(mock_istar, fake_repo):
    mock_istar.return_value = False
    repo_dir, _ = fake_repo
    git_obj = scm.Git(f"file://{repo_dir}", "master")
    err_msg = f"No valid archive found at {git_obj.sources_dir.archive_path}"
    with pytest.raises(CachitoError, match=err_msg):
        git_obj._verify_archive()


@pytest.mark.parametrize("exception_type", [OSError, zlib.error, tarfile.ExtractError])
@mock.patch("tarfile.TarFile.extractall")
def test_verify_corrupted_archive(mock_extract, fake_repo, exception_type, tmp_path):
    mock_extract.side_effect = exception_type("Something wrong with the tar archive")
    repo_dir, _ = fake_repo
    git_obj = scm.Git(f"file://{repo_dir}", "master")
    stub_file = tmp_path / "fake_contents"
    with open(stub_file, "w") as f:
        f.write("stub\n")

    with tarfile.open(git_obj.sources_dir.archive_path, "w:gz") as tar:
        tar.add(stub_file)

    err_msg = f"Invalid archive at {git_obj.sources_dir.archive_path}"
    with pytest.raises(CachitoError, match=err_msg):
        git_obj._verify_archive()


@mock.patch("cachito.workers.scm.run_cmd")
def test_verify_corrupted_git_repo(mock_fsck, fake_repo, tmp_path):
    mock_fsck.side_effect = subprocess.CalledProcessError(0, "stub command")
    repo_dir, _ = fake_repo
    git_obj = scm.Git(f"file://{repo_dir}", "master")
    stub_file = tmp_path / "fake_contents"
    with open(stub_file, "w") as f:
        f.write("stub\n")

    with tarfile.open(git_obj.sources_dir.archive_path, "w:gz") as tar:
        tar.add(stub_file)

    err_msg = f"Invalid archive at {git_obj.sources_dir.archive_path}"
    with pytest.raises(CachitoError, match=err_msg):
        git_obj._verify_archive()


def test_verify_archive_not_available():
    git_obj = scm.Git("invalid", "ref")
    err_msg = f"No valid archive found at {git_obj.sources_dir.archive_path}"
    with pytest.raises(CachitoError, match=err_msg):
        git_obj._verify_archive()


def test_verify_invalid_repo(fake_repo, tmp_path):
    repo_dir, _ = fake_repo
    git_obj = scm.Git(f"file://{repo_dir}", "master")
    # substitute the archive with a broken git repository
    os.unlink(os.path.join(repo_dir, ".git", "HEAD"))
    git_obj.sources_dir.archive_path = tmp_path / "archive.tar.gz"
    with tarfile.open(git_obj.sources_dir.archive_path, mode="w:gz") as bundle_archive:
        bundle_archive.add(repo_dir, "app")

    err_msg = f"Invalid archive at {git_obj.sources_dir.archive_path}"
    with pytest.raises(CachitoError, match=err_msg):
        git_obj._verify_archive()


def test_create_archive_verify_fails(fake_repo, caplog):
    repo_dir, _ = fake_repo
    git_obj = scm.Git(f"file://{repo_dir}", "master")
    # substitute the archive with a broken git repository
    os.unlink(os.path.join(repo_dir, ".git", "HEAD"))
    err_msg = f"Invalid archive at {git_obj.sources_dir.archive_path}"
    with pytest.raises(CachitoError, match=err_msg):
        git_obj._create_archive(repo_dir)
    # verify the archive was not created
    assert f"Removing invalid archive at {git_obj.sources_dir.archive_path}" in caplog.text
    assert not os.path.exists(git_obj.sources_dir.archive_path)


@pytest.mark.parametrize("gitsubmodule", [True, False])
@mock.patch("cachito.workers.scm.Git._verify_archive")
@mock.patch("cachito.workers.scm.Git.clone_and_archive")
def test_fetch_source_invalid_archive_exists(mock_clone, mock_verify, caplog, gitsubmodule):
    mock_verify.side_effect = [CachitoError("stub"), None]
    scm_git = scm.Git(url, ref)
    po = mock.patch.object
    if gitsubmodule:
        with mock.patch("cachito.workers.scm.SourcesDir") as mock_scr:
            scm_git_submodule = scm.Git(url, f"{ref}-with-submodules")
            mock_scr.return_value = scm_git_submodule.sources_dir
            with po(scm_git_submodule.sources_dir.archive_path, "exists", return_value=True):
                with po(scm_git_submodule.sources_dir.package_dir, "glob") as glob:
                    scm_git_submodule.fetch_source(gitsubmodule)
        glob.assert_called_once()
        msg = f'The archive at "{scm_git_submodule.sources_dir.archive_path}" is '
        "invalid and will be re-created"
        assert msg in caplog.text
    else:
        with po(scm_git.sources_dir.archive_path, "exists", return_value=True):
            with po(scm_git.sources_dir.package_dir, "glob") as glob:
                scm_git.fetch_source(gitsubmodule)
        glob.assert_called_once()
        msg = (
            f'The archive at "{scm_git.sources_dir.archive_path}" is invalid and will be re-created'
        )
        assert msg in caplog.text
    mock_clone.assert_called_once()


@mock.patch("git.Repo")
def test_update_git_submodules(mock_repo):
    git_obj = scm.Git(url, ref)
    git_obj.update_git_submodules(mock_repo)
    # Verify the git submodule update was called correctly
    mock_repo.submodule_update.assert_called_once_with(recursive=False)


@mock.patch("git.Repo")
def test_update_git_submodules_failed(mock_repo):
    repo = mock_repo.return_value
    # Set up the side effect for submodule_update call
    repo.submodule_update.side_effect = git.GitCommandError("some error", 1)

    expected = re.escape("Updating the Git submodule(s) failed")
    git_obj = scm.Git(url, ref)
    with pytest.raises(CachitoError, match=expected):
        git_obj.update_git_submodules(repo)
