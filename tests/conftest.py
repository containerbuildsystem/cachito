# SPDX-License-Identifier: GPL-3.0-or-later
import os

import pytest
import flask_migrate

from cachito.web import db as _db
from cachito.web.config import TEST_DB_FILE
from cachito.web.wsgi import create_app


@pytest.fixture(scope='session')
def app(request):
    """Return Flask application for the pytest session."""
    app = create_app('cachito.web.config.TestingConfig')
    # Establish an application context before running the tests. This allows the use of
    # Flask-SQLAlchemy in the test setup.
    ctx = app.app_context()
    ctx.push()

    def teardown():
        ctx.pop()

    request.addfinalizer(teardown)
    return app


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
