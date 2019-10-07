# SPDX-License-Identifier: GPL-3.0-or-later
import os
from textwrap import dedent
from unittest import mock

import pytest

from cachito.workers.pkg_manager import (
    add_deps_to_bundle, archive_contains_path, resolve_gomod_deps, update_request_with_deps,
)
from cachito.errors import CachitoError


url = 'https://github.com/release-engineering/retrodep.git'
ref = 'c50b93a32df1c9d700e3e80996845bc2e13be848'
archive_path = f'/tmp/cachito-archives/release-engineering/retrodep/{ref}.tar.gz'

mock_cmd_output = dedent("""\
    github.com/release-engineering/retrodep/v2
    github.com/Masterminds/semver v1.4.2
    github.com/kr/pretty v0.1.0
    github.com/kr/pty v1.1.1
    github.com/kr/text v0.1.0
    github.com/op/go-logging v0.0.0-20160315200505-970db520ece7
    github.com/pkg/errors v0.8.0 github.com/pkg/new_errors v1.0.0
    golang.org/x/crypto v0.0.0-20190308221718-c2843e01d9a2
    golang.org/x/net v0.0.0-20190311183353-d8887717615a
    golang.org/x/sys v0.0.0-20190215142949-d0b11bdaac8a
    golang.org/x/text v0.3.0
    golang.org/x/tools v0.0.0-20190325161752-5a8dccf5b48a
    gopkg.in/check.v1 v1.0.0-20180628173108-788fd7840127
    gopkg.in/yaml.v2 v2.2.2
    """)


@pytest.mark.parametrize('request_id', (None, 3))
@mock.patch('cachito.workers.pkg_manager.add_deps_to_bundle')
@mock.patch('cachito.workers.pkg_manager.GoCacheTemporaryDirectory')
@mock.patch('subprocess.run')
@mock.patch('tarfile.open')
def test_resolve_gomod_deps(
    mock_tarfile_open, mock_run, mock_temp_dir, mock_add_deps, request_id, tmpdir, sample_deps,
):
    archive_path = '/this/is/path/to/archive.tar.gz'
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmpdir)

    # Mock the "subprocess.run" calls
    mock_run.side_effect = [
        mock.Mock(returncode=0, stdout=None),   # go mod download
        mock.Mock(returncode=0, stdout=mock_cmd_output)  # go list -m all
    ]

    # Mock the opening of the tar file containing application source code
    mock_final_tarfile = mock.Mock()
    mock_final_tarfile.extractall.return_value = None
    mock_tarfile_open.return_value.__enter__.side_effect = [
        mock_final_tarfile,
    ]

    resolved_deps = resolve_gomod_deps(archive_path, request_id)

    assert resolved_deps == sample_deps
    if request_id:
        mock_add_deps.assert_called_once()
        assert mock_add_deps.call_args[0][0].endswith('pkg/mod/cache/download')
        assert mock_add_deps.call_args[0][1] == 'gomod/pkg/mod/cache/download'
        assert mock_add_deps.call_args[0][2] == 3
    else:
        mock_add_deps.assert_not_called()


@pytest.mark.parametrize(('go_mod_rc', 'go_list_rc'), ((0, 1), (1, 0)))
@mock.patch('cachito.workers.pkg_manager.GoCacheTemporaryDirectory')
@mock.patch('subprocess.run')
@mock.patch('tarfile.open')
def test_go_list_cmd_failure(
    mock_tarfile_open, mock_run, mock_temp_dir, tmpdir, go_mod_rc, go_list_rc
):
    archive_path = '/this/is/path/to/archive.tar.gz'
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmpdir)

    # Mock the "subprocess.run" calls
    mock_run.side_effect = [
        mock.Mock(returncode=go_mod_rc, stdout=None),   # go mod download
        mock.Mock(returncode=go_list_rc, stdout=mock_cmd_output)  # go list -m all
    ]

    # Mock the opening of the tar file containing application source code
    mock_final_tarfile = mock.Mock()
    mock_final_tarfile.extractall.return_value = None
    mock_tarfile_open.return_value.__enter__.side_effect = [
        mock_final_tarfile,
    ]

    with pytest.raises(CachitoError) as exc_info:
        resolve_gomod_deps(archive_path)
    assert str(exc_info.value) == 'Processing gomod dependencies failed'


@mock.patch('cachito.workers.config.Config.cachito_deps_patch_batch_size', 5)
@mock.patch('cachito.workers.requests.requests_auth_session')
def test_update_request_with_deps(mock_requests, sample_deps):
    mock_requests.patch.return_value.ok = True
    update_request_with_deps(1, sample_deps)
    url = 'http://cachito.domain.local/api/v1/requests/1'
    calls = [
        mock.call(url, json={'dependencies': sample_deps[:5]}, timeout=60),
        mock.call(url, json={'dependencies': sample_deps[5:10]}, timeout=60),
        mock.call(url, json={'dependencies': sample_deps[10:]}, timeout=60),
    ]
    assert mock_requests.patch.call_count == 3
    mock_requests.patch.assert_has_calls(calls)


@mock.patch('cachito.workers.pkg_manager.get_worker_config')
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


@pytest.mark.parametrize('names, expected', (
    (('app/go.mod', 'app/something'), True),
    (('app/pizza', 'app/something'), False),
))
@mock.patch('tarfile.open')
def test_archive_contains_path(mock_tarfile_open, names, expected):
    # Mock the opening of the tar file containing application source code
    mock_tarfile = mock.Mock()
    mock_tarfile.getnames.return_value = names
    mock_tarfile_open.return_value.__enter__.return_value = mock_tarfile

    archive_path = '/some/path/file.tar.gz'
    assert archive_contains_path(archive_path, 'app/go.mod') is expected
