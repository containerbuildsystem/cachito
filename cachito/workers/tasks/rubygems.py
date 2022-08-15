# SPDX-License-Identifier: GPL-3.0-or-later
from os.path import relpath
from pathlib import Path
from textwrap import dedent

from cachito.errors import CachitoError
from cachito.workers import nexus
from cachito.workers.pkg_managers.rubygems import (
    get_rubygems_hosted_repo_name,
    get_rubygems_nexus_username,
)
from cachito.workers.tasks.celery import app
from cachito.workers.tasks.utils import make_base64_config_file

__all__ = ["cleanup_rubygems_request"]


@app.task
def cleanup_rubygems_request(request_id):
    """Clean up the Nexus RubyGems content for the Cachito request."""
    payload = {
        "rubygems_repository_name": get_rubygems_hosted_repo_name(request_id),
        "username": get_rubygems_nexus_username(request_id),
    }
    nexus.execute_script("rubygems_cleanup", payload)


def _get_config_file_for_given_package(
    dependencies, bundle_dir, package_source_dir, rubygems_hosted_url
):
    """
    Get Bundler config file.

    Returns a Bundler config file with a mirror set for RubyGems dependencies pointing to
    `rubygems_hosted_repo` URL. All GIT dependencies are configured to be replaced by local git
     repos.

    :param dependencies: an array of dependencies (dictionaries) with keys
        "name": package name,
        "path": an absolute path to a locally downloaded git repo,
        "kind": dependency kind
    :param bundle_dir: an absolute path to the root of the Cachito bundle
    :param package_source_dir: a path to the root directory of given package
    :param rubygems_hosted_url: URL pointing to a request specific RubyGems hosted repo with
     hardcoded user credentials
    :return: dict with "content", "path" and "type" keys
    """
    base_config = dedent(
        f"""
        # Sets mirror for all RubyGems sources
        BUNDLE_MIRROR__ALL: "{rubygems_hosted_url}"
        # Turn off the probing
        BUNDLE_MIRROR__ALL__FALLBACK_TIMEOUT: "false"
        # Install only ruby platform gems (=> gems with native extensions are compiled from source).
        # All gems should be platform independent already, so why not keep it here.
        BUNDLE_FORCE_RUBY_PLATFORM: "true"
        BUNDLE_DEPLOYMENT: "true"
        # Defaults to true when deployment is set to true
        BUNDLE_FROZEN: "true"
        # For local Git replacements, branches don't have to be specified (commit hash is enough)
        BUNDLE_DISABLE_LOCAL_BRANCH_CHECK: "true"
    """
    )

    config = [base_config]
    for dependency in dependencies:
        if dependency["kind"] == "GIT":
            # These substitutions are required by Bundler
            name = dependency["name"].upper().replace("-", "___").replace(".", "__")
            relative_path = relpath(dependency["path"], package_source_dir)
            dep_replacement = f'BUNDLE_LOCAL__{name}: "{relative_path + "/app"}"'
            config.append(dep_replacement)

    final_config = "\n".join(config)

    config_file_path = package_source_dir / Path(".bundle/config")
    if config_file_path.exists():
        raise CachitoError(
            f"Cachito wants to create a config file at location {config_file_path}, "
            f"but it already exists."
        )
    final_path = config_file_path.relative_to(Path(bundle_dir))
    return make_base64_config_file(final_config, final_path)
