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


@patch('cachito.workers.utils.get_worker_config')
def test_get_request_bundle_dir(mock_gwc):
    mock_gwc.return_value.cachito_bundles_dir = '/tmp/some/path'
    rv = utils.get_request_bundle_dir(3)
    assert rv == '/tmp/some/path/temp/3'


@patch('cachito.workers.utils.get_worker_config')
def test_get_request_bundle_path(mock_gwc):
    mock_gwc.return_value.cachito_bundles_dir = '/tmp/some/path'
    rv = utils.get_request_bundle_path(3)
    assert rv == '/tmp/some/path/3.tar.gz'
