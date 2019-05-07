# SPDX-License-Identifier: GPL-3.0-or-later
import io
import os
import tarfile
from unittest import mock

import pytest
from requests import Timeout
from cachito.errors import CachitoError

from cachito.workers import tasks


@mock.patch('cachito.workers.tasks.Git')
def test_fetch_app_source(mock_git):
    url = 'https://github.com/release-engineering/retrodep.git'
    ref = 'c50b93a32df1c9d700e3e80996845bc2e13be848'
    tasks.fetch_app_source(url, ref)
    mock_git.assert_called_once_with(url, ref)
    mock_git.return_value.fetch_source.assert_called_once_with()


@mock.patch('cachito.workers.tasks.Git')
def test_fetch_app_source_request_timed_out(mock_git):
    url = 'https://github.com/release-engineering/retrodep.git'
    ref = 'c50b93a32df1c9d700e3e80996845bc2e13be848'
    mock_git.return_value.fetch_source.side_effect = Timeout('The request timed out')
    with pytest.raises(CachitoError, match='The connection timed out while downloading the source'):
        tasks.fetch_app_source(url, ref)


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
