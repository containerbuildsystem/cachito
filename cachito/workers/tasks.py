# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
import tarfile

from celery import Celery
from celery.signals import celeryd_init
from requests import Timeout

from cachito.workers.config import configure_celery, validate_celery_config, get_worker_config
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
def fetch_gomod_source(app_archive_path, copy_cache_to=None):
    """
    Resolve and fetch gomod dependencies for given app source archive.

    :param str app_archive_path: the full path to the application source code
    :param str copy_cache_to: path to copy artifacts from gomod cache
    """
    log.info('Fetching gomod dependencies for "%s"', app_archive_path)
    try:
        resolve_gomod_deps(app_archive_path, copy_cache_to)
    except CachitoError:
        # TODO: Post a failure status back to the API. This could also be converted to a decorator.
        log.exception('Failed to fetch gomod dependencies for "%s"', app_archive_path)
        raise
    # TODO: Store list of dependencies in DB via the API.
    return app_archive_path


@app.task
def assemble_source_code_archive(app_archive_path, deps_path, bundle_archive_path):
    """
    Creates an archive with the source code for application and its dependencies.

    :param str app_archive_path: the path to the archive of the application source code
    :param str deps_path: the path to the directory containing the dependencies source code
    :param str bundle_archive_path: the destination path of the assembled archive
    """
    log.info('Assembling source code archive in "%s"', bundle_archive_path)
    cachito_shared_dir = get_worker_config().cachito_shared_dir
    absolute_app_archive_path = os.path.join(cachito_shared_dir, app_archive_path)
    absolute_deps_path = os.path.join(cachito_shared_dir, deps_path)
    bundle_archive_path = os.path.join(cachito_shared_dir, bundle_archive_path)

    # Generate a tarball containing the application and dependencies source code
    with tarfile.open(bundle_archive_path, mode='w:gz') as bundle_archive:
        with tarfile.open(absolute_app_archive_path, mode='r:*') as app_archive:
            for member in app_archive.getmembers():
                bundle_archive.addfile(member, app_archive.extractfile(member.name))

        bundle_archive.add(absolute_deps_path, 'deps')
