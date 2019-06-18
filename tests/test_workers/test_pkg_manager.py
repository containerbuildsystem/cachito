# SPDX-License-Identifier: GPL-3.0-or-later
import os
from textwrap import dedent
from unittest import mock

import pytest

from cachito.workers.pkg_manager import resolve_gomod_deps
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


@mock.patch('tempfile.TemporaryDirectory')
@mock.patch('subprocess.run')
@mock.patch('tarfile.open')
def test_resolve_gomod_deps(
    mock_tarfile_open, mock_run, mock_temp_dir, tmpdir, sample_deps
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

    resolved_deps = resolve_gomod_deps(archive_path)

    assert resolved_deps == sample_deps


@mock.patch('tempfile.TemporaryDirectory')
@mock.patch('subprocess.run')
@mock.patch('tarfile.open')
def test_resolve_gomod_deps_with_copy_cache(
    mock_tarfile_open, mock_run, mock_temp_dir, tmpdir, sample_deps
):
    archive_path = '/this/is/path/to/archive.tar.gz'
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmpdir)

    dep_cache_partial_path = os.path.join(
        'pkg', 'mod', 'cache', 'download', 'server.com', 'dep1', '@v', 'dep1.zip')

    def side_effect(*args, **kwargs):
        if 'list' not in args[0]:
            # "go list" command
            return mock.Mock(returncode=0, stdout=None)

        # "go mod" command - generate dummy dependency cache
        dep1_path = os.path.join(str(tmpdir), dep_cache_partial_path)
        os.makedirs(os.path.dirname(dep1_path), exist_ok=True)
        with open(dep1_path, 'wb') as f:
            f.write(b'dep1 archive')
        return mock.Mock(returncode=0, stdout=mock_cmd_output)

    mock_run.side_effect = side_effect

    # Mock the opening of the tar file containing application source code
    mock_final_tarfile = mock.Mock()
    mock_final_tarfile.extractall.return_value = None
    mock_tarfile_open.return_value.__enter__.side_effect = [
        mock_final_tarfile,
    ]

    copy_cache_to = os.path.join(str(tmpdir), 'the-cache')
    resolved_deps = resolve_gomod_deps(archive_path, copy_cache_to=copy_cache_to)

    assert resolved_deps == sample_deps
    # Verify cache has been copied to the provided copy_cache_to location under the gomod dir
    assert os.path.exists(os.path.join(copy_cache_to, 'gomod', dep_cache_partial_path))


@pytest.mark.parametrize(('go_mod_rc', 'go_list_rc'), ((0, 1), (1, 0)))
@mock.patch('tempfile.TemporaryDirectory')
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
