# SPDX-License-Identifier: GPL-3.0-or-later
import pytest


from cachito.web.wsgi import create_app


@pytest.fixture(scope='session')
def client():
    """Return Flask application client for the pytest session."""
    return create_app('cachito.web.config.TestingConfig').test_client()
