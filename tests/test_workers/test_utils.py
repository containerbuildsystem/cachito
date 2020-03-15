# SPDX-License-Identifier: GPL-3.0-or-later
from unittest.mock import Mock, patch

from cachito.workers import utils


@patch('cachito.workers.utils.tarfile.open')
def test_extract_app_src(mock_tarfile_open):
    # Mock the opening of the tar file containing application source code
    mock_final_tarfile = Mock()
    mock_final_tarfile.extractall.return_value = None
    mock_tarfile_open.return_value.__enter__.side_effect = [mock_final_tarfile]

    rv = utils.extract_app_src('/tmp/test.tar.gz', '/tmp/bundles')

    assert rv == '/tmp/bundles/app'
