# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
import logging

import kombu

from cachito.errors import ConfigError


ARCHIVES_VOLUME = os.path.join(tempfile.gettempdir(), "cachito-archives")


class Config(object):
    """The base Cachito Celery configuration."""

    # When publishing a message, don't continuously retry or else the HTTP connection times out
    broker_transport_options = {"max_retries": 10}
    # Refer to README.md for information on all the Cachito configuration options
    cachito_api_timeout = 60
    cachito_auth_type = None
    cachito_default_environment_variables = {
        "gomod": {},
        "npm": {
            "CHROMEDRIVER_SKIP_DOWNLOAD": "true",
            "CYPRESS_INSTALL_BINARY": "0",
            "GECKODRIVER_SKIP_DOWNLOAD": "true",
            "SKIP_SASS_BINARY_DOWNLOAD_FOR_CI": "true",
        },
    }
    cachito_deps_patch_batch_size = 50
    cachito_download_timeout = 120
    cachito_gomod_strict_vendor = False
    cachito_log_level = "INFO"
    cachito_js_download_batch_size = 30
    cachito_nexus_ca_cert = "/etc/cachito/nexus_ca.pem"
    cachito_nexus_hoster_password = None
    cachito_nexus_hoster_url = None
    cachito_nexus_hoster_username = None
    cachito_nexus_js_hosted_repo_name = "cachito-js-hosted"
    cachito_nexus_npm_proxy_repo_url = "http://localhost:8081/repository/cachito-js/"
    cachito_nexus_proxy_password = None
    cachito_nexus_proxy_username = None
    cachito_nexus_request_repo_prefix = "cachito-"
    cachito_nexus_timeout = 60
    cachito_nexus_username = "cachito"
    cachito_request_lifetime = 1
    include = [
        "cachito.workers.tasks.general",
        "cachito.workers.tasks.gomod",
        "cachito.workers.tasks.npm",
    ]
    # The task messages will be acknowledged after the task has been executed,
    # instead of just before
    task_acks_late = True
    # Don't use the default 'celery' queue and routing key
    task_default_queue = "cachito"
    task_default_routing_key = "cachito"
    # By default, have the worker process general, gomod, and npm tasks
    task_queues = (
        kombu.Queue("cachito"),
        kombu.Queue("cachito_gomod", routing_key="cachito.gomod"),
        kombu.Queue("cachito_npm", routing_key="cachito.npm"),
    )
    # Requeue the message if the worker abruptly exits or is signaled
    task_reject_on_worker_lost = True
    # Route gomod tasks and npm tasks to separate queues. This is useful if workers are dedicated
    # to specific package managers.
    task_routes = {
        "cachito.workers.tasks.gomod.*": {
            "queue": "cachito_gomod",
            "routing_key": "cachito.gomod",
        },
        "cachito.workers.tasks.npm.*": {"queue": "cachito_npm", "routing_key": "cachito.npm"},
    }
    # Only allow a single process so the concurrency is only based on the number of instances of the
    # worker container
    worker_concurrency = 1
    # Don't allow the worker to fetch more messages than it can handle at a time. This is so that
    # other tasks aren't starved when processing a large archive.
    worker_prefetch_multiplier = 1


class ProductionConfig(Config):
    """The production Cachito Celery configuration."""

    cachito_auth_type = "kerberos"


class DevelopmentConfig(Config):
    """The development Cachito Celery configuration."""

    broker_url = "amqp://cachito:cachito@rabbitmq:5672//"
    cachito_api_url = "http://cachito-api:8080/api/v1/"
    cachito_athens_url = "http://athens:3000"
    cachito_bundles_dir = os.path.join(ARCHIVES_VOLUME, "bundles")
    cachito_log_level = "DEBUG"
    cachito_nexus_password = "cachito"
    cachito_nexus_proxy_password = "cachito_unprivileged"
    cachito_nexus_proxy_username = "cachito_unprivileged"
    cachito_nexus_url = "http://nexus:8081"
    cachito_sources_dir = os.path.join(ARCHIVES_VOLUME, "sources")


class TestingConfig(DevelopmentConfig):
    """The testing Cachito Celery configuration."""

    cachito_api_url = "http://cachito.domain.local/api/v1/"
    cachito_default_environment_variables = {
        "gomod": {"GO111MODULE": "on"},
        "npm": {"CHROMEDRIVER_SKIP_DOWNLOAD": "true", "SKIP_SASS_BINARY_DOWNLOAD_FOR_CI": "true"},
    }


