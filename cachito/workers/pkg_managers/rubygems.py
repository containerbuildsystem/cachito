# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import random
import re
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

import requests
from gemlock_parser.gemfile_lock import GemfileLockParser

from cachito.errors import NexusError, ValidationError
from cachito.workers import get_worker_config, nexus
from cachito.workers.errors import NexusScriptError
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers.general import (
    download_binary_file,
    download_raw_component,
    extract_git_info,
    upload_raw_package,
)
from cachito.workers.scm import Git

GIT_REF_FORMAT = re.compile(r"^[a-fA-F0-9]{40}$")
PLATFORMS_RUBY = re.compile(r"^PLATFORMS\n {2}ruby\n\n", re.MULTILINE)

log = logging.getLogger(__name__)


@dataclass
class GemMetadata:
    """Gem metadata."""

    name: str
    version: str
    type: str
    source: str


def prepare_nexus_for_rubygems_request(rubygems_repo_name, raw_repo_name):
    """
    Prepare Nexus so that Cachito can stage Rubygems content.

    :param str rubygems_repo_name: the name of the Rubygems repository for the request
    :param str raw_repo_name: the name of the raw repository for the request
    :raise NexusError: if the script execution fails
    """
    payload = {
        "rubygems_repository_name": rubygems_repo_name,
        "raw_repository_name": raw_repo_name,
    }
    script_name = "rubygems_before_content_staged"
    try:
        nexus.execute_script(script_name, payload)
    except NexusScriptError:
        log.exception("Failed to execute the script %s", script_name)
        raise NexusError("Failed to prepare Nexus for Cachito to stage Rubygems content")


def parse_gemlock(source_dir, gemlock_path):
    """Parse dependencies from Gemfile.lock.

    :param Path source_dir: the full path to the project directory
    :param Path gemlock_path: the full path to Gemfile.lock
    :return: list of Gems
    """
    if not gemlock_path.is_file():
        raise ValidationError(
            f"Gemfile.lock at path {gemlock_path} does not exist or is not a regular file."
        )

    _validate_gemlock_platforms(gemlock_path)

    dependencies = []
    all_gems = GemfileLockParser(str(gemlock_path)).all_gems
    for gem in all_gems.values():
        _validate_gem_metadata(gem, source_dir, gemlock_path.parent)
        source = gem.remote if gem.type != "PATH" else gem.path
        dependencies.append(GemMetadata(gem.name, gem.version, gem.type, source))

    return dependencies


def _validate_gemlock_platforms(gemlock_path):
    """Make sure Gemfile.lock contains only one platform - ruby."""
    with open(gemlock_path) as f:
        contents = f.read()

    if not PLATFORMS_RUBY.search(contents):
        msg = "PLATFORMS section of Gemfile.lock has to contain one and only platform - ruby."
        raise ValidationError(msg)


def _validate_gem_metadata(gem, source_dir, gemlock_dir):
    """Validate parsed Gem.

    While individual gems may contain platform information, this function doesn't check it,
    because it expects the Gemfile.lock to be ruby platform specific.
    :param Gem gem: gem with information parsed from Gemfile.lock
    :param Path source_dir: the full path to the project root
    :param Path gemlock_dir: the root directory containing Gemfile.lock
    :raise: ValidationError
    """
    if gem.name is None or gem.version is None:
        raise ValidationError("Unspecified name or version of a RubyGem.")

    if gem.type == "GEM":
        if gem.remote != "https://rubygems.org/":
            raise ValidationError(
                "Cachito supports only https://rubygems.org/ as a remote for Ruby GEM dependencies."
            )
    elif gem.type == "GIT":
        if not gem.remote.startswith("https://"):
            raise ValidationError("All Ruby GIT dependencies have to use HTTPS protocol.")
        if not GIT_REF_FORMAT.match(gem.version):
            msg = (
                f"No git ref for gem: {gem.name} (expected 40 hexadecimal characters, "
                f"got: {gem.version})."
            )
            raise ValidationError(msg)
    elif gem.type == "PATH":
        _validate_path_dependency_dir(gem, source_dir, gemlock_dir)
    else:
        raise ValidationError("Gemfile.lock contains unsupported dependency type.")


