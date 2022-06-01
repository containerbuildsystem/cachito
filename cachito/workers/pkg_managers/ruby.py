from pathlib import Path

import packagedcode.gemfile_lock as gemlock_parser

from cachito.errors import ValidationError


class GemMetadata:
    """Gem metadata."""

    def __init__(self, name, version, gem_type, path):
        """Fill GemMetadata with data."""
        self.name = name
        self.version = version
        self.type = gem_type
        self.path = path

    def __eq__(self, other):
        if isinstance(other, GemMetadata):
            return (
                self.name == other.name
                and self.version == other.version
                and self.type == other.type
                and self.path == other.path
            )
        return False


def parse_gemlock(gemlock_path: Path):
    """Parse dependencies from Gemfile.lock.

    :param Path gemlock_path: the full path to Gemfile.lock
    :return: list of Gems
    """
    dt = gemlock_parser.GemfileLockParser(str(gemlock_path)).dependency_tree
    dependencies = []

    for key in dt:
        gem = dt[key]
        validate_gem_metadata(gem, gemlock_path.parents[0])
        dependencies.append(GemMetadata(gem.name, gem.version, gem.type, gem.remote))

    return dependencies


def validate_gem_metadata(gem, gemlock_dir):
    """Validate parsed Gem.

    :param Gem gem: gem with information parsed from Gemfile.lock
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
    elif gem.type == "PATH":
        dependency_dir = gemlock_dir / gem.path
        if not dependency_dir.exists():
            raise ValidationError("PATH dependency references a non-existing path.")
    else:
        raise ValidationError("Gemfile.lock contains unsupported dependency type.")