def configure_celery(celery_app):
    """
    Configure the Celery application instance.

    :param celery.Celery celery: the Celery application instance to configure
    """
    config = ProductionConfig
    prod_config_file_path = "/etc/cachito/celery.py"
    if os.getenv("CACHITO_DEV", "").lower() == "true":
        config = DevelopmentConfig
        # When in development mode, create required directories for the user
        for required_dir in (config.cachito_bundles_dir, config.cachito_sources_dir):
            if not os.path.isdir(required_dir):
                os.mkdir(required_dir)
    elif os.getenv("CACHITO_TESTING", "false").lower() == "true":
        config = TestingConfig
    elif os.path.isfile(prod_config_file_path):
        # Celery doesn't support importing config files that aren't part of a Python path. This is
        # a hack taken from flask.config.from_pyfile.
        _user_config = {}
        with open(prod_config_file_path, mode="rb") as config_file:
            exec(compile(config_file.read(), prod_config_file_path, "exec"), _user_config)

        # Celery doesn't support configuring from multiple objects, so this is a way for
        # the configuration in prod_config_file_path to override the defaults in ProductionConfig
        config = ProductionConfig()
        for key, value in _user_config.items():
            # The _user_config dictionary will contain the __builtins__ key, which we need to skip
            if not key.startswith("__"):
                setattr(config, key, value)

    celery_app.config_from_object(config, force=True)
    logging.getLogger("cachito.workers").setLevel(celery_app.conf.cachito_log_level)


def validate_celery_config(conf, **kwargs):
    """
    Perform basic validation on the Celery configuration when the worker is initialized.

    :param celery.app.utils.Settings conf: the Celery application configuration to validate
    :raises ConfigError: if the configuration is invalid
    """
    for required_dir_conf in ("cachito_bundles_dir", "cachito_sources_dir"):
        required_dir = conf.get(required_dir_conf)
        if not required_dir or not os.path.isdir(required_dir):
            raise ConfigError(
                f'The configuration "{required_dir_conf}" must be set to an existing directory'
            )

    if not conf.get("cachito_api_url"):
        raise ConfigError('The configuration "cachito_api_url" must be set')

    hoster_username = conf.get("cachito_nexus_hoster_username")
    hoster_password = conf.get("cachito_nexus_hoster_password")
    if (hoster_username or hoster_password) and not (hoster_username and hoster_password):
        raise ConfigError(
            'If "cachito_nexus_hoster_username" or "cachito_nexus_hoster_password" is set, '
            "the other must also be set"
        )

    if conf.get("cachito_auth_type") == "cert" and conf.get("cachito_auth_cert") is None:
        raise ConfigError('cachito_auth_cert configuration must be set for "cert" authentication')

    if not isinstance(conf.get("cachito_default_environment_variables"), dict):
        raise ConfigError(
            'The configuration "cachito_default_environment_variables" must be a dictionary'
        )

    default_env_vars = conf.cachito_default_environment_variables
    for value in default_env_vars.values():
        if not isinstance(value, dict):
            raise ConfigError(
                'The configuration "cachito_default_environment_variables" must be a '
                "dictionary of dictionaries"
            )

    invalid_gomod_env_vars = default_env_vars.get("gomod", {}).keys() & {"GOCACHE", "GOPATH"}
    if invalid_gomod_env_vars:
        raise ConfigError(
            'The configuration "cachito_default_environment_variables.gomod" cannot overwrite the '
            f"following environment variables: {', '.join(invalid_gomod_env_vars)}"
        )


def validate_nexus_config():
    """
    Perform validation on the Celery configuration for package managers that require Nexus.

    :raise ConfigError: if the Celery configuration isn't configured for Nexus
    """
    conf = get_worker_config()
    for nexus_conf in (
        "cachito_nexus_password",
        "cachito_nexus_url",
        "cachito_nexus_username",
    ):
        if not conf.get(nexus_conf):
            raise ConfigError(
                f'The configuration "{nexus_conf}" must be set for this package manager'
            )


def validate_npm_config():
    """
    Perform validation on the Celery configuration for the npm package manager.

    :raise ConfigError: if the Celery configuration isn't configured for npm
    """
    validate_nexus_config()
    conf = get_worker_config()
    if not conf.get("cachito_nexus_npm_proxy_repo_url"):
        raise ConfigError(
            'The configuration "cachito_nexus_npm_proxy_repo_url" must be set for this package '
            "manager"
        )


def get_worker_config():
    """Get the Celery worker configuration."""
    # Import this here to avoid a circular import
    import cachito.workers.tasks.celery

    return cachito.workers.tasks.celery.app.conf
