# SPDX-License-Identifier: GPL-3.0-or-later
import os
import pathlib
import tarfile
from unittest import mock

import pytest
from requests import Timeout

from cachito.errors import CachitoError
from cachito.workers import tasks


@pytest.mark.parametrize('dir_exists', (True, False))
@mock.patch('cachito.workers.tasks.general.os.makedirs')
@mock.patch('cachito.workers.tasks.general.os.path.exists')
@mock.patch('cachito.workers.tasks.general.extract_app_src')
@mock.patch('cachito.workers.tasks.general.set_request_state')
@mock.patch('cachito.workers.tasks.general.Git')
def test_fetch_app_source(
    mock_git, mock_set_request_state, mock_extract_app_src, mock_exists, mock_makedirs, dir_exists,
):
    mock_exists.return_value = dir_exists

    url = 'https://github.com/release-engineering/retrodep.git'
    ref = 'c50b93a32df1c9d700e3e80996845bc2e13be848'
    tasks.fetch_app_source(url, ref, 1)
    mock_git.assert_called_once_with(url, ref)
    mock_git.return_value.fetch_source.assert_called_once_with()
    mock_set_request_state.assert_called_once_with(
        1, 'in_progress', 'Fetching the application source')

    bundle_dir = '/tmp/cachito-archives/bundles/temp/1'
    mock_exists.assert_called_once_with(bundle_dir)
    if dir_exists:
        mock_makedirs.assert_not_called()
    else:
        mock_makedirs.assert_called_once_with(bundle_dir, exist_ok=True)

    mock_extract_app_src.assert_called_once_with(mock_git().archive_path, bundle_dir)


@mock.patch('cachito.workers.tasks.general.set_request_state')
@mock.patch('cachito.workers.tasks.general.Git')
def test_fetch_app_source_request_timed_out(mock_git, mock_set_request_state):
    url = 'https://github.com/release-engineering/retrodep.git'
    ref = 'c50b93a32df1c9d700e3e80996845bc2e13be848'
    mock_git.return_value.fetch_source.side_effect = Timeout('The request timed out')
    with pytest.raises(CachitoError, match='The connection timed out while downloading the source'):
        tasks.fetch_app_source(url, ref, 1)


@pytest.mark.parametrize('auto_detect, contains_go_mod, dep_replacements, expect_state_update', (
    (False, False, None, True),
    (True, True, None, True),
    (True, False, None, False),
    (True, True, False, [{'name': 'github.com/pkg/errors', 'type': 'gomod', 'version': 'v0.8.1'}]),
))
@mock.patch('cachito.workers.tasks.golang.os.path.exists')
@mock.patch('cachito.workers.tasks.golang.update_request_with_deps')
@mock.patch('cachito.workers.tasks.golang.set_request_state')
@mock.patch('cachito.workers.tasks.golang.resolve_gomod')
def test_fetch_gomod_source(
    mock_resolve_gomod, mock_set_request_state, mock_update_request_with_deps,
    mock_path_exists, auto_detect, contains_go_mod, dep_replacements, expect_state_update,
    sample_deps_replace, sample_package, sample_env_vars,
):
    mock_request = mock.Mock()
    mock_set_request_state.return_value = mock_request
    mock_path_exists.return_value = contains_go_mod
    mock_resolve_gomod.return_value = sample_package, sample_deps_replace
    tasks.fetch_gomod_source(1, auto_detect, dep_replacements)
    if expect_state_update:
        mock_set_request_state.assert_called_once_with(
            1, 'in_progress', 'Fetching the golang dependencies')
        mock_update_request_with_deps.assert_called_once_with(
            1, sample_deps_replace, sample_env_vars, 'gomod', [sample_package])

    if auto_detect:
        mock_path_exists.assert_called_once_with('/tmp/cachito-archives/bundles/temp/1/app/go.mod')
        if contains_go_mod:
            mock_resolve_gomod.assert_called_once_with(
                '/tmp/cachito-archives/bundles/temp/1/app', mock_request, dep_replacements,
            )
        else:
            mock_resolve_gomod.assert_not_called()
    else:
        mock_resolve_gomod.assert_called_once_with(
            '/tmp/cachito-archives/bundles/temp/1/app', mock_request, dep_replacements,
        )
        mock_path_exists.assert_not_called()


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
@mock.patch('cachito.workers.utils.get_worker_config')
def test_create_bundle_archive(mock_gwc, mock_set_request, deps_present, tmpdir):
    # Make the bundles and sources dir configs point to under the pytest managed temp dir
    bundles_dir = tmpdir.mkdir('bundles')
    mock_gwc.return_value.cachito_bundles_dir = str(bundles_dir)
    request_id = 3
    request_bundle_dir = bundles_dir.mkdir('temp').mkdir(str(request_id))

    # Create the extracted application source
    app_archive_contents = {
        'app/.git': b'some content',
        'app/pizza.go': b'Cheese Pizza',
        'app/all_systems.go': b'All Systems Go',
    }

    request_bundle_dir.mkdir('app')
    for name, data in app_archive_contents.items():
        file_path = os.path.join(str(request_bundle_dir), name)
        with open(file_path, 'wb') as f:
            f.write(data)

    # Create the dependencies cache from the call to add_deps_to_bundle call from resolve_gomod
    deps_archive_contents = {
        'deps/gomod/pkg/mod/cache/download/server.com/dep1/@v/dep1.zip': b'dep1 archive',
        'deps/gomod/pkg/mod/cache/download/server.com/dep2/@v/dep2.zip': b'dep2 archive',
    }

    if deps_present:
        for name, data in deps_archive_contents.items():
            path = request_bundle_dir.join(name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, 'wb').write(data)

    # Test the bundle is created when create_bundle_archive is called
    tasks.create_bundle_archive(request_id)
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
    # The .git folder must be excluded
    expected.remove('app/.git')
    if deps_present:
        expected |= set(deps_archive_contents.keys())

    assert bundle_contents == expected

    mock_set_request.assert_called_once_with(
        request_id, 'in_progress', 'Assembling the bundle archive')
