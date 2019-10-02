# SPDX-License-Identifier: GPL-3.0-or-later
import os

from flask import current_app, Flask
from flask_login import LoginManager
from flask_migrate import Migrate
import kombu.exceptions
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import default_exceptions, InternalServerError

from cachito.web.auth import user_loader, load_user_from_request
from cachito.web.docs import docs
from cachito.web.api_v1 import api_v1
from cachito.web import db
from cachito.web.errors import json_error
from cachito.errors import ValidationError


def healthcheck():
    """
    Perform an application-level health check.

    This is not part of the published API because it is intended to be used by monitoring tools.
    This returns a 200 response if the application is alive and able to serve requests. It returns
    a 500 response otherwise.
    """
    try:
        db.session.execute('SELECT 1 FROM request LIMIT 0').fetchall()
    except SQLAlchemyError:
        current_app.logger.exception('The healthcheck failed when querying the database')
        raise InternalServerError()

    return ('OK', 200, [('Content-Type', 'text/plain')])


def load_config(app):
    """
    Determine the correct configuration to use and apply it.

    :param flask.Flask app: a Flask application object
    """
    config_file = None
    if os.getenv('CACHITO_DEV', '').lower() == 'true':
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
    # Initialize Flask Login
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.user_loader(user_loader)
    login_manager.request_loader(load_user_from_request)

    app.register_blueprint(docs)
    app.register_blueprint(api_v1, url_prefix='/api/v1')
    app.add_url_rule('/healthcheck', view_func=healthcheck)

    for code in default_exceptions.keys():
        app.register_error_handler(code, json_error)
    app.register_error_handler(ValidationError, json_error)
    app.register_error_handler(kombu.exceptions.KombuError, json_error)

    return app
