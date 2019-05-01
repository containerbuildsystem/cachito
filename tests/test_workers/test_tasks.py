# SPDX-License-Identifier: GPL-3.0-or-later
import os
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


@mock.patch('cachito.workers.tasks.get_worker_config')
@mock.patch('cachito.workers.tasks.shutil.copy')
@mock.patch('cachito.workers.tasks.Git')
def test_fetch_app_source_with_copy_cache(mock_git, mock_copy, mock_get_config, tmpdir):
    url = 'https://github.com/release-engineering/retrodep.git'
    ref = 'c50b93a32df1c9d700e3e80996845bc2e13be848'
    relative_copy_cache = os.path.join('spam/app.tar.gz')
    absolute_copy_cache = str(tmpdir.join(relative_copy_cache))

    mock_config = mock.Mock()
    mock_config.cachito_shared_dir = str(tmpdir)
    mock_get_config.return_value = mock_config

    tasks.fetch_app_source(url, ref, copy_cache_to=relative_copy_cache)
    mock_copy.assert_called_once_with(mock_git().archive_path, absolute_copy_cache)


@mock.patch('cachito.workers.tasks.Git')
def test_fetch_app_source_request_timed_out(mock_git):
    url = 'https://github.com/release-engineering/retrodep.git'
    ref = 'c50b93a32df1c9d700e3e80996845bc2e13be848'
    mock_git.return_value.fetch_source.side_effect = Timeout('The request timed out')
    with pytest.raises(CachitoError, match='The connection timed out while downloading the source'):
        tasks.fetch_app_source(url, ref)
