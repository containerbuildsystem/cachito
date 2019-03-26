# SPDX-License-Identifier: GPL-3.0-or-later
from flask import Flask

from cachito.web.splash import splash
from cachito.web.api_v1 import api_v1


# See app factory pattern:
#   http://flask.pocoo.org/docs/0.12/patterns/appfactories/
def create_app(config_filename=None):
    app = Flask(__name__)
    if config_filename:
        app.config.from_pyfile(config_filename)

    app.register_blueprint(splash)
    app.register_blueprint(api_v1, url_prefix='/api/v1')
    return app
