# SPDX-License-Identifier: GPL-3.0-or-later
import os
from textwrap import dedent
from unittest import mock

import pytest

from cachito.workers.pkg_manager import (
    add_deps_to_bundle, resolve_gomod_deps, update_request_with_deps,
)
from cachito.errors import CachitoError


url = 'https://github.com/release-engineering/retrodep.git'
ref = 'c50b93a32df1c9d700e3e80996845bc2e13be848'
archive_path = f'/tmp/cachito-archives/release-engineering/retrodep/{ref}.tar.gz'


def _generate_mock_cmd_output(error_pkg='github.com/pkg/errors v1.0.0'):
    return dedent(f"""\
        github.com/release-engineering/retrodep/v2
        github.com/Masterminds/semver v1.4.2
        github.com/kr/pretty v0.1.0
        github.com/kr/pty v1.1.1
        github.com/kr/text v0.1.0
        github.com/op/go-logging v0.0.0-20160315200505-970db520ece7
        {error_pkg}
        golang.org/x/crypto v0.0.0-20190308221718-c2843e01d9a2
        golang.org/x/net v0.0.0-20190311183353-d8887717615a
        golang.org/x/sys v0.0.0-20190215142949-d0b11bdaac8a
        golang.org/x/text v0.3.0
        golang.org/x/tools v0.0.0-20190325161752-5a8dccf5b48a
        gopkg.in/check.v1 v1.0.0-20180628173108-788fd7840127
        gopkg.in/yaml.v2 v2.2.2
        k8s.io/metrics v0.0.0 ./staging/src/k8s.io/metrics
    """)


@pytest.mark.parametrize('dep_replacement, go_list_error_pkg, expected_replace', (
    (None, 'github.com/pkg/errors v1.0.0', None),
    (
        {'name': 'github.com/pkg/errors', 'type': 'gomod', 'version': 'v1.0.0'},
        'github.com/pkg/errors v0.9.0 github.com/pkg/errors v1.0.0',
        'github.com/pkg/errors=github.com/pkg/errors@v1.0.0',
    ),
    (
        {
            'name': 'github.com/pkg/errors',
            'new_name': 'github.com/pkg/new_errors',
            'type': 'gomod',
            'version': 'v1.0.0',
        },
        'github.com/pkg/errors v0.9.0 github.com/pkg/new_errors v1.0.0',
        'github.com/pkg/errors=github.com/pkg/new_errors@v1.0.0',
    )
))
@mock.patch('cachito.workers.pkg_manager.add_deps_to_bundle')
@mock.patch('cachito.workers.pkg_manager.GoCacheTemporaryDirectory')
@mock.patch('subprocess.run')
def test_resolve_gomod_deps(
    mock_run, mock_temp_dir, mock_add_deps, dep_replacement, go_list_error_pkg, expected_replace,
    tmpdir, sample_deps, sample_deps_replace, sample_deps_replace_new_name,
):
    mock_cmd_output = _generate_mock_cmd_output(go_list_error_pkg)
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmpdir)

    # Mock the "subprocess.run" calls
    run_side_effects = []
    if dep_replacement:
        run_side_effects.append(
            mock.Mock(returncode=0, stdout=None),  # go mod edit -replace
        )
    run_side_effects.extend([
        mock.Mock(returncode=0, stdout=None),   # go mod download
        mock.Mock(returncode=0, stdout=mock_cmd_output),  # go list -m all
    ])
    mock_run.side_effect = run_side_effects

    archive_path = '/this/is/path/to/archive.tar.gz'
    if dep_replacement is None:
        resolved_deps = resolve_gomod_deps(archive_path, 3)
        expected_deps = sample_deps
    else:
        resolved_deps = resolve_gomod_deps(archive_path, 3, [dep_replacement])
        if dep_replacement.get('new_name'):
            expected_deps = sample_deps_replace_new_name
        else:
            expected_deps = sample_deps_replace

    if expected_replace:
        assert mock_run.call_args_list[0][0][0] == \
            ('go', 'mod', 'edit', '-replace', expected_replace)

    assert resolved_deps == expected_deps
    mock_add_deps.assert_called_once()
    assert mock_add_deps.call_args[0][0].endswith('pkg/mod/cache/download')
    assert mock_add_deps.call_args[0][1] == 'gomod/pkg/mod/cache/download'
    assert mock_add_deps.call_args[0][2] == 3


@mock.patch('cachito.workers.pkg_manager.GoCacheTemporaryDirectory')
@mock.patch('subprocess.run')
def test_resolve_gomod_deps_unused_dep(mock_run, mock_temp_dir, tmpdir):
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmpdir)

    # Mock the "subprocess.run" calls
    mock_run.side_effect = [
        mock.Mock(returncode=0, stdout=None),  # go mod edit -replace
        mock.Mock(returncode=0, stdout=None),   # go mod download
        mock.Mock(returncode=0, stdout=_generate_mock_cmd_output()),  # go list -m all
    ]

    expected_error = 'The following gomod dependency replacements don\'t apply: pizza'
    with pytest.raises(CachitoError, match=expected_error):
        resolve_gomod_deps(
            '/path/archive.tar.gz', 3, [{'name': 'pizza', 'type': 'gomod', 'version': 'v1.0.0'}])


@pytest.mark.parametrize(('go_mod_rc', 'go_list_rc'), ((0, 1), (1, 0)))
@mock.patch('cachito.workers.pkg_manager.GoCacheTemporaryDirectory')
@mock.patch('subprocess.run')
def test_go_list_cmd_failure(
    mock_run, mock_temp_dir, tmpdir, go_mod_rc, go_list_rc
):
    archive_path = '/this/is/path/to/archive.tar.gz'
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmpdir)

    # Mock the "subprocess.run" calls
    mock_run.side_effect = [
        mock.Mock(returncode=go_mod_rc, stdout=None),   # go mod download
        mock.Mock(returncode=go_list_rc, stdout=_generate_mock_cmd_output())  # go list -m all
    ]

    with pytest.raises(CachitoError) as exc_info:
        resolve_gomod_deps(archive_path, 1)
    assert str(exc_info.value) == 'Processing gomod dependencies failed'


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
