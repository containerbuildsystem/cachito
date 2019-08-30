# SPDX-License-Identifier: GPL-3.0-or-later
from unittest import mock

import pytest
from requests import Timeout
from cachito.errors import CachitoError

from cachito.workers import tasks


@pytest.mark.parametrize('request_id_to_update', (None, 1))
@mock.patch('cachito.workers.tasks.general.set_request_state')
@mock.patch('cachito.workers.tasks.general.Git')
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


@mock.patch('cachito.workers.tasks.general.Git')
def test_fetch_app_source_request_timed_out(mock_git):
    url = 'https://github.com/release-engineering/retrodep.git'
    ref = 'c50b93a32df1c9d700e3e80996845bc2e13be848'
    mock_git.return_value.fetch_source.side_effect = Timeout('The request timed out')
    with pytest.raises(CachitoError, match='The connection timed out while downloading the source'):
        tasks.fetch_app_source(url, ref)


@pytest.mark.parametrize('request_id_to_update', (None, 1))
@mock.patch('cachito.workers.tasks.golang.update_request_with_deps')
@mock.patch('cachito.workers.tasks.golang.set_request_state')
@mock.patch('cachito.workers.tasks.golang.resolve_gomod_deps')
def test_fetch_gomod_source(
    mock_resolve_gomod_deps, mock_set_request_state, mock_update_request_with_deps,
    request_id_to_update, sample_deps,
):
    app_archive_path = 'path/to/archive.tar.gz'
    mock_resolve_gomod_deps.return_value = sample_deps
    tasks.fetch_gomod_source(app_archive_path, request_id_to_update=request_id_to_update)
    if request_id_to_update:
        mock_set_request_state.assert_called_once_with(
            1, 'in_progress', 'Fetching the golang dependencies')
        mock_update_request_with_deps.assert_called_once_with(1, sample_deps)
    else:
        mock_set_request_state.assert_not_called()


@mock.patch('cachito.workers.requests.requests_auth_session')
def test_set_request_state(mock_requests):
    mock_requests.patch.return_value.ok = True
    tasks.set_request_state(1, 'complete', 'Completed successfully')
    expected_payload = {'state': 'complete', 'state_reason': 'Completed successfully'}
    mock_requests.patch.assert_called_once_with(
        'http://cachito.domain.local/api/v1/requests/1', json=expected_payload, timeout=30)


@mock.patch('cachito.workers.requests.requests_auth_session.patch')
def test_set_request_state_connection_failed(mock_requests_patch):
    mock_requests_patch.side_effect = Timeout('The request timed out')
    expected = 'The connection failed when setting the state to "complete" on request 1'
    with pytest.raises(CachitoError, match=expected):
        tasks.set_request_state(1, 'complete', 'Completed successfully')


@mock.patch('cachito.workers.requests.requests_auth_session')
def test_set_request_state_bad_status_code(mock_requests):
    mock_requests.patch.return_value.ok = False
    expected = 'Setting the state to "complete" on request 1 failed'
    with pytest.raises(CachitoError, match=expected):
        tasks.set_request_state(1, 'complete', 'Completed successfully')


@mock.patch('cachito.workers.tasks.general.set_request_state')
def test_failed_request_callback(mock_set_request_state):
    exc = CachitoError('some error')
    tasks.failed_request_callback(None, exc, None, 1)
    mock_set_request_state.assert_called_once_with(1, 'failed', 'some error')


@mock.patch('cachito.workers.tasks.general.set_request_state')
def test_failed_request_callback_not_cachitoerror(mock_set_request_state):
    exc = ValueError('some error')
    tasks.failed_request_callback(None, exc, None, 1)
    mock_set_request_state.assert_called_once_with(1, 'failed', 'An unknown error occurred')
