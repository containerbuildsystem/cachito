# SPDX-License-Identifier: GPL-3.0-or-later
import logging

from cachito.errors import CachitoError
from cachito.workers.pkg_managers import (
    resolve_gomod,
    update_request_with_deps,
    update_request_with_packages,
)
from cachito.workers.tasks.celery import app
from cachito.workers.tasks.general import set_request_state
from cachito.workers.paths import RequestBundleDir

__all__ = ["fetch_gomod_source"]
log = logging.getLogger(__name__)


@app.task
def fetch_gomod_source(request_id, auto_detect=False, dep_replacements=None):
    """
    Resolve and fetch gomod dependencies for a given request.

    :param int request_id: the Cachito request ID this is for
    :param bool auto_detect: automatically detect if the archive uses Go modules
    :param list dep_replacements: dependency replacements with the keys "name" and "version"
    """
    bundle_dir = RequestBundleDir(request_id)
    if auto_detect:
        log.debug("Checking if the application source uses Go modules")
        if not bundle_dir.go_mod_file.exists():
            log.info("The application source does not use Go modules")
            return

    log.info("Fetching gomod dependencies for request %d", request_id)
    request = set_request_state(request_id, "in_progress", "Fetching the golang dependencies")
    try:
        module, deps = resolve_gomod(str(bundle_dir.source_dir), request, dep_replacements)
    except CachitoError:
        log.exception("Failed to fetch gomod dependencies for request %d", request_id)
        raise

    env_vars = {"GOCACHE": "deps/gomod", "GOPATH": "deps/gomod"}
    update_request_with_packages(request_id, [module], "gomod", env_vars)
    update_request_with_deps(request_id, deps)
