# SPDX-License-Identifier: GPL-3.0-or-later
from unittest.mock import patch
from io import BytesIO

import celery
import pytest

from cachito.workers.config import configure_celery, validate_celery_config
from cachito.errors import ConfigError


@patch('os.path.isfile', return_value=False)
def test_configure_celery_with_classes(mock_isfile):
    celery_app = celery.Celery()
    assert celery_app.conf.task_default_queue == 'celery'
    configure_celery(celery_app)
    assert celery_app.conf.task_default_queue == 'cachito'


@patch('os.getenv')
@patch('os.path.isfile', return_value=True)
@patch('cachito.workers.config.open')
def test_configure_celery_with_classes_and_files(mock_open, mock_isfile, mock_getenv):
    mock_getenv.return_value = ''
    mock_open.return_value = BytesIO(
        b'task_default_queue = "not-cachito"\ntimezone="America/New_York"\n')
    celery_app = celery.Celery()
    assert celery_app.conf.task_default_queue == 'celery'
    assert celery_app.conf.timezone is None
    configure_celery(celery_app)
    assert celery_app.conf.task_default_queue == 'not-cachito'
    assert celery_app.conf.timezone == 'America/New_York'


@patch('os.path.isdir', return_value=True)
def test_validate_celery_config(mock_isdir):
    celery_app = celery.Celery()
    celery_app.conf.cachito_api_url = 'http://cachito-api/api/v1/'
    celery_app.conf.cachito_bundles_dir = '/tmp/some-path/bundles'
    celery_app.conf.cachito_sources_dir = '/tmp/some-path/sources'
    validate_celery_config(celery_app.conf)
    mock_isdir.assert_any_call(celery_app.conf.cachito_bundles_dir)
    mock_isdir.assert_any_call(celery_app.conf.cachito_sources_dir)


@patch('os.path.isdir', return_value=True)
@pytest.mark.parametrize('bundles_dir, sources_dir', ((False, True), (True, False)))
def test_validate_celery_config_failure(mock_isdir, bundles_dir, sources_dir):
    celery_app = celery.Celery()
    celery_app.conf.cachito_sources_dir = '/tmp/some-path/sources'

    if bundles_dir:
        celery_app.conf.cachito_bundles_dir = '/tmp/some-path/bundles'
        dir_name = 'cachito_sources_dir'
    elif sources_dir:
        celery_app.conf.cachito_sources_dir = '/tmp/some-path/sources'
        dir_name = 'cachito_bundles_dir'

    setattr(celery_app.conf, dir_name, None)
    expected = f'The configuration "{dir_name}" must be set to an existing directory'
    with pytest.raises(ConfigError, match=expected):
        validate_celery_config(celery_app.conf)
