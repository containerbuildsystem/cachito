# SPDX-License-Identifier: GPL-3.0-or-later
from cachito.workers import nexus
from cachito.workers.pkg_managers.pip import (
    get_pypi_hosted_repo_name,
    get_raw_hosted_repo_name,
    get_hosted_repositories_username,
)
from cachito.workers.tasks.celery import app


@app.task
def cleanup_pip_request(request_id):
    """Clean up the Nexus Python content for the Cachito request."""
    payload = {
        "pip_repository_name": get_pypi_hosted_repo_name(request_id),
        "raw_repository_name": get_raw_hosted_repo_name(request_id),
        "username": get_hosted_repositories_username(request_id),
    }
    nexus.execute_script("pip_cleanup", payload)
