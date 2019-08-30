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
    celery_app.conf.cachito_archives_dir = '/tmp/some-path'
    celery_app.conf.cachito_shared_dir = '/tmp/some-other-path'
    celery_app.conf.cachito_api_url = 'http://cachito-api/api/v1/'
    validate_celery_config(celery_app.conf)
    mock_isdir.assert_called_once_with(celery_app.conf.cachito_archives_dir)


def test_validate_celery_config_failure():
    celery_app = celery.Celery()
    celery_app.conf.cachito_archives_dir = None
    expected = 'The configuration "cachito_archives_dir" must be set to an existing directory'
    with pytest.raises(ConfigError, match=expected):
        validate_celery_config(celery_app.conf)
