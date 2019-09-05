# SPDX-License-Identifier: GPL-3.0-or-later
import io
import os
import pathlib
import tarfile
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
        'http://cachito.domain.local/api/v1/requests/1', json=expected_payload, timeout=60)


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


@pytest.mark.parametrize('deps_present', (True, False))
@mock.patch('cachito.workers.tasks.general.set_request_state')
@mock.patch('cachito.workers.tasks.general.get_worker_config')
def test_create_bundle_archive(mock_get_worker_config, mock_set_request, deps_present, tmpdir):
    # Make the bundles and sources dir configs point to under the pytest managed temp dir
    bundles_dir = tmpdir.mkdir('bundles')
    sources_dir = tmpdir.mkdir('sources')
    mock_get_worker_config.return_value = mock.Mock(
        cachito_bundles_dir=str(bundles_dir),
        cachito_sources_dir=str(sources_dir),
    )

    # Create the mocked application source archive (app.tar.gz)
    app_archive_path = (
        sources_dir.mkdir('release-engineering').mkdir('some_app').join('app.tar.gz')
    )
    app_archive_contents = {
        'app/pizza.go': b'Cheese Pizza',
        'app/all_systems.go': b'All Systems Go',
    }

    with tarfile.open(app_archive_path, mode='w:gz') as app_archive:
        for name, data in app_archive_contents.items():
            fileobj = io.BytesIO(data)
            tarinfo = tarfile.TarInfo(name)
            tarinfo.size = len(fileobj.getvalue())
            app_archive.addfile(tarinfo, fileobj=fileobj)

    # Create the dependencies cache from the call to add_deps_to_bundle call from resolve_gomod_deps
    deps_archive_contents = {
        'deps/gomod/pkg/mod/cache/download/server.com/dep1/@v/dep1.zip': b'dep1 archive',
        'deps/gomod/pkg/mod/cache/download/server.com/dep2/@v/dep2.zip': b'dep2 archive',
    }

    request_id = 3
    if deps_present:
        temp_bundle_path = bundles_dir.mkdir('temp').mkdir(str(request_id))
        for name, data in deps_archive_contents.items():
            path = temp_bundle_path.join(name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, 'wb').write(data)

    # Test the bundle is created when create_bundle_archive is called
    tasks.create_bundle_archive(app_archive_path, request_id)
    bundle_archive_path = str(bundles_dir.join(f'{request_id}.tar.gz'))
    assert os.path.exists(bundle_archive_path)

    # Verify the contents of the assembled bundle archive
    with tarfile.open(bundle_archive_path, mode='r:*') as bundle_archive:
        bundle_contents = set([
            path for path in bundle_archive.getnames()
            if pathlib.Path(path).suffix in ('.go', '.zip')
        ])

        # Always make sure there is a deps directory. This will be empty if no deps were present.
        assert 'deps' in bundle_archive.getnames()

    expected = set(app_archive_contents.keys())
    if deps_present:
        expected |= set(deps_archive_contents.keys())

    assert bundle_contents == expected

    mock_set_request.assert_called_once_with(
        request_id, 'in_progress', 'Assembling the bundle archive')
