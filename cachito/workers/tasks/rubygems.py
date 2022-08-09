from cachito.workers import nexus
from cachito.workers.pkg_managers.rubygems import (
    get_rubygems_hosted_repo_name,
    get_rubygems_nexus_username,
)
from cachito.workers.tasks.celery import app

__all__ = ["cleanup_rubygems_request"]


@app.task
def cleanup_rubygems_request(request_id):
    """Clean up the Nexus RubyGems content for the Cachito request."""
    payload = {
        "rubygems_repository_name": get_rubygems_hosted_repo_name(request_id),
        "username": get_rubygems_nexus_username(request_id),
    }
    nexus.execute_script("rubygems_cleanup", payload)
