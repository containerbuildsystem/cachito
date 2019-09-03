# SPDX-License-Identifier: GPL-3.0-or-later
import io
import os
import tarfile
from textwrap import dedent
from unittest import mock

import pytest

from cachito.workers.pkg_manager import (
    resolve_gomod_deps, update_request_with_deps, add_deps_to_bundle_archive,
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
    github.com/pkg/errors v0.8.1
    golang.org/x/crypto v0.0.0-20190308221718-c2843e01d9a2
    golang.org/x/net v0.0.0-20190311183353-d8887717615a
    golang.org/x/sys v0.0.0-20190215142949-d0b11bdaac8a
    golang.org/x/text v0.3.0
    golang.org/x/tools v0.0.0-20190325161752-5a8dccf5b48a
    gopkg.in/check.v1 v1.0.0-20180628173108-788fd7840127
    gopkg.in/yaml.v2 v2.2.2
    """)


@pytest.mark.parametrize('request_id', (None, 3))
@mock.patch('cachito.workers.pkg_manager.add_deps_to_bundle_archive')
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
        assert mock_add_deps.call_args[0][0] == 3
        assert mock_add_deps.call_args[0][1] == '/this/is/path/to/archive.tar.gz'
        assert mock_add_deps.call_args[0][2].endswith('pkg/mod/cache/download')
        assert mock_add_deps.call_args[0][3] == 'gomod/pkg/mod/cache/download'
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


@mock.patch('cachito.workers.requests.requests_auth_session')
def test_update_request_with_deps(mock_requests, sample_deps):
    mock_requests.patch.return_value.ok = True
    update_request_with_deps(1, sample_deps)
    url = 'http://cachito.domain.local/api/v1/requests/1'
    expected_payload = {'dependencies': sample_deps}
    mock_requests.patch.assert_called_once_with(url, json=expected_payload, timeout=30)


@mock.patch('tempfile.TemporaryDirectory')
@mock.patch('cachito.workers.pkg_manager.get_worker_config')
def test_add_deps_to_bundle_archive(mock_get_worker_config, mock_temp_dir, tmpdir):
    # Create a temporary directory for add_deps_to_bundle_archive
    relative_add_deps_tmpdir = 'add_deps_temp'
    tmpdir.mkdir(relative_add_deps_tmpdir)
    mock_temp_dir.return_value.__enter__.return_value = tmpdir.join(relative_add_deps_tmpdir)
    # Make the bundles and sources dir configs point to under the pytest managed temp dir
    bundles_dir = tmpdir.join('bundles')
    sources_dir = tmpdir.join('sources')
    mock_get_worker_config.return_value = mock.Mock(
        cachito_bundles_dir=str(bundles_dir),
        cachito_sources_dir=str(sources_dir),
    )
    # Create a temporary directory to store the application source and deps. Normally the
    # application source would be in some nested folders, but for the test, it doesn't matter.
    relative_tmpdir = 'temp'
    tmpdir.mkdir(relative_tmpdir)
    app_archive_path = tmpdir.join(relative_tmpdir, 'app.tar.gz')
    deps_path = tmpdir.join(relative_tmpdir, 'deps')

    # Create the mocked application source archive (app.tar.gz)
    app_archive_contents = {
        'app/spam.go': b'Spam mapS',
        'app/ham.go': b'Ham maH',
    }

    with tarfile.open(app_archive_path, mode='w:gz') as app_archive:
        for name, data in app_archive_contents.items():
            fileobj = io.BytesIO(data)
            tarinfo = tarfile.TarInfo(name)
            tarinfo.size = len(fileobj.getvalue())
            app_archive.addfile(tarinfo, fileobj=fileobj)

    # Create the dependencies cache that mocks the output of `go mod download`
    deps_archive_contents = {
        'pkg/mod/cache/download/server.com/dep1/@v/dep1.zip': b'dep1 archive',
        'pkg/mod/cache/download/server.com/dep2/@v/dep2.zip': b'dep2 archive',
    }

    for name, data in deps_archive_contents.items():
        path = deps_path.join(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'wb').write(data)

    cache_path = os.path.join('pkg', 'mod', 'cache', 'download')
    # The path to the part of the gomod cache that should be added to the bundle
    deps_path_input = os.path.join(deps_path, cache_path)
    # The path to where the cache should end up in the bundle archive
    dest_cache_path = os.path.join('gomod', cache_path)
    request_id = 3
    add_deps_to_bundle_archive(request_id, app_archive_path, deps_path_input, dest_cache_path)

    # Verify the bundle was created
    bundle_archive_path = str(bundles_dir.join(f'{request_id}.tar.gz'))
    assert os.path.exists(bundle_archive_path)

    # Verify contents of assembled archive
    with tarfile.open(bundle_archive_path, mode='r:*') as bundle_archive:
        for expected_member in list(app_archive_contents.keys()):
            bundle_archive.getmember(expected_member)
        for expected_member in list(deps_archive_contents.keys()):
            bundle_archive.getmember(os.path.join('deps', 'gomod', expected_member))
