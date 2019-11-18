# SPDX-License-Identifier: GPL-3.0-or-later
from unittest import mock

import git
import pytest

from cachito.workers import scm
from cachito.errors import CachitoError

url = 'https://github.com/release-engineering/retrodep.git'
ref = 'c50b93a32df1c9d700e3e80996845bc2e13be848'
archive_path = f'/tmp/cachito-archives/release-engineering/retrodep/{ref}.tar.gz'


@mock.patch('cachito.workers.tasks.celery.app')
@mock.patch('os.makedirs')
def test_archive_path(mock_makedirs, mock_celery_app):
    path = '/tmp/cachito-archives'
    mock_celery_app.conf.cachito_sources_dir = path
    git_obj = scm.Git(url, ref)
    assert git_obj.archives_dir == path
    assert git_obj.archive_path == archive_path
    mock_makedirs.assert_called_once_with(
        '/tmp/cachito-archives/release-engineering/retrodep', exist_ok=True
    )


def test_repo_name():
    git_obj = scm.Git(url, ref)
    assert git_obj.repo_name == 'release-engineering/retrodep'


@mock.patch('tarfile.open')
@mock.patch('tempfile.TemporaryDirectory')
@mock.patch('git.repo.Repo.clone_from')
@mock.patch('cachito.workers.scm.Git.archive_path', new_callable=mock.PropertyMock)
def test_clone_and_archive(mock_archive_path, mock_clone, mock_temp_dir, mock_tarfile_open):
    # Mock the archive being created
    mock_tarfile = mock.Mock()
    mock_tarfile_open.return_value.__enter__.return_value = mock_tarfile
    # Mock the commit being returned from repo.commit(self.ref)
    mock_commit = mock.Mock()
    mock_clone.return_value.commit.return_value = mock_commit
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = '/tmp/cachito-temp'
    # Mock the Git.archives_dir property
    mock_archive_path.return_value = archive_path

    git_obj = scm.Git(url, ref)
    git_obj.clone_and_archive()

    # Verify the tempfile.TemporaryDirectory context manager was used
    mock_temp_dir.return_value.__enter__.assert_called_once()
    # Verify the repo was cloned and checked out properly
    mock_clone.assert_called_once_with(url, '/tmp/cachito-temp/repo', no_checkout=True)
    assert mock_clone.return_value.head.reference == mock_commit
    mock_clone.return_value.head.reset.assert_called_once_with(index=True, working_tree=True)
    # Verfiy the archive was created
    mock_tarfile.add.assert_called_once_with('/tmp/cachito-temp/repo', 'app')


@mock.patch('tempfile.TemporaryDirectory')
@mock.patch('git.repo.Repo.clone_from')
def test_clone_and_archive_clone_failed(mock_git_clone, mock_temp_dir):
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = '/tmp/cachito-temp'
    # Mock the git clone call
    mock_git_clone.side_effect = git.GitCommandError('some error', 1)

    git_obj = scm.Git(url, ref)
    with pytest.raises(CachitoError, match='Cloning the Git repository failed'):
        git_obj.clone_and_archive()


@mock.patch('tempfile.TemporaryDirectory')
@mock.patch('git.repo.Repo.clone_from')
@mock.patch('cachito.workers.scm.Git.archive_path', new_callable=mock.PropertyMock)
def test_clone_and_archive_checkout_failed(mock_archive_path, mock_git_clone, mock_temp_dir):
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = '/tmp/cachito-temp'
    # Mock the git calls
    mock_git_clone.return_value.commit.side_effect = git.GitCommandError('commit is invalid', 1)
    # Mock the Git.archives_dir property
    mock_archive_path.return_value = archive_path

    git_obj = scm.Git(url, ref)
    expected = (
        'Checking out the Git repository failed. Please verify the supplied reference of '
        f'"{ref}" is valid.'
    )
    with pytest.raises(CachitoError, match=expected):
        git_obj.clone_and_archive()
