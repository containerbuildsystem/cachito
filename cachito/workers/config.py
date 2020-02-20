# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
import logging

import kombu

from cachito.errors import ConfigError


ARCHIVES_VOLUME = os.path.join(tempfile.gettempdir(), 'cachito-archives')


class Config(object):
    """The base Cachito Celery configuration."""
    # When publishing a message, don't continuously retry or else the HTTP connection times out
    broker_transport_options = {
        'max_retries': 10,
    }
    cachito_auth_type = None
    cachito_log_level = 'INFO'
    # The timeout when downloading application source archives from sources such as GitHub
    cachito_download_timeout = 120
    # The timeout when making a Cachito API request
    cachito_api_timeout = 60
    # Configurable number of days before which a request becomes stale
    cachito_request_lifetime = 1
    # The task messages will be acknowledged after the task has been executed,
    # instead of just before
    task_acks_late = True
    # Don't use the default 'celery' queue and routing key
    task_default_queue = 'cachito'
    task_default_routing_key = 'cachito'
    # By default, have the worker process general and golang tasks
    task_queues = (
        kombu.Queue('cachito'),
        kombu.Queue('cachito_golang', routing_key='cachito.golang'),
    )
    # Requeue the message if the worker abruptly exits or is signaled
    task_reject_on_worker_lost = True
    # Route golang tasks to a separate queue. This will be more useful when there's more than one
    # type of worker.
    task_routes = {
        'cachito.workers.tasks.golang.*': {
            'queue': 'cachito_golang',
            'routing_key': 'cachito.golang',
        },
    }
    # Only allow a single process so the concurrency is only based on the number of instances of the
    # worker container
    worker_concurrency = 1
    # Don't allow the worker to fetch more messages than it can handle at a time. This is so that
    # other tasks aren't starved when processing a large archive.
    worker_prefetch_multiplier = 1
    # Configurable batch size of the json payload so that the cachito API request doesn't time out
    cachito_deps_patch_batch_size = 50


class ProductionConfig(Config):
    """The production Cachito Celery configuration."""
    cachito_auth_type = 'kerberos'


class DevelopmentConfig(Config):
    """The development Cachito Celery configuration."""

    broker_url = 'amqp://cachito:cachito@rabbitmq:5672//'
    cachito_api_url = 'http://cachito-api:8080/api/v1/'
    cachito_athens_url = 'http://athens:3000'
    cachito_bundles_dir = os.path.join(ARCHIVES_VOLUME, 'bundles')
    cachito_log_level = 'DEBUG'
    cachito_sources_dir = os.path.join(ARCHIVES_VOLUME, 'sources')


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
        for required_dir in (config.cachito_bundles_dir, config.cachito_sources_dir):
            if not os.path.isdir(required_dir):
                os.mkdir(required_dir)
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
    Perform basic validation on the Celery configuration when the worker is initialized.

    :param celery.app.utils.Settings conf: the Celery application configuration to validate
    :raises ConfigError: if the configuration is invalid
    """
    for required_dir_conf in ('cachito_bundles_dir', 'cachito_sources_dir'):
        required_dir = conf.get(required_dir_conf)
        if not required_dir or not os.path.isdir(required_dir):
            raise ConfigError(
                f'The configuration "{required_dir_conf}" must be set to an existing directory'
            )

    if not conf.get('cachito_api_url'):
        raise ConfigError('The configuration "cachito_api_url" must be set')


def get_worker_config():
    # Import this here to avoid a circular import
    import cachito.workers.tasks.celery
    return cachito.workers.tasks.celery.app.conf
