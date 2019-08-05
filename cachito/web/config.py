# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile

TEST_DB_FILE = os.path.join(tempfile.gettempdir(), 'cachito.db')


class Config(object):
    """The base Cachito Flask configuration."""
    CACHITO_WAIT_TIMEOUT = 120  # Seconds
    CACHITO_MAX_PER_PAGE = 100
    CACHITO_WORKER_USERNAMES = []


class ProductionConfig(Config):
    """The production Cachito Flask configuration."""
    DEBUG = False


class DevelopmentConfig(Config):
    """The development Cachito Flask configuration."""
    SQLALCHEMY_DATABASE_URI = 'postgresql+psycopg2://cachito:cachito@db:5432/cachito'
    SQLALCHEMY_TRACK_MODIFICATIONS = True
    CACHITO_SHARED_DIR = os.path.join(tempfile.gettempdir(), 'cachito-shared')
    LOGIN_DISABLED = True


class TestingConfig(DevelopmentConfig):
    """The testing Cachito Flask configuration."""
    CACHITO_WORKER_USERNAMES = ['worker@domain.local']
    # IMPORTANT: don't use in-memory sqlite. Alembic migrations will create a new
    # connection producing a new instance of the database which is deleted immediately
    # after the migration completes...
    #   https://github.com/miguelgrinberg/Flask-Migrate/issues/153
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{TEST_DB_FILE}'
    DEBUG = True
    LOGIN_DISABLED = False
    TESTING = True
