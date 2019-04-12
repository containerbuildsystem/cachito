# SPDX-License-Identifier: GPL-3.0-or-later
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
