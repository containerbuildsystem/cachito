# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import configparser
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path

import pkg_resources

from cachito.errors import ValidationError


log = logging.getLogger(__name__)

NOTHING = object()  # A None replacement for cases where the distinction is needed


def any_to_version(obj):
    """
    Convert any python object to a version string.

    https://github.com/pypa/setuptools/blob/ba209a15247b9578d565b7491f88dc1142ba29e4/setuptools/config.py#L535

    :param any obj: object to convert to version
    :rtype: str
    """
    version = obj

    if not isinstance(version, str):
        if hasattr(version, "__iter__"):
            version = ".".join(map(str, version))
        else:
            version = str(version)

    return pkg_resources.safe_version(version)


def get_top_level_attr(block_node, attr_name, before_line=None):
    """
    Get attribute from module if it is defined at top level and assigned to a literal expression.

    https://github.com/pypa/setuptools/blob/ba209a15247b9578d565b7491f88dc1142ba29e4/setuptools/config.py#L36

    Note that this approach is not equivalent to the setuptools one - setuptools looks for the
    attribute starting from the top, we start at the bottom. Arguably, starting at the bottom
    makes more sense, but it should not make any real difference in practice.

    :param ast.AST block_node: Any AST node that has a 'body' attribute
    :param str attr_name: Name of attribute to search for
    :param int before_line: Only look for attributes defined before this line

    :rtype: anything that can be expressed as a literal ("primitive" types, collections)
    :raises AttributeError: If attribute not found or not assigned to a literal expression
    """
    if before_line is None:
        before_line = float("inf")
    try:
        return next(
            ast.literal_eval(node.value)
            for node in reversed(block_node.body)
            if node.lineno < before_line and isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name) and target.id == attr_name
        )
    except ValueError:
        raise AttributeError(f"Attribute {attr_name!r} is not assigned to a literal expression")
    except StopIteration:
        raise AttributeError(f"Attribute {attr_name!r} not found")


class SetupFile(ABC):
    """Abstract base class for setup.cfg and setup.py handling."""

    def __init__(self, top_dir, file_name):
        """
        Initialize a SetupFile.

        :param str top_dir: Path to root of project directory
        :param str file_name: Name of Python setup file, expected to be in the root directory
        """
        self._top_dir = Path(top_dir).resolve()
        self._path = self._top_dir / file_name

    def exists(self):
        """Check if file exists."""
        return self._path.is_file()

    @abstractmethod
    def get_name(self):
        """Attempt to determine the package name. Should only be called if file exists."""

    @abstractmethod
    def get_version(self):
        """Attempt to determine the package version. Should only be called if file exists."""


