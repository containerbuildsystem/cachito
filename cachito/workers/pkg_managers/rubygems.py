# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from gemlock_parser.gemfile_lock import GemfileLockParser

from cachito.errors import CachitoError, ValidationError
from cachito.workers import nexus
from cachito.workers.errors import NexusScriptError

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
    :raise CachitoError: if the script execution fails
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
        raise CachitoError("Failed to prepare Nexus for Cachito to stage Rubygems content")


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
