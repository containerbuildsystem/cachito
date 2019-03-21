# SPDX-License-Identifier: GPL-3.0-or-later
import pytest


from cachito.wsgi import create_app


@pytest.fixture(scope='session')
def client():
    """Return Flask application client for the pytest session."""
    return create_app().test_client()
