# SPDX-License-Identifier: GPL-3.0-or-later
import os

from flask import Flask
from flask_migrate import Migrate

from cachito.web.splash import splash
from cachito.web.api_v1 import api_v1
from cachito.web import db


def load_config(app):
    """
    Determine the correct configuration to use and apply it.

    :param flask.Flask app: a Flask application object
    """
    config_file = None
    if os.getenv('FLASK_ENV') == 'development':
        default_config_obj = 'cachito.web.config.DevelopmentConfig'
    else:
        default_config_obj = 'cachito.web.config.ProductionConfig'
        config_file = '/etc/cachito/settings.py'
    app.config.from_object(default_config_obj)

    if config_file and os.path.isfile(config_file):
        app.config.from_pyfile(config_file)


# See app factory pattern:
#   http://flask.pocoo.org/docs/0.12/patterns/appfactories/
def create_app(config_obj=None):
    """
    Create a Flask application object.

    :param str config_obj: the path to the configuration object to use instead of calling
        load_config
    :return: a Flask application object
    :rtype: flask.Flask
    """
    app = Flask(__name__)
    if config_obj:
        app.config.from_object(config_obj)
    else:
        load_config(app)

    # Initialize the database
    db.init_app(app)
    # Initialize the database migrations
    migrations_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'migrations')
    Migrate(app, db, directory=migrations_dir)

    app.register_blueprint(splash)
    app.register_blueprint(api_v1, url_prefix='/api/v1')
    return app
