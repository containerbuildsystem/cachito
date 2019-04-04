# SPDX-License-Identifier: GPL-3.0-or-later

TEST_DB_FILE = '/tmp/cachito.db'


class Config(object):
    """The base Cachito Flask configuration."""
    pass


class ProductionConfig(Config):
    """The production Cachito Flask configuration."""
    DEBUG = False


class DevelopmentConfig(Config):
    """The development Cachito Flask configuration."""
    SQLALCHEMY_DATABASE_URI = 'postgresql+psycopg2://cachito:cachito@db:5432/cachito'
    SQLALCHEMY_TRACK_MODIFICATIONS = True


class TestingConfig(DevelopmentConfig):
    """The testing Cachito Flask configuration."""
    # IMPORTANT: don't use in-memory sqlite. Alembic migrations will create a new
    # connection producing a new instance of the database which is deleted immediately
    # after the migration completes...
    #   https://github.com/miguelgrinberg/Flask-Migrate/issues/153
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{TEST_DB_FILE}'
    DEBUG = True
    TESTING = True
