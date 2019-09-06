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
def auth_env():
    return {'REMOTE_USER': 'tbrady@DOMAIN.LOCAL'}


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


@pytest.fixture()
def sample_deps():
    return [
        {'type': 'gomod', 'name': 'github.com/Masterminds/semver', 'version': 'v1.4.2'},
        {'type': 'gomod', 'name': 'github.com/kr/pretty', 'version': 'v0.1.0'},
        {'type': 'gomod', 'name': 'github.com/kr/pty', 'version': 'v1.1.1'},
        {'type': 'gomod', 'name': 'github.com/kr/text', 'version': 'v0.1.0'},
        {'type': 'gomod', 'name': 'github.com/op/go-logging',
         'version': 'v0.0.0-20160315200505-970db520ece7'},
        {'type': 'gomod', 'name': 'github.com/pkg/errors', 'version': 'v0.8.1'},
        {'type': 'gomod', 'name': 'golang.org/x/crypto',
         'version': 'v0.0.0-20190308221718-c2843e01d9a2'},
        {'type': 'gomod', 'name': 'golang.org/x/net',
         'version': 'v0.0.0-20190311183353-d8887717615a'},
        {'type': 'gomod', 'name': 'golang.org/x/sys',
         'version': 'v0.0.0-20190215142949-d0b11bdaac8a'},
        {'type': 'gomod', 'name': 'golang.org/x/text', 'version': 'v0.3.0'},
        {'type': 'gomod', 'name': 'golang.org/x/tools',
         'version': 'v0.0.0-20190325161752-5a8dccf5b48a'},
        {'type': 'gomod', 'name': 'gopkg.in/check.v1',
         'version': 'v1.0.0-20180628173108-788fd7840127'},
        {'type': 'gomod', 'name': 'gopkg.in/yaml.v2', 'version': 'v2.2.2'},
    ]


@pytest.fixture()
def sample_env_vars():
    sample = {}
    sample['GOPATH'] = sample['GOCACHE'] = 'deps/gomod'
    return sample


@pytest.fixture(scope='session')
def worker_auth_env():
    return {'REMOTE_USER': 'worker@DOMAIN.LOCAL'}