class SetupCFG(SetupFile):
    """
    Parse metadata.name and metadata.version from a setup.cfg file.

    Aims to match setuptools behaviour as closely as possible, but does make
    some compromises (such as never executing arbitrary Python code).
    """

    # Valid Python name - any sequence of \w characters that does not start with a number
    _name_re = re.compile(r"[^\W\d]\w*")

    def __init__(self, top_dir):
        """
        Initialize a SetupCFG.

        :param str top_dir: Path to root of project directory
        """
        super().__init__(top_dir, "setup.cfg")
        self.__parsed = NOTHING

    def get_name(self):
        """
        Get metadata.name if present.

        :rtype: str or None
        """
        name = self._get_option("metadata", "name")
        if name is None:
            log.debug("No metadata.name in setup.cfg")
            return None

        log.debug("Found metadata.name in setup.cfg: %r", name)
        return name

    def get_version(self):
        """
        Get metadata.version if present.

        Partially supports the file: directive (setuptools supports multiple files
        as an argument to file:, this makes no sense for version).

        Partially supports the attr: directive (will only work if the attribute
        being referenced is assigned to a literal expression).

        :rtype: str or None
        """
        version = self._get_option("metadata", "version")
        if version is None:
            log.debug("No metadata.version in setup.cfg")
            return None

        log.debug("Resolving metadata.version in setup.cfg from %r", version)
        version = self._resolve_version(version)
        if version is None:
            log.debug("Failed to resolve metadata.version in setup.cfg")
            return None

        version = any_to_version(version)
        log.debug("Found metadata.version in setup.cfg: %r", version)
        return version

    @property
    def _parsed(self):
        """
        Try to parse config file, return None if parsing failed.

        Will not parse file (or try to) more than once.
        """
        if self.__parsed is NOTHING:  # Have not tried to parse file yet
            log.debug("Parsing setup.cfg at %r", str(self._path))
            parsed = configparser.ConfigParser()

            with self._path.open() as f:
                try:
                    parsed.read_file(f)
                    self.__parsed = parsed
                except configparser.Error as e:
                    log.debug("Failed to parse setup.cfg: %s", e)
                    self.__parsed = None  # Tried to parse file and failed

        return self.__parsed

    def _get_option(self, section, option):
        """Get option from config section, return None if option missing or file invalid."""
        if self._parsed is None:
            return None
        try:
            return self._parsed.get(section, option)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return None

    def _resolve_version(self, version):
        """Attempt to resolve the version attribute."""
        if version.startswith("file:"):
            file_arg = version[len("file:") :].strip()
            version = self._read_version_from_file(file_arg)
        elif version.startswith("attr:"):
            attr_arg = version[len("attr:") :].strip()
            version = self._read_version_from_attr(attr_arg)
        return version

    def _read_version_from_file(self, file_path):
        """Read version from file after making sure file is a subpath of project dir."""
        full_file_path = self._ensure_local(file_path)
        if full_file_path.is_file():
            version = full_file_path.read_text().strip()
            log.debug("Read version from %r: %r", file_path, version)
            return version
        else:
            log.debug("Version file %r does not exist or is not a file", file_path)
            return None

    def _ensure_local(self, path):
        """Check that path is a subpath of project directory, return resolved path."""
        full_path = (self._top_dir / path).resolve()
        try:
            full_path.relative_to(self._top_dir)
        except ValueError:
            raise ValidationError(f"{str(path)!r} is not a subpath of {str(self._top_dir)!r}")
        return full_path

    def _read_version_from_attr(self, attr_spec):
        """
        Read version from module attribute.

        Like setuptools, will try to find the attribute by looking for Python
        literals in the AST of the module. Unlike setuptools, will not execute
        the module if this fails.

        https://github.com/pypa/setuptools/blob/ba209a15247b9578d565b7491f88dc1142ba29e4/setuptools/config.py#L354

        :param str attr_spec: "import path" of attribute, e.g. package.version.__version__
        :rtype: str or None
        """
        module_name, _, attr_name = attr_spec.rpartition(".")
        if not module_name:
            # Assume current directory is a package, look for attribute in __init__.py
            module_name = "__init__"

        log.debug("Attempting to find attribute %r in %r", attr_name, module_name)

        module_file = self._find_module(module_name, self._get_package_dirs())
        if module_file is not None:
            log.debug("Found module %r at %r", module_name, str(module_file))
        else:
            log.debug("Module %r not found", module_name)
            return None

        try:
            module_ast = ast.parse(module_file.read_text(), module_file.name)
        except SyntaxError as e:
            log.debug("Syntax error when parsing module: %s", e)
            return None

        try:
            version = get_top_level_attr(module_ast, attr_name)
            log.debug("Found attribute %r in %r: %r", attr_name, module_name, version)
            return version
        except AttributeError as e:
            log.debug("Could not find attribute in %r: %s", module_name, e)
            return None

    def _find_module(self, module_name, package_dir=None):
        """
        Try to find a module in the project directory and return path to source file.

        :param str module_name: "import path" of module
        :param dict[str, str] package_dir: same semantics as options.package_dir in setup.cfg

        :rtype: Path or None
        """
        module_path = self._convert_to_path(module_name)
        root_module = module_path.parts[0]

        package_dir = package_dir or {}

        if root_module in package_dir:
            custom_path = Path(package_dir[root_module])
            log.debug(f"Custom path set for root module %r: %r", root_module, str(custom_path))
            # Custom path replaces the root module
            module_path = custom_path.joinpath(*module_path.parts[1:])
        elif "" in package_dir:
            custom_path = Path(package_dir[""])
            log.debug(f"Custom path set for all root modules: %r", str(custom_path))
            # Custom path does not replace the root module
            module_path = custom_path / module_path

        full_module_path = self._ensure_local(module_path)

        package_init = full_module_path / "__init__.py"
        if package_init.is_file():
            return package_init

        module_py = Path(f"{full_module_path}.py")
        if module_py.is_file():
            return module_py

        return None

    def _convert_to_path(self, module_name):
        """Check that module name is valid and covert to file path."""
        parts = module_name.split(".")
        if not parts[0]:
            # Relative import (supported only to the extent that one leading '.' is ignored)
            parts.pop(0)
        if not all(self._name_re.fullmatch(part) for part in parts):
            raise ValidationError(f"{module_name!r} is not an accepted module name")
        return Path(*parts)

    def _get_package_dirs(self):
        """
        Get options.package_dir and convert to dict if present.

        https://github.com/pypa/setuptools/blob/ba209a15247b9578d565b7491f88dc1142ba29e4/setuptools/config.py#L264

        :rtype: dict[str, str] or None
        """
        package_dir_value = self._get_option("options", "package_dir")
        if package_dir_value is None:
            return None

        if "\n" in package_dir_value:
            package_items = package_dir_value.splitlines()
        else:
            package_items = package_dir_value.split(",")

        # Strip whitespace and discard empty values
        package_items = filter(bool, (p.strip() for p in package_items))

        package_dirs = {}
        for item in package_items:
            package, sep, p_dir = item.partition("=")
            if sep:
                # Otherwise value was malformed ('=' was missing)
                package_dirs[package.strip()] = p_dir.strip()

        return package_dirs
