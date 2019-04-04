# SPDX-License-Identifier: GPL-3.0-or-later
import os

import pytest
import flask_migrate

from cachito.web import db as _db
from cachito.web.config import TEST_DB_FILE
from cachito.web.wsgi import create_app


@pytest.fixture(scope='session')
def app():
    """Return Flask application for the pytest session."""
    return create_app('cachito.web.config.TestingConfig')


@pytest.fixture(scope='session')
def client(app):
    """Return Flask application client for the pytest session."""
    return app.test_client()


@pytest.fixture()
def db(app, tmpdir):
    """Yields a DB with required app tables but with no records."""
    # Clear the database for each test to ensure tests are idempotent.
    try:
        os.remove(TEST_DB_FILE)
    except FileNotFoundError:
        pass

    with app.app_context():
        flask_migrate.upgrade()

    return _db
