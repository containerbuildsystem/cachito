# SPDX-License-Identifier: GPL-3.0-or-later
import os
from unittest import mock

from cachito.workers.pkg_managers import add_deps_to_bundle, update_request_with_deps


@mock.patch('cachito.workers.config.Config.cachito_deps_patch_batch_size', 5)
@mock.patch('cachito.workers.requests.requests_auth_session')
def test_update_request_with_deps(mock_requests, sample_deps_replace):
    mock_requests.patch.return_value.ok = True
    update_request_with_deps(1, sample_deps_replace)
    url = 'http://cachito.domain.local/api/v1/requests/1'
    calls = [
        mock.call(url, json={'dependencies': sample_deps_replace[:5]}, timeout=60),
        mock.call(url, json={'dependencies': sample_deps_replace[5:10]}, timeout=60),
        mock.call(url, json={'dependencies': sample_deps_replace[10:]}, timeout=60),
    ]
    assert mock_requests.patch.call_count == 3
    mock_requests.patch.assert_has_calls(calls)


@mock.patch('cachito.workers.utils.get_worker_config')
def test_add_deps_to_bundle(mock_get_worker_config, tmpdir):
    # Make the bundles and sources dir configs point to under the pytest managed temp dir
    bundles_dir = tmpdir.mkdir('bundles')
    mock_get_worker_config.return_value = mock.Mock(cachito_bundles_dir=str(bundles_dir))
    # Create a temporary directory to store the application deps
    relative_tmpdir = 'temp'
    tmpdir.mkdir(relative_tmpdir)
    deps_path = tmpdir.join(relative_tmpdir, 'deps')

    # Create the dependencies cache that mocks the output of `go mod download`
    deps_contents = {
        'pkg/mod/cache/download/server.com/dep1/@v/dep1.zip': b'dep1 archive',
        'pkg/mod/cache/download/server.com/dep2/@v/dep2.zip': b'dep2 archive',
    }

    for name, data in deps_contents.items():
        path = deps_path.join(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'wb').write(data)

    cache_path = os.path.join('pkg', 'mod', 'cache', 'download')
    # The path to the part of the gomod cache that should be added to the bundle
    src_deps_path = os.path.join(deps_path, cache_path)
    # The path to where the cache should end up in the bundle archive
    dest_cache_path = os.path.join('gomod', cache_path)
    request_id = 3
    add_deps_to_bundle(src_deps_path, dest_cache_path, request_id)

    # Verify the deps were copied
    for expected in list(deps_contents.keys()):
        expected_path = str(bundles_dir.join('temp', str(request_id), 'deps', 'gomod', expected))
        assert os.path.exists(expected_path) is True
