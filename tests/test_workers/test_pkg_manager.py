# SPDX-License-Identifier: GPL-3.0-or-later
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


def make_expected_output():
    return [
        {'type': 'gomod', 'name': 'github.com/Masterminds/semver', 'version': 'v1.4.2'},
        {'type': 'gomod', 'name': 'github.com/kr/pretty', 'version': 'v0.1.0'},
        {'type': 'gomod', 'name': 'github.com/kr/pty', 'version': 'v1.1.1'},
        {'type': 'gomod', 'name': 'github.com/kr/text', 'version': 'v0.1.0'},
        {'type': 'gomod', 'name': 'github.com/op/go-logging',
         'version': 'v0.0.0-20160315200505-970db520ece7'},
        {'type': 'gomod', 'name': 'github.com/pkg/errors', 'version': 'v0.8.1'},
        {'type': 'gomod', 'name': 'golang.org/x/crypto',
         'version': 'v0.0.0-20190308221718-c2843e01d9a2'},
        {'type': 'gomod', 'name': 'golang.org/x/net',
         'version': 'v0.0.0-20190311183353-d8887717615a'},
        {'type': 'gomod', 'name': 'golang.org/x/sys',
         'version': 'v0.0.0-20190215142949-d0b11bdaac8a'},
        {'type': 'gomod', 'name': 'golang.org/x/text', 'version': 'v0.3.0'},
        {'type': 'gomod', 'name': 'golang.org/x/tools',
         'version': 'v0.0.0-20190325161752-5a8dccf5b48a'},
        {'type': 'gomod', 'name': 'gopkg.in/check.v1',
         'version': 'v1.0.0-20180628173108-788fd7840127'},
        {'type': 'gomod', 'name': 'gopkg.in/yaml.v2', 'version': 'v2.2.2'},
    ]


@mock.patch('tempfile.TemporaryDirectory')
@mock.patch('subprocess.Popen')
@mock.patch('tarfile.open')
def test_resolve_gomod_deps(
    mock_tarfile_open, mock_popen, mock_temp_dir, tmpdir
):
    archive_path = '/this/is/path/to/archive.tar.gz'
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmpdir)

    # Mock the "go list" call
    output = mock_cmd_output

    mock_popen.return_value.communicate.return_value = (output, '')
    mock_popen.return_value.returncode = 0

    # Mock the opening of the tar file containing application source code
    mock_final_tarfile = mock.Mock()
    mock_final_tarfile.extractall.return_value = None
    mock_tarfile_open.return_value.__enter__.side_effect = [
        mock_final_tarfile,
    ]

    resolved_deps = resolve_gomod_deps(archive_path)

    assert resolved_deps == make_expected_output()


@mock.patch('tempfile.TemporaryDirectory')
@mock.patch('subprocess.Popen')
@mock.patch('tarfile.open')
def test_go_list_cmd_failure(
    mock_tarfile_open, mock_popen, mock_temp_dir, tmpdir
):
    archive_path = '/this/is/path/to/archive.tar.gz'
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmpdir)

    mock_popen.return_value.communicate.return_value = ('', '')
    mock_popen.return_value.returncode = 1

    # Mock the opening of the tar file containing application source code
    mock_final_tarfile = mock.Mock()
    mock_final_tarfile.extractall.return_value = None
    mock_tarfile_open.return_value.__enter__.side_effect = [
        mock_final_tarfile,
    ]

    with pytest.raises(CachitoError) as exc_info:
        resolve_gomod_deps(archive_path)
    assert str(exc_info.value) == 'Fetching gomod dependencies failed'