def _validate_path_dependency_dir(gem, project_root, gemlock_dir):
    """Validate path of PATH dependency.

    :param gem: validated gem
    :param project_root: project root directory
    :param gemlock_dir: absolute path to Gemfile.lock parent directory
    """
    dependency_dir = gemlock_dir / Path(gem.path)
    try:
        dependency_dir = dependency_dir.resolve(strict=True)
        dependency_dir.relative_to(project_root.resolve())
    except FileNotFoundError:
        raise ValidationError(
            f"PATH dependency {str(gem.name)} references a non-existing path: "
            f"{str(dependency_dir)}."
        )
    except RuntimeError:
        raise ValidationError(
            f"Path of PATH dependency {str(gem.name)} contains an infinite loop: "
            f"{str(dependency_dir)}."
        )
    except ValueError:
        raise ValidationError(f"{str(dependency_dir)} is not a subpath of {str(project_root)}")


def finalize_nexus_for_rubygems_request(rubygems_repo_name, raw_repo_name, username):
    """
    Configure Nexus so that the request's Rubygems repositories are ready for consumption.

    :param str rubygems_repo_name: the name of the rubygems hosted repository for a given request
    :param str raw_repo_name: the name of the raw repository for the Cachito Rubygems request
    :param str username: the username of the user to be created for the Cachito Rubygems request
    :return: the password of the Nexus user that has access to the request's Rubygems repositories
    :rtype: str
    :raise NexusError: if the script execution fails
    """
    # Generate a 24-32 character (each byte is two hex characters) password
    password = secrets.token_hex(random.randint(12, 16))  # nosec
    payload = {
        "password": password,
        "rubygems_repository_name": rubygems_repo_name,
        "raw_repository_name": raw_repo_name,
        "username": username,
    }
    script_name = "rubygems_after_content_staged"
    try:
        nexus.execute_script(script_name, payload)
    except NexusScriptError:
        log.exception("Failed to execute the script %s", script_name)
        raise NexusError("Failed to configure Nexus Rubygems repositories for final consumption")
    return password


def download_dependencies(request_id, dependencies):
    """
    Download all dependencies from Gemfile.lock with its sources.

    After downloading, upload all GIT dependencies to the Nexus raw repo if they were not already
    present. Dependencies from rubygems.org get cached automatically just by being downloaded
    from the right URL, see _download_rubygems_package().

    :param int request_id: ID of the request these dependencies are being downloaded for
    :param list[GemMetadata] dependencies: List of dependencies
    :return: Info about downloaded packages; all items will contain "kind" and "path" keys
        (and more based on kind, see _download_*_package functions for more details)
    :rtype: list[dict]
    """
    bundle_dir = RequestBundleDir(request_id)
    bundle_dir.rubygems_deps_dir.mkdir(parents=True, exist_ok=True)

    config = get_worker_config()
    rubygems_proxy_url = config.cachito_nexus_rubygems_proxy_url
    rubygems_raw_repo_name = config.cachito_nexus_rubygems_raw_repo_name

    nexus_username, nexus_password = nexus.get_nexus_hoster_credentials()
    nexus_auth = requests.auth.HTTPBasicAuth(nexus_username, nexus_password)

    downloads = []

    for dep in dependencies:
        log.info("Downloading %s (%s)", dep.name, dep.version)

        if dep.type == "GEM":
            download_info = _download_rubygems_package(
                dep, bundle_dir.rubygems_deps_dir, rubygems_proxy_url, nexus_auth
            )
        elif dep.type == "GIT":
            download_info = _download_git_package(
                dep, bundle_dir.rubygems_deps_dir, rubygems_raw_repo_name, nexus_auth
            )
        else:
            # Should not happen
            raise RuntimeError(f"Unexpected dependency type: {dep.type!r}")

        log.info(
            "Successfully downloaded gem %s (%s) to %s",
            dep.name,
            dep.version,
            download_info["path"].relative_to(bundle_dir),
        )

        # If the raw component is not in the Nexus hoster instance, upload it there
        if dep.type == "GIT" and not download_info["have_raw_component"]:
            log.debug(
                "Uploading %r to %r as %r",
                download_info["path"].name,
                rubygems_raw_repo_name,
                download_info["raw_component_name"],
            )
            dest_dir, filename = download_info["raw_component_name"].rsplit("/", 1)
            upload_raw_package(
                rubygems_raw_repo_name,
                download_info["path"],
                dest_dir,
                filename,
                is_request_repository=False,
            )

        download_info["kind"] = dep.type
        downloads.append(download_info)

    return downloads


