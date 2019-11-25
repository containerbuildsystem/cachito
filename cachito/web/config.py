# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile

from cachito.errors import ConfigError

TEST_DB_FILE = os.path.join(tempfile.gettempdir(), 'cachito.db')


class Config(object):
    """The base Cachito Flask configuration."""
    # Additional loggers to set to the level defined in CACHITO_LOG_LEVEL
    CACHITO_ADDITIONAL_LOGGERS = []
    # This sets the level of the "flask.app" logger, which is accessed from current_app.logger
    CACHITO_LOG_LEVEL = 'INFO'
    CACHITO_LOG_FORMAT = '%(asctime)s %(name)s %(levelname)s %(module)s.%(funcName)s %(message)s'
    CACHITO_MAX_PER_PAGE = 100
    # Users that are allowed to use the "user" property when creating a request
    CACHITO_USER_REPRESENTATIVES = []
    CACHITO_WORKER_USERNAMES = []


class ProductionConfig(Config):
    """The production Cachito Flask configuration."""
    DEBUG = False


class DevelopmentConfig(Config):
    """The development Cachito Flask configuration."""
    CACHITO_BUNDLES_DIR = os.path.join(tempfile.gettempdir(), 'cachito-archives', 'bundles')
    CACHITO_LOG_LEVEL = 'DEBUG'
    SQLALCHEMY_DATABASE_URI = 'postgresql+psycopg2://cachito:cachito@db:5432/cachito'
    SQLALCHEMY_TRACK_MODIFICATIONS = True
    LOGIN_DISABLED = True


class TestingConfig(DevelopmentConfig):
    """The testing Cachito Flask configuration."""
    CACHITO_USER_REPRESENTATIVES = ['tbrady@DOMAIN.LOCAL']
    CACHITO_WORKER_USERNAMES = ['worker@domain.local']
    # IMPORTANT: don't use in-memory sqlite. Alembic migrations will create a new
    # connection producing a new instance of the database which is deleted immediately
    # after the migration completes...
    #   https://github.com/miguelgrinberg/Flask-Migrate/issues/153
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{TEST_DB_FILE}'
    DEBUG = True
    LOGIN_DISABLED = False
    TESTING = True


def validate_cachito_config(config, cli=False):
    """
    Perform basic validatation on the Cachito configuration.

    :param dict config: a dictionary of configuration values
    :param bool cli: a boolean that denotes if the configuration should be validated for the CLI
    :raises ConfigError: if the configuration is invalid
    """

    # Validate the required config variables
    for config_var in (
        'CACHITO_LOG_LEVEL', 'CACHITO_MAX_PER_PAGE', 'CACHITO_LOG_FORMAT', 'CACHITO_BUNDLES_DIR',
    ):
        if config_var == 'CACHITO_BUNDLES_DIR':
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
        elif not config.get(config_var):
            raise ConfigError(
                f'The configuration "{config_var}" must be set'
            )
