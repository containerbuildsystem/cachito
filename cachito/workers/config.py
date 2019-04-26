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
    cachito_archives_dir = os.path.join(tempfile.gettempdir(), 'cachito-archives')
    cachito_log_level = 'DEBUG'


class TestingConfig(DevelopmentConfig):
    """The testing Cachito Celery configuration."""


def configure_celery(celery_app):
    """
    Configure the Celery application instance.

    :param celery.Celery celery: the Celery application instance to configure
    """
    config = ProductionConfig
    prod_config_file_path = '/etc/cachito/celery.py'
    if os.getenv('CACHITO_DEV', '').lower() == 'true':
        config = DevelopmentConfig
        # When in development mode, create the archives directory for the user
        if not os.path.isdir(config.cachito_archives_dir):
            os.mkdir(config.cachito_archives_dir)
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
    archives_dir = conf.get('cachito_archives_dir')
    if archives_dir is None or not os.path.isdir(archives_dir):
        raise ConfigError(
            'The configuration "cachito_archives_dir" must be set to an existing directory'
        )


def get_worker_config():
    # Import this here to avoid a circular import
    import cachito.workers.tasks
    return cachito.workers.tasks.app.conf