def _download_rubygems_package(gem, deps_dir, proxy_url, proxy_auth):
    """Download platform independent RubyGem.

    The platform independence is ensured by downloading it from platform independent url
    (url that doesn't have any platform suffix).
    :param GemMetadata gem: Gem dependency from a Gemfile.lock file
    :param Path deps_dir: The deps/rubygems directory in a Cachito request bundle
    :param str proxy_url: URL of Nexus RubyGems proxy
    :param requests.auth.AuthBase proxy_auth: Authorization for the RubyGems proxy
    """
    package_dir = deps_dir / gem.name
    package_dir.mkdir(exist_ok=True)
    download_path = package_dir / f"{gem.name}-{gem.version}.gem"

    proxied_url = f"{proxy_url.rstrip('/')}/gems/{gem.name}-{gem.version}.gem"
    download_binary_file(proxied_url, download_path, auth=proxy_auth)

    return {
        "package": gem.name,
        "version": gem.version,
        "path": download_path,
    }


def _download_git_package(gem, rubygems_deps_dir, rubygems_raw_repo_name, nexus_auth):
    """
    Fetch the source for a Ruby package from Git.

    If the package is already present in Nexus as a raw component, download it
    from there instead of fetching from the original location.

    :param GemMetadata gem: Git dependency from a Gemfile.lock file
    :param Path rubygems_deps_dir: The deps/rubygems directory in a Cachito request bundle
    :param str rubygems_raw_repo_name: Name of the Nexus raw repository for RubyGems
    :param requests.auth.AuthBase nexus_auth: Authorization for the Nexus raw repo

    :return: Dict with package name, download path, git url and ref, name of raw component in Nexus
        and boolean whether we already have the raw component in Nexus
    """
    git_info = extract_git_info(f"{gem.source}@{gem.version}")

    # Download to e.g. deps/rubygems/github.com/namespace/repo
    package_dir = rubygems_deps_dir.joinpath(
        git_info["host"], git_info["namespace"], git_info["repo"]
    )
    package_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{git_info['repo']}-external-gitcommit-{gem.version}.tar.gz"
    download_path = package_dir / filename
    raw_component_name = f"{git_info['repo']}/{filename}"

    # Download raw component if we already have it
    have_raw_component = download_raw_component(
        raw_component_name, rubygems_raw_repo_name, download_path, nexus_auth
    )

    if not have_raw_component:
        log.debug("Raw component not found, will fetch from git")
        repo_name = Git(gem.source, gem.version)
        repo_name.fetch_source(gitsubmodule=False)
        # Copy downloaded archive to expected download path
        shutil.copy(repo_name.sources_dir.archive_path, download_path)

    return {
        "package": gem.name,
        "path": download_path,
        "url": gem.source,
        "ref": gem.version.lower(),
        "raw_component_name": raw_component_name,
        "have_raw_component": have_raw_component,
    }
