# SPDX-License-Identifier: GPL-3.0-or-later
import logging

from celery import Celery
from celery.signals import celeryd_init
from requests import Timeout

from cachito.workers.config import configure_celery, validate_celery_config
from cachito.workers.pkg_manager import resolve_gomod_deps
from cachito.workers.scm import Git
from cachito.errors import CachitoError


log = logging.getLogger(__name__)
logging.basicConfig()
app = Celery()
configure_celery(app)
celeryd_init.connect(validate_celery_config)


@app.task
def add(x, y):
    """Add two numbers together to prove Celery works"""
    return x + y


@app.task
def fetch_app_source(url, ref):
    """
    Fetch the application source code that was requested and put it in long-term storage.

    :param str url: the source control URL to pull the source from
    :param str ref: the source control reference
    """
    log.info('Fetching the source from "%s" at reference "%s"', url, ref)
    try:
        # Default to Git for now
        scm = Git(url, ref)
        scm.fetch_source()
    except Timeout:
        raise CachitoError('The connection timed out while downloading the source')
    except CachitoError:
        # TODO: Post a failure status back to the API. This could also be converted to a decorator.
        log.exception('Failed to fetch the source from the URL "%s" and reference "%s"', url, ref)
        raise
    return scm.archive_path


@app.task
def fetch_gomod_source(archive_path):
    """
    Resolve and fetch gomod dependencies for given app source archive.

    :param str archive_path: the full path to the application source code
    """
    log.info('Fetching gomod dependencies for "%s"', archive_path)
    try:
        deps = resolve_gomod_deps(archive_path)
    except CachitoError:
        # TODO: Post a failure status back to the API. This could also be converted to a decorator.
        log.exception('Failed to fetch gomod dependencies for "%s"', archive_path)
        raise
    # TODO: Store list of dependencies in DB via the API.
    return deps
