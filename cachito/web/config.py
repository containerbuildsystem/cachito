# SPDX-License-Identifier: GPL-3.0-or-later


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
    pass
