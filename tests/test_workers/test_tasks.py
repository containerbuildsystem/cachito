# SPDX-License-Identifier: GPL-3.0-or-later
import io
import os
import tarfile
from unittest import mock

import pytest
from requests import Timeout
from cachito.errors import CachitoError

from cachito.workers import tasks


@pytest.mark.parametrize('request_id_to_update', (None, 1))
@mock.patch('cachito.workers.tasks.set_request_state')
@mock.patch('cachito.workers.tasks.Git')
def test_fetch_app_source(mock_git, mock_set_request_state, request_id_to_update):
    url = 'https://github.com/release-engineering/retrodep.git'
    ref = 'c50b93a32df1c9d700e3e80996845bc2e13be848'
    tasks.fetch_app_source(url, ref, request_id_to_update=request_id_to_update)
    mock_git.assert_called_once_with(url, ref)
    mock_git.return_value.fetch_source.assert_called_once_with()
    if request_id_to_update:
        mock_set_request_state.assert_called_once_with(
            1, 'in_progress', 'Fetching the application source')
    else:
        mock_set_request_state.assert_not_called()


@mock.patch('cachito.workers.tasks.Git')
def test_fetch_app_source_request_timed_out(mock_git):
    url = 'https://github.com/release-engineering/retrodep.git'
    ref = 'c50b93a32df1c9d700e3e80996845bc2e13be848'
    mock_git.return_value.fetch_source.side_effect = Timeout('The request timed out')
    with pytest.raises(CachitoError, match='The connection timed out while downloading the source'):
        tasks.fetch_app_source(url, ref)


@pytest.mark.parametrize('request_id_to_update', (None, 1))
@mock.patch('cachito.workers.tasks.set_request_state')
@mock.patch('cachito.workers.tasks.resolve_gomod_deps')
def test_fetch_gomod_source(mock_resolve_gomod_deps, mock_set_request_state, request_id_to_update):
    app_archive_path = 'path/to/archive.tar.gz'
    tasks.fetch_gomod_source(app_archive_path, request_id_to_update=request_id_to_update)
    if request_id_to_update:
        mock_set_request_state.assert_called_once_with(
            1, 'in_progress', 'Fetching the golang dependencies')
    else:
        mock_set_request_state.assert_not_called()


@mock.patch('cachito.workers.tasks.get_worker_config')
def test_assemble_archive_bundle(mock_get_worker_config, tmpdir):
    mocked_config = mock.Mock(cachito_shared_dir=str(tmpdir))
    mock_get_worker_config.return_value = mocked_config
    relative_tmpdir = 'temp'
    tmpdir.mkdir(relative_tmpdir)
    relative_deps_path = os.path.join(relative_tmpdir, 'deps')
    relative_bundle_archive_path = os.path.join(relative_tmpdir, 'bundle.tar.gz')

    app_archive_path = tmpdir.join(relative_tmpdir, 'app.tar.gz')
    absolute_deps_path = tmpdir.join(relative_deps_path)
    absolute_bundle_archive_path = tmpdir.join(relative_bundle_archive_path)

    app_archive_contents = {
        'app/spam.go': b'Spam mapS',
        'app/ham.go': b'Ham maH',
    }

    deps_archive_contents = {
        'gomod/pkg/mod/cache/download/server.com/dep1/@v/dep1.zip': b'dep1 archive',
        'gomod/pkg/mod/cache/download/server.com/dep2/@v/dep2.zip': b'dep2 archive',
    }

    # Create mocked application source archive
    with tarfile.open(app_archive_path, mode='w:gz') as app_archive:
        for name, data in app_archive_contents.items():
            fileobj = io.BytesIO(data)
            tarinfo = tarfile.TarInfo(name)
            tarinfo.size = len(fileobj.getvalue())
            app_archive.addfile(tarinfo, fileobj=fileobj)

    # Create mocked dependencies cache
    for name, data in deps_archive_contents.items():
        path = absolute_deps_path.join(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'wb').write(data)

    tasks.assemble_source_code_archive(
        app_archive_path, relative_deps_path, relative_bundle_archive_path)

    # Verify contents of assembled archive
    with tarfile.open(absolute_bundle_archive_path, mode='r:*') as bundle_archive:
        for expected_member in list(app_archive_contents.keys()):
            bundle_archive.getmember(expected_member)
        for expected_member in list(deps_archive_contents.keys()):
            bundle_archive.getmember(os.path.join('deps', expected_member))


@mock.patch('cachito.workers.tasks.requests')
def test_set_request_state(mock_requests):
    mock_requests.patch.return_value.ok = True
    tasks.set_request_state(1, 'complete', 'Completed successfully')
    expected_payload = {'state': 'complete', 'state_reason': 'Completed successfully'}
    mock_requests.patch.assert_called_once_with(
        'http://cachito.domain.local/api/v1/requests/1', json=expected_payload, timeout=30)


@mock.patch('cachito.workers.tasks.requests.patch')
def test_set_request_state_connection_failed(mock_requests_patch):
    mock_requests_patch.side_effect = Timeout('The request timed out')
    expected = 'The connection failed when setting the state to "complete" on request 1'
    with pytest.raises(CachitoError, match=expected):
        tasks.set_request_state(1, 'complete', 'Completed successfully')


@mock.patch('cachito.workers.tasks.requests')
def test_set_request_state_bad_status_code(mock_requests):
    mock_requests.patch.return_value.ok = False
    expected = 'Setting the state to "complete" on request 1 failed'
    with pytest.raises(CachitoError, match=expected):
        tasks.set_request_state(1, 'complete', 'Completed successfully')


@mock.patch('cachito.workers.tasks.set_request_state')
def test_failed_request_callback(mock_set_request_state):
    exc = CachitoError('some error')
    tasks.failed_request_callback(None, exc, None, 1)
    mock_set_request_state.assert_called_once_with(1, 'failed', 'some error')


@mock.patch('cachito.workers.tasks.set_request_state')
def test_failed_request_callback_not_cachitoerror(mock_set_request_state):
    exc = ValueError('some error')
    tasks.failed_request_callback(None, exc, None, 1)
    mock_set_request_state.assert_called_once_with(1, 'failed', 'An unknown error occurred')
