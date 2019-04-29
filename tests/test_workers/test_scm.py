# SPDX-License-Identifier: GPL-3.0-or-later
from unittest import mock

import pytest

from cachito.workers import scm
from cachito.errors import CachitoError

url = 'https://github.com/release-engineering/retrodep.git'
ref = 'c50b93a32df1c9d700e3e80996845bc2e13be848'
archive_path = f'/tmp/cachito-archives/release-engineering/retrodep/{ref}.tar.gz'


@mock.patch('cachito.workers.tasks.app')
@mock.patch('os.makedirs')
def test_archive_path(mock_makedirs, mock_celery_app):
    path = '/tmp/cachito-archives'
    mock_celery_app.conf.cachito_archives_dir = path
    git = scm.Git(url, ref)
    assert git.archives_dir == path
    assert git.archive_path == archive_path
    mock_makedirs.assert_called_once_with(
        '/tmp/cachito-archives/release-engineering/retrodep', exist_ok=True
    )


def test_repo_name():
    git = scm.Git(url, ref)
    assert git.repo_name == 'release-engineering/retrodep'


@mock.patch('tempfile.TemporaryDirectory')
@mock.patch('requests.get')
@mock.patch('cachito.workers.scm.Git.archive_path', new_callable=mock.PropertyMock)
@mock.patch('shutil.copyfileobj')
@mock.patch('tarfile.open')
def test_download_source_archive(
    mock_tarfile_open, mock_copyfileobj, mock_archive_path, mock_requests, mock_temp_dir
):
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = '/tmp/cachito-temp'
    # Mock the requests.get call of downloading the archive from GitHub
    mock_requests.return_value.__enter__.return_value.ok = True
    # Mock the Git.archive_path property
    mock_archive_path.return_value = archive_path
    # Mock the opening of the initial tar file that was downloaded from GitHub
    mock_initial_tarfile = mock.Mock()
    mock_initial_tarfile.firstmember.name = 'retrodep'
    # Mock the opening of the tar file that will contain the content to put in long-term storage
    mock_final_tarfile = mock.Mock()
    mock_tarfile_open.return_value.__enter__.side_effect = [
        mock_initial_tarfile,
        mock_final_tarfile,
    ]

    git = scm.Git(url, ref)
    download_url = f'{url[: -len(".git")]}/archive/{ref}.tar.gz'
    # Mock the opening of the initial tar file to write the content downloaded from GitHub
    with mock.patch('builtins.open', mock.mock_open()) as mock_file:
        git.download_source_archive(download_url)
        # Verify the tarfile was written to in the temporary directory
        mock_file.assert_called_once_with(f'/tmp/cachito-temp/{ref}.tar.gz', 'wb')

    # Verify the tempfile.TemporaryDirectory context manager was used
    mock_temp_dir.return_value.__enter__.assert_called_once()
    # Verify the archive was downloaded
    mock_requests.assert_called_once_with(download_url, stream=True, timeout=120)
    # Verify the archive was written to disk
    mock_copyfileobj.assert_called_once()
    # Verify that the intial archive that was downloaded was extracted to the temporary directory
    mock_initial_tarfile.extractall.assert_called_once_with('/tmp/cachito-temp')
    # Verify that the final archive was created
    mock_final_tarfile.add.assert_called_once_with('/tmp/cachito-temp/retrodep', 'app')


@mock.patch('tempfile.TemporaryDirectory')
@mock.patch('requests.get')
def test_download_source_download_failed(mock_requests, mock_temp_dir):
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = '/tmp/cachito-temp'
    # Mock the requests.get call of downloading the archive from GitHub so that it fails
    mock_requests.return_value.__enter__.return_value.ok = False

    download_url = f'{url[: -len(".git")]}/archive/{ref}.tar.gz'
    git = scm.Git(url, ref)
    expected_error = 'An unexpected error was encountered when downloading the source'
    with pytest.raises(CachitoError, match=expected_error):
        git.download_source_archive(download_url)


@mock.patch('tempfile.TemporaryDirectory')
@mock.patch('subprocess.run')
@mock.patch('cachito.workers.scm.Git.archive_path', new_callable=mock.PropertyMock)
def test_clone_and_archive(mock_archive_path, mock_run, mock_temp_dir):
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = '/tmp/cachito-temp'
    # Mock the git calls
    mock_run.return_value.returncode = 0
    # Mock the Git.archives_dir property
    mock_archive_path.return_value = archive_path

    git = scm.Git(url, ref)
    git.clone_and_archive()

    # Verify the tempfile.TemporaryDirectory context manager was used
    mock_temp_dir.return_value.__enter__.assert_called_once()
    # Verify the git calls were correct
    mock_run.assert_has_calls(
        [
            mock.call(
                [
                    'git',
                    'clone',
                    '-q',
                    '--no-checkout',
                    'https://github.com/release-engineering/retrodep.git',
                    '/tmp/cachito-temp/repo',
                ],
                capture_output=True,
                universal_newlines=True,
                encoding='utf-8'
            ),
            mock.call(
                [
                    'git',
                    '-C',
                    '/tmp/cachito-temp/repo',
                    'archive',
                    '-o',
                    f'/tmp/cachito-archives/release-engineering/retrodep/{ref}.tar.gz',
                    '--prefix=app/',
                    'c50b93a32df1c9d700e3e80996845bc2e13be848',
                ],
                capture_output=True,
                universal_newlines=True,
                encoding='utf-8'
            ),
        ]
    )


@mock.patch('tempfile.TemporaryDirectory')
@mock.patch('subprocess.run')
def test_clone_and_archive_clone_failed(mock_run, mock_temp_dir):
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = '/tmp/cachito-temp'
    # Mock the git clone call
    mock_run.return_value.returncode = 1
    mock_run.return_value.stderr = 'failure'

    git = scm.Git(url, ref)
    with pytest.raises(CachitoError, match='Cloning the git repository failed'):
        git.clone_and_archive()


@pytest.mark.parametrize(
    'archive_error, expected_error',
    (
        ('some error', 'An unexpected error was encountered when downloading the source'),
        ('Not a valid object name', 'An invalid reference was provided'),
    ),
)
@mock.patch('tempfile.TemporaryDirectory')
@mock.patch('subprocess.run')
@mock.patch('cachito.workers.scm.Git.archive_path', new_callable=mock.PropertyMock)
def test_clone_and_archive_git_archive_failed(
    mock_archive_path, mock_run, mock_temp_dir, archive_error, expected_error
):
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = '/tmp/cachito-temp'
    # Mock the git calls
    mock_clone = mock.Mock()
    mock_clone.returncode = 0
    mock_archive = mock.Mock()
    mock_archive.returncode = 1
    mock_archive.stderr = archive_error
    mock_run.side_effect = [mock_clone, mock_archive]
    # Mock the Git.archives_dir property
    mock_archive_path.return_value = archive_path

    git = scm.Git(url, ref)
    with pytest.raises(CachitoError, match=expected_error):
        git.clone_and_archive()
