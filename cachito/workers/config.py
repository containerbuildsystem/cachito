# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
import logging

from cachito.errors import ConfigError


class Config(object):
    """The base Cachito Celery configuration."""

    # Don't use the default 'celery' queue
    task_default_queue = 'cachito'
    cachito_log_level = 'INFO'


class ProductionConfig(Config):
    """The production Cachito Celery configuration."""


class DevelopmentConfig(Config):
    """The development Cachito Celery configuration."""

    broker_url = 'amqp://cachito:cachito@rabbitmq:5672//'
    athens_url = 'http://athens:3000'
    cachito_api_url = 'http://cachito-api:8080/api/v1/'
    cachito_archives_dir = os.path.join(tempfile.gettempdir(), 'cachito-archives')
    cachito_shared_dir = os.path.join(tempfile.gettempdir(), 'cachito-shared')
    cachito_log_level = 'DEBUG'

    result_backend = 'rpc'
    result_persistent = True


class TestingConfig(DevelopmentConfig):
    """The testing Cachito Celery configuration."""
    cachito_api_url = 'http://cachito.domain.local/api/v1/'


def configure_celery(celery_app):
    """
    Configure the Celery application instance.

    :param celery.Celery celery: the Celery application instance to configure
    """
    config = ProductionConfig
    prod_config_file_path = '/etc/cachito/celery.py'
    if os.getenv('CACHITO_DEV', '').lower() == 'true':
        config = DevelopmentConfig
        # When in development mode, create required directories for the user
        for dirname in (config.cachito_archives_dir, config.cachito_shared_dir):
            if not os.path.isdir(dirname):
                os.mkdir(dirname)
    elif os.getenv('CACHITO_TESTING', 'false').lower() == 'true':
        config = TestingConfig
    elif os.path.isfile(prod_config_file_path):
        # Celery doesn't support importing config files that aren't part of a Python path. This is
        # a hack taken from flask.config.from_pyfile.
        _user_config = {}
        with open(prod_config_file_path, mode='rb') as config_file:
            exec(compile(config_file.read(), prod_config_file_path, 'exec'), _user_config)

        # Celery doesn't support configuring from multiple objects, so this is a way for
        # the configuration in prod_config_file_path to override the defaults in ProductionConfig
        config = ProductionConfig()
        for key, value in _user_config.items():
            # The _user_config dictionary will contain the __builtins__ key, which we need to skip
            if not key.startswith('__'):
                setattr(config, key, value)

    celery_app.config_from_object(config, force=True)
    logging.getLogger('cachito.workers').setLevel(celery_app.conf.cachito_log_level)


def validate_celery_config(conf, **kwargs):
    """
    Perform basic validatation on the Celery configuration when the worker is initialized.

    :param celery.app.utils.Settings conf: the Celery application configuration to validate
    :raises ConfigError: if the configuration is invalid
    """
    for required_dir_conf in ('cachito_archives_dir', 'cachito_shared_dir'):
        required_dir = conf.get(required_dir_conf)
        if not required_dir or not os.path.isdir(required_dir):
            raise ConfigError(
                f'The configuration "{required_dir_conf}" must be set to an existing directory'
            )

    if not conf.get('cachito_api_url'):
        raise ConfigError('The configuration "cachito_api_url" must be set')


def get_worker_config():
    # Import this here to avoid a circular import
    import cachito.workers.tasks
    return cachito.workers.tasks.app.conf
