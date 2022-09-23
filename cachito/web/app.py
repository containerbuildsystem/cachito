# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
from pathlib import Path
from timeit import default_timer as timer

import connexion
import pydantic
from flask import current_app
from flask.logging import default_handler
from flask_login import LoginManager
from flask_migrate import Migrate
from opentelemetry import trace
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import InternalServerError, default_exceptions

from cachito.errors import (
    CachitoError,
    ClientError,
    ContentManifestError,
    ServerError,
    ValidationError,
)
from cachito.web import db
from cachito.web.auth import load_user_from_request, user_loader
from cachito.web.config import validate_cachito_config
from cachito.web.docs import docs
from cachito.web.errors import json_error, validation_error
from cachito.web.metrics import init_metrics
from cachito.web.validation import ParameterValidator, RequestBodyValidator


def healthcheck():
    """
    Perform an application-level health check.

    This is not part of the published API because it is intended to be used by monitoring tools.
    This returns a 200 response if the application is alive and able to serve requests. It returns
    a 500 response otherwise.
    """
    current_app.logger.info("A healthcheck request was received")

    try:
        start_time = timer()

        db.session.execute("SELECT 1 FROM request LIMIT 0").fetchall()

        end_time = timer() - start_time
        current_app.logger.info("The healthcheck database query took %f seconds", end_time)
    except SQLAlchemyError:
        current_app.logger.exception("The healthcheck failed when querying the database")
        raise InternalServerError()

    return ("OK", 200, [("Content-Type", "text/plain")])


def load_config(app):
    """
    Determine the correct configuration to use and apply it.

    :param flask.Flask app: a Flask application object
    """
    config_file = None
    if os.getenv("CACHITO_DEV", "").lower() == "true":
        default_config_obj = "cachito.web.config.DevelopmentConfig"
    else:
        default_config_obj = "cachito.web.config.ProductionConfig"
        config_file = "/etc/cachito/settings.py"
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
    connexion_app = connexion.FlaskApp(__name__, options={"swagger_ui": False})
    app = connexion_app.app

    if config_obj:
        app.config.from_object(config_obj)
    else:
        load_config(app)

    # Configure logging
    default_handler.setFormatter(
        logging.Formatter(fmt=app.config["CACHITO_LOG_FORMAT"], datefmt="%Y-%m-%d %H:%M:%S")
    )
    app.logger.setLevel(app.config["CACHITO_LOG_LEVEL"])
    for logger_name in app.config["CACHITO_ADDITIONAL_LOGGERS"]:
        logger = logging.getLogger(logger_name)
        logger.setLevel(app.config["CACHITO_LOG_LEVEL"])
        # Add the Flask handler that streams to WSGI stderr
        logger.addHandler(default_handler)

    # Initialize the database
    db.init_app(app)
    # Initialize the database migrations
    migrations_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), "migrations")
    Migrate(app, db, directory=migrations_dir)
    # Initialize Flask Login
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.user_loader(user_loader)
    login_manager.request_loader(load_user_from_request)

    app.register_blueprint(docs)

    path = Path(__file__).parent.absolute()
    connexion_app.add_api(
        f"{path}/static/api_v1.yaml",
        strict_validation=True,
        validator_map={"body": RequestBodyValidator, "parameter": ParameterValidator},
    )

    app.add_url_rule("/healthcheck", view_func=healthcheck)

    for code in default_exceptions.keys():
        app.register_error_handler(code, json_error)
    app.register_error_handler(CachitoError, json_error)
    app.register_error_handler(ClientError, json_error)
    app.register_error_handler(ServerError, json_error)
    app.register_error_handler(ValidationError, json_error)
    app.register_error_handler(ContentManifestError, json_error)
    app.register_error_handler(pydantic.ValidationError, validation_error)

    init_metrics(app)
    _instrument_app(app)
    return app


def _instrument_app(app):
    """
    Instrument the Flask app.

    Sets up the OpenTelemetry tracing exporter, configures the endpoint
    to send trace data to.
    """
    # Some of the following has already been executed due to the manner in which
    # the tasks config is included....

    service_name = "cachito-api"
    resource = Resource(attributes={SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    # Used for local development environment aka docker-compose up.
    if "CACHITO_JAEGER_EXPORTER_ENDPOINT" in app.config.keys():
        app.logger.info("Configuring Jaeger Exporter")
        jaeger_exporter = JaegerExporter(
            agent_host_name=app.config["CACHITO_JAEGER_EXPORTER_ENDPOINT"],
            agent_port=int(app.config["CACHITO_JAEGER_EXPORTER_PORT"]),
        )
        processor = BatchSpanProcessor(jaeger_exporter)
    # test/stage/prod environments....
    elif "CACHITO_OTLP_EXPORTER_ENDPOINT" in app.config.keys():
        app.logger.info(
            "Configuring OTLP Exporter: " + str(app.config["CACHITO_OTLP_EXPORTER_ENDPOINT"])
        )
        otlp_exporter = OTLPSpanExporter(endpoint=app.config["CACHITO_OTLP_EXPORTER_ENDPOINT"])
        processor = BatchSpanProcessor(otlp_exporter)
    # Undefined; send data to the console.
    else:
        app.logger.info("Configuring ConsoleSpanExporter")
        processor = BatchSpanProcessor(ConsoleSpanExporter())

    # Toggle between sending to jaeger and displaying span info on console
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

    FlaskInstrumentor().instrument_app(
        app, excluded_urls="/static/*,/favicon.ico,/metrics,/healthcheck"
    )
    RequestsInstrumentor().instrument()
    CeleryInstrumentor().instrument()
    SQLAlchemyInstrumentor().instrument(
        enable_commenter=True,
        commenter_options={
            "db_driver": True,
        },
    )
    Psycopg2Instrumentor().instrument(enable_commenter=True, commenter_options={})


def create_cli_app():
    """
    Create a Flask application instance and validate the configuration for the Flask CLI.

    :return: a Flask application object
    :rtype: flask.Flask
    """
    app = create_app()
    validate_cachito_config(app.config, cli=True)
    return app
