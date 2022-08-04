# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
from typing import List, Optional

from cachito.errors import ConfigError

TEST_DB_FILE = os.path.join(os.environ.get("TOX_ENV_DIR") or tempfile.gettempdir(), "cachito.db")


class Config(object):
    """The base Cachito Flask configuration."""

    DEBUG = False
    # Additional loggers to set to the level defined in CACHITO_LOG_LEVEL
    CACHITO_ADDITIONAL_LOGGERS: List[str] = ["cachito.common.packages_data"]
    CACHITO_DEFAULT_PACKAGE_MANAGERS: List[str] = ["gomod"]
    # This sets the level of the "flask.app" logger, which is accessed from current_app.logger
    CACHITO_LOG_LEVEL = "INFO"
    CACHITO_LOG_FORMAT = "[%(asctime)s %(name)s %(levelname)s %(module)s.%(funcName)s] %(message)s"
    CACHITO_MAX_PER_PAGE = 100
    # Pairs of mutually exclusive package managers (cannot process the same package)
    CACHITO_MUTUALLY_EXCLUSIVE_PACKAGE_MANAGERS = [("npm", "yarn")]
    CACHITO_PACKAGE_MANAGERS = ["gomod"]
    CACHITO_REQUEST_FILE_LOGS_DIR: Optional[str] = None
    # Users that are allowed to use the "user" property when creating a request
    CACHITO_USER_REPRESENTATIVES: List[str] = []
    CACHITO_WORKER_USERNAMES: List[str] = []
    LOGIN_DISABLED = False
    TESTING = False

    # Temp dir used by the Prometheus Flask Exporter to coalesce the metrics from the threads
    if "PROMETHEUS_MULTIPROC_DIR" not in os.environ.keys():
        raise ConfigError(
            "The environment variable PROMETHEUS_MULTIPROC_DIR must be set prior to execution"
        )
    else:
        PROMETHEUS_METRICS_TEMP_DIR = os.environ["PROMETHEUS_MULTIPROC_DIR"]


class ProductionConfig(Config):
    """The production Cachito Flask configuration."""

    pass


class DevelopmentConfig(Config):
    """The development Cachito Flask configuration."""

    DEBUG = True
    CACHITO_BUNDLES_DIR = os.path.join(tempfile.gettempdir(), "cachito-archives", "bundles")
    CACHITO_LOG_LEVEL = "DEBUG"
    CACHITO_MUTUALLY_EXCLUSIVE_PACKAGE_MANAGERS = [("npm", "yarn"), ("gomod", "git-submodule")]
    CACHITO_PACKAGE_MANAGERS = ["gomod", "npm", "pip", "git-submodule", "yarn", "rubygems"]
    CACHITO_REQUEST_FILE_LOGS_DIR = "/var/log/cachito/requests"
    SQLALCHEMY_DATABASE_URI = "postgresql+psycopg2://cachito:cachito@db:5432/cachito"
    SQLALCHEMY_TRACK_MODIFICATIONS = True
    LOGIN_DISABLED = True


class TestingConfig(DevelopmentConfig):
    """The testing Cachito Flask configuration."""

    CACHITO_USER_REPRESENTATIVES = ["tbrady@DOMAIN.LOCAL"]
    CACHITO_WORKER_USERNAMES = ["worker@DOMAIN.LOCAL"]
    # IMPORTANT: don't use in-memory sqlite. Alembic migrations will create a new
    # connection producing a new instance of the database which is deleted immediately
    # after the migration completes...
    #   https://github.com/miguelgrinberg/Flask-Migrate/issues/153
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{TEST_DB_FILE}"
    LOGIN_DISABLED = False
    TESTING = True


class TestingConfigNoAuth(TestingConfig):
    """The testing Cachito Flask configuration without authentication."""

    # This is needed because Flask seems to read the LOGIN_DISABLED setting
    # and configure the relevant extensions at app creation time. Changing this
    # during a test run still leaves login enabled. This behavior also applies
    # to ENV and DEBUG config values:
    #   https://flask.palletsprojects.com/en/1.1.x/config/#environment-and-debug-features
    LOGIN_DISABLED = True


def validate_cachito_config(config, cli=False):
    """
    Perform basic validatation on the Cachito configuration.

    :param dict config: a dictionary of configuration values
    :param bool cli: a boolean that denotes if the configuration should be validated for the CLI
    :raises ConfigError: if the configuration is invalid
    """
    # Validate the required config variables
    for config_var in (
        "CACHITO_DEFAULT_PACKAGE_MANAGERS",
        "CACHITO_LOG_LEVEL",
        "CACHITO_MAX_PER_PAGE",
        "CACHITO_MUTUALLY_EXCLUSIVE_PACKAGE_MANAGERS",
        "CACHITO_LOG_FORMAT",
        "CACHITO_BUNDLES_DIR",
        "SQLALCHEMY_DATABASE_URI",
        "PROMETHEUS_METRICS_TEMP_DIR",
    ):
        if config_var == "CACHITO_BUNDLES_DIR":
            if cli:
                # Don't verify CACHITO_BUNDLES_DIR if this is coming from the CLI since it's not
                # used and requires the CACHITO_BUNDLES_DIR to exist. This is a hassle for hooks
                # in the deployments for OpenShift/Kubernetes.
                continue

            required_dir = config.get(config_var)
            if not required_dir or not os.path.isdir(required_dir):
                raise ConfigError(
                    f'The configuration "{config_var}" must be set to an existing directory'
                )
        elif config_var == "CACHITO_MUTUALLY_EXCLUSIVE_PACKAGE_MANAGERS":
            mutually_exclusive = config.get(config_var)
            if mutually_exclusive is None:
                raise ConfigError(f'The configuration "{config_var}" must be set')

            if not all(
                isinstance(pair, (tuple, list)) and len(pair) == 2 for pair in mutually_exclusive
            ):
                raise ConfigError(
                    f'All values in "{config_var}" must be pairs (2-tuples or 2-item lists)'
                )
        elif not config.get(config_var):
            raise ConfigError(f'The configuration "{config_var}" must be set')
