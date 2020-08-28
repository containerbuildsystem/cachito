# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import configparser
import logging
import random
import re
import secrets
import shutil
import urllib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import pkg_resources
import requests

from cachito.errors import CachitoError, ValidationError
from cachito.workers import nexus
from cachito.workers.config import get_worker_config
from cachito.workers.errors import NexusScriptError
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers.general import (
    ChecksumInfo,
    verify_checksum,
    download_binary_file,
)
from cachito.workers.requests import requests_session
from cachito.workers.scm import Git


log = logging.getLogger(__name__)

NOTHING = object()  # A None replacement for cases where the distinction is needed

# Check that the path component of a URL ends with a full-length git ref
GIT_REF_IN_PATH = re.compile(r"@[a-fA-F0-9]{40}$")


def get_pip_metadata(package_dir):
    """
    Attempt to get the name and and version of a Pip package.

    First, try to parse the setup.py script (if present) and extract name and version
    from keyword arguments to the setuptools.setup() call. If either name or version
    could not be resolved and there is a setup.cfg file, try to fill in the missing
    values from metadata.name and metadata.version in the .cfg file.

    If either name or version could not be resolved, raise an error.

    :param str package_dir: Path to the root directory of a Pip package
    :return: Tuple of strings (name, version)
    :raises CachitoError: If either name or version could not be resolved
    """
    name = None
    version = None

    setup_py = SetupPY(package_dir)
    setup_cfg = SetupCFG(package_dir)

    if setup_py.exists():
        log.info("Extracting metadata from setup.py")
        name = setup_py.get_name()
        version = setup_py.get_version()
    else:
        log.warning("No setup.py in directory, package is likely not Pip compatible")

    if not (name and version) and setup_cfg.exists():
        log.info("Filling in missing metadata from setup.cfg")
        name = name or setup_cfg.get_name()
        version = version or setup_cfg.get_version()

    missing = []

    if name:
        log.info("Resolved package name: %r", name)
    else:
        log.error("Could not resolve package name")
        missing.append("name")

    if version:
        log.info("Resolved package version: %r", version)
    else:
        log.error("Could not resolve package version")
        missing.append("version")

    if missing:
        raise CachitoError(f"Could not resolve package metadata: {', '.join(missing)}")

    return name, version


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


def get_top_level_attr(body, attr_name, before_line=None):
    """
    Get attribute from module if it is defined at top level and assigned to a literal expression.

    https://github.com/pypa/setuptools/blob/ba209a15247b9578d565b7491f88dc1142ba29e4/setuptools/config.py#L36

    Note that this approach is not equivalent to the setuptools one - setuptools looks for the
    attribute starting from the top, we start at the bottom. Arguably, starting at the bottom
    makes more sense, but it should not make any real difference in practice.

    :param list[ast.AST] body: The body of an AST node
    :param str attr_name: Name of attribute to search for
    :param int before_line: Only look for attributes defined before this line

    :rtype: anything that can be expressed as a literal ("primitive" types, collections)
    :raises AttributeError: If attribute not found
    :raises ValueError: If attribute assigned to something that is not a literal
    """
    if before_line is None:
        before_line = float("inf")
    try:
        return next(
            ast.literal_eval(node.value)
            for node in reversed(body)
            if node.lineno < before_line and isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name) and target.id == attr_name
        )
    except ValueError:
        raise ValueError(f"{attr_name!r} is not assigned to a literal expression")
    except StopIteration:
        raise AttributeError(f"{attr_name!r} not found")


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
        if not name:
            log.info("No metadata.name in setup.cfg")
            return None

        log.info("Found metadata.name in setup.cfg: %r", name)
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
        if not version:
            log.info("No metadata.version in setup.cfg")
            return None

        log.debug("Resolving metadata.version in setup.cfg from %r", version)
        version = self._resolve_version(version)
        if not version:
            # Falsy values also count as "failed to resolve" (0, None, "", ...)
            log.info("Failed to resolve metadata.version in setup.cfg")
            return None

        version = any_to_version(version)
        log.info("Found metadata.version in setup.cfg: %r", version)
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
                    log.error("Failed to parse setup.cfg: %s", e)
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
            log.error("Version file %r does not exist or is not a file", file_path)
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
            log.error("Module %r not found", module_name)
            return None

        try:
            module_ast = ast.parse(module_file.read_text(), module_file.name)
        except SyntaxError as e:
            log.error("Syntax error when parsing module: %s", e)
            return None

        try:
            version = get_top_level_attr(module_ast.body, attr_name)
            log.debug("Found attribute %r in %r: %r", attr_name, module_name, version)
            return version
        except (AttributeError, ValueError) as e:
            log.error("Could not find attribute in %r: %s", module_name, e)
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


@dataclass(frozen=True)
class ASTpathelem:
    """An element of AST path."""

    node: ast.AST
    attr: str  # Child node is (in) this field
    index: int = None  # If field is a list, this is the index of the child node

    @property
    def field(self):
        """Return field referenced by self.attr."""
        return getattr(self.node, self.attr)

    def field_is_body(self):
        r"""
        Check if the field is a body (a list of statement nodes).

        All 'stmt*' attributes here: https://docs.python.org/3/library/ast.html#abstract-grammar

        Check with the following command:

            curl 'https://docs.python.org/3/library/ast.html#abstract-grammar' |
            grep -E 'stmt\* \w+' --only-matching |
            sort -u
        """
        return self.attr in ("body", "orelse", "finalbody")

    def __str__(self):
        """Make string representation of path element: <type>(<lineno>).<field>[<index>]."""
        s = self.node.__class__.__name__
        if hasattr(self.node, "lineno"):
            s += f"(#{self.node.lineno})"
        s += f".{self.attr}"
        if self.index is not None:
            s += f"[{self.index}]"
        return s


@dataclass(frozen=True)
class SetupBranch:
    """Setup call node, path to setup call from root node."""

    call_node: ast.AST
    node_path: list  # of ASTpathelems


class SetupPY(SetupFile):
    """
    Find the setup() call in a setup.py file and extract the `name` and `version` kwargs.

    Will only work for very basic use cases - value of keyword argument must be a literal
    expression or a variable assigned to a literal expression.

    Some supported examples:

    1) trivial

        from setuptools import setup

        setup(name="foo", version="1.0.0")

    2) if __main__

        import setuptools

        name = "foo"
        version = "1.0.0"

        if __name__ == "__main__":
            setuptools.setup(name=name, version=version)

    3) my_setup()

        import setuptools

        def my_setup():
            name = "foo"
            version = "1.0.0"

            setuptools.setup(name=name, version=version)

        my_setup()

    For examples 2) and 3), we do not actually resolve any conditions or check that the
    function containing the setup() call is eventually executed. We simply assume that,
    this being the setup.py script, setup() will end up being called no matter what.
    """

    def __init__(self, top_dir):
        """
        Initialize a SetupPY.

        :param str top_dir: Path to root of project directory
        """
        super().__init__(top_dir, "setup.py")
        self.__ast = NOTHING
        self.__setup_branch = NOTHING

    def get_name(self):
        """
        Attempt to extract package name from setup.py.

        :rtype: str or None
        """
        name = self._get_setup_kwarg("name")
        if not name or not isinstance(name, str):
            log.info(
                "Name in setup.py was either not found, or failed to resolve to a valid string"
            )
            return None

        log.info("Found name in setup.py: %r", name)
        return name

    def get_version(self):
        """
        Attempt to extract package version from setup.py.

        As of setuptools version 49.2.1, there is no special logic for passing
        an iterable as version in setup.py. Unlike name, however, it does support
        non-string arguments (except tuples with len() != 1, those break horribly).

        https://github.com/pypa/setuptools/blob/5e60dc50e540a942aeb558aabe7d92ab7eb13d4b/setuptools/dist.py#L462

        Rather than trying to keep edge cases consistent with setuptools, treat them
        consistently within Cachito.

        :rtype: str or None
        """
        version = self._get_setup_kwarg("version")
        if not version:
            # Only truthy values are valid, not any of (0, None, "", ...)
            log.info(
                "Version in setup.py was either not found, or failed to resolve to a valid value"
            )
            return None

        version = any_to_version(version)
        log.info("Found version in setup.py: %r", version)
        return version

    @property
    def _ast(self):
        """
        Try to parse AST if not already parsed.

        Will not parse file (or try to) more than once.
        """
        if self.__ast is NOTHING:
            log.debug("Parsing setup.py at %r", str(self._path))

            try:
                self.__ast = ast.parse(self._path.read_text(), self._path.name)
            except SyntaxError as e:
                log.error("Syntax error when parsing setup.py: %s", e)
                self.__ast = None

        return self.__ast

    @property
    def _setup_branch(self):
        """
        Find setup() call anywhere in the file, return setup branch.

        The file is expected to contain only one setup call. If there are two or more,
        we cannot safely determine which one gets called. In such a case, we will simply
        find and process the first one.

        If setup call not found, return None. Will not search more than once.
        """
        if self._ast is None:
            return None

        if self.__setup_branch is NOTHING:
            setup_call, setup_path = self._find_setup_call(self._ast)

            if setup_call is None:
                log.error("File does not seem to have a setup call")
                self.__setup_branch = None
            else:
                setup_path.reverse()  # Path is in reverse order
                log.debug("Found setup call on line %s", setup_call.lineno)
                path_repr = " -> ".join(map(str, setup_path))
                log.debug("Pseudo-path: %s", path_repr)
                self.__setup_branch = SetupBranch(setup_call, setup_path)

        return self.__setup_branch

    def _find_setup_call(self, root_node):
        """
        Find setup() or setuptools.setup() call anywhere in or under root_node.

        Return call node and path from root node to call node (reversed).
        """
        if self._is_setup_call(root_node):
            return root_node, []

        for name, field in ast.iter_fields(root_node):
            # Field is a node
            if isinstance(field, ast.AST):
                setup_call, setup_path = self._find_setup_call(field)
                if setup_call is not None:
                    setup_path.append(ASTpathelem(root_node, name))
                    return setup_call, setup_path
            # Field is a list of nodes (use any(), nodes will never be mixed with non-nodes)
            elif isinstance(field, list) and any(isinstance(x, ast.AST) for x in field):
                for i, node in enumerate(field):
                    setup_call, setup_path = self._find_setup_call(node)
                    if setup_call is not None:
                        setup_path.append(ASTpathelem(root_node, name, i))
                        return setup_call, setup_path

        return None, []  # No setup call under root_node

    def _is_setup_call(self, node):
        """Check if node is setup() or setuptools.setup() call."""
        if not isinstance(node, ast.Call):
            return False

        fn = node.func
        return (isinstance(fn, ast.Name) and fn.id == "setup") or (
            isinstance(fn, ast.Attribute)
            and fn.attr == "setup"
            and isinstance(fn.value, ast.Name)
            and fn.value.id == "setuptools"
        )

    def _get_setup_kwarg(self, arg_name):
        """
        Find setup() call, extract specified argument from keyword arguments.

        If argument value is a variable, then what we do is only a very loose approximation
        of how Python resolves variables. None of the following examples will work:

        1) any indented blocks (unless setup() call appears under the same block)

            with x:
                name = "foo"

            setup(name=name)

        2) late binding

            def my_setup():
                setup(name=name)

            name = "foo"

            my_setup()

        The rationale for not supporting these cases:
        - it is difficult
        - there is no use case for 1) which is both valid and possible to resolve safely
        - 2) seems like a bad enough practice to justify ignoring it
        """
        if self._setup_branch is None:
            return None

        for kw in self._setup_branch.call_node.keywords:
            if kw.arg == arg_name:
                try:
                    value = ast.literal_eval(kw.value)
                    log.debug("setup kwarg %r is a literal: %r", arg_name, value)
                    return value
                except ValueError:
                    pass

                if isinstance(kw.value, ast.Name):
                    log.debug("setup kwarg %r looks like a variable", arg_name)
                    return self._get_variable(kw.value.id)

                expr_type = kw.value.__class__.__name__
                log.error("setup kwarg %r is an unsupported expression: %s", arg_name, expr_type)
                return None

        log.debug("setup kwarg %r not found", arg_name)
        return None

    def _get_variable(self, var_name):
        """Walk back up the AST along setup branch, look for first assignment of variable."""
        lineno = self._setup_branch.call_node.lineno
        node_path = self._setup_branch.node_path

        log.debug("Backtracking up the AST from line %s to find variable %r", lineno, var_name)

        for elem in filter(ASTpathelem.field_is_body, reversed(node_path)):
            try:
                value = get_top_level_attr(elem.field, var_name, lineno)
                log.debug("Found variable %r: %r", var_name, value)
                return value
            except ValueError as e:
                log.error("Variable cannot be resolved: %s", e)
                return None
            except AttributeError:
                pass

        log.error("Variable %r not found along the setup call branch", var_name)
        return None


class PipRequirementsFile:
    """Parse requirements from a pip requirements file."""

    # Comment lines start with optional leading spaces followed by "#"
    LINE_COMMENT = re.compile(r"(^|\s)#.*$")

    # Options allowed in a requirements file. The values represent whether or not the option
    # requires a value.
    # https://pip.pypa.io/en/stable/reference/pip_install/#requirements-file-format
    OPTIONS = {
        "--constraint": True,
        "--editable": False,  # The required value is the requirement itself, not a parameter
        "--extra-index-url": True,
        "--find-links": True,
        "--index-url": True,
        "--no-binary": True,
        "--no-index": False,
        "--only-binary": True,
        "--pre": False,
        "--prefer-binary": False,
        "--require-hashes": False,
        "--requirement": True,
        "--trusted-host": True,
        "--use-feature": True,
        "-c": True,
        "-e": False,  # The required value is the requirement itself, not a parameter
        "-f": True,
        "--hash": True,
        "-i": True,
        "-r": True,
    }

    # Options that are specific to a single requirement in the requirements file. All other
    # options apply to all the requirements.
    REQUIREMENT_OPTIONS = {"-e", "--editable", "--hash"}

    def __init__(self, file_path):
        """Initialize a PipRequirementsFile.

        :param str file_path: the full path to the requirements file
        """
        self.file_path = file_path
        self.__parsed = NOTHING

    @classmethod
    def from_requirements_and_options(cls, requirements, options):
        """Create a new PipRequirementsFile instance from given parameters.

        :param list requirements: list of PipRequirement instances
        :param list options: list of strings of global options
        :return: new instance of PipRequirementsFile
        """
        new_instance = cls(None)
        new_instance.__parsed = {"requirements": list(requirements), "options": list(options)}
        return new_instance

    def write(self, file_path=None):
        """Write the options and requirements to a file.

        :param str file_path: the file path to write the new file. If not provided, the file
            path used when initiating the class is used.
        :raises ValidationError: if a file path cannot be determined
        """
        file_path = file_path or self.file_path
        if not file_path:
            raise RuntimeError("Unspecified 'file_path' for the requirements file")

        with open(file_path, "w") as f:
            if self.options:
                f.write(" ".join(self.options))
                f.write("\n")
            for requirement in self.requirements:
                f.write(str(requirement))
                f.write("\n")

    @property
    def requirements(self):
        """Return a list of PipRequirement objects."""
        return self._parsed["requirements"]

    @property
    def options(self):
        """Return a list of options."""
        return self._parsed["options"]

    @property
    def _parsed(self):
        """Return the parsed requirements file.

        :return: a dict with the keys ``requirements`` and ``options``
        """
        if self.__parsed is NOTHING:
            parsed = {"requirements": [], "options": []}

            for line in self._read_lines():
                (
                    global_options,
                    requirement_options,
                    requirement_line,
                ) = self._split_options_and_requirement(line)
                if global_options:
                    parsed["options"].extend(global_options)

                if requirement_line:
                    parsed["requirements"].append(
                        PipRequirement.from_line(requirement_line, requirement_options)
                    )

            self.__parsed = parsed

        return self.__parsed

    def _read_lines(self):
        """Read and yield the lines from the requirements file.

        Lines ending in the line continuation character are joined with the next line.
        Comment lines are ignored.
        """
        buffered_line = []

        with open(self.file_path) as f:
            for line in f.read().splitlines():
                if not line.endswith("\\"):
                    buffered_line.append(line)
                    new_line = "".join(buffered_line)
                    new_line = self.LINE_COMMENT.sub("", new_line).strip()
                    if new_line:
                        yield new_line
                    buffered_line = []
                else:
                    buffered_line.append(line.rstrip("\\"))

        # Last line ends in "\"
        if buffered_line:
            yield "".join(buffered_line)

    def _split_options_and_requirement(self, line):
        """Split global and requirement options from the requirement line.

        :param str line: requirement line from the requirements file
        :return: three-item tuple where the first item is a list of global options, the
            second item a list of requirement options, and the last item a str of the
            requirement without any options.
        """
        global_options = []
        requirement_options = []
        requirement = []

        # Indicates the option must be followed by a value
        _require_value = False
        # Reference to either global_options or requirement_options list
        _context_options = None

        for part in line.split():
            if _require_value:
                _context_options.append(part)
                _require_value = False
            elif part.startswith("-"):
                option = None
                value = None
                if "=" in part:
                    option, value = part.split("=", 1)
                else:
                    option = part

                if option not in self.OPTIONS:
                    raise ValidationError(f"Unknown requirements file option {part!r}")

                _require_value = self.OPTIONS[option]

                if option in self.REQUIREMENT_OPTIONS:
                    _context_options = requirement_options
                else:
                    _context_options = global_options

                if value and not _require_value:
                    raise ValidationError(f"Unexpected value for requirements file option {part!r}")

                _context_options.append(option)
                if value:
                    _context_options.append(value)
                    _require_value = False
            else:
                requirement.append(part)

        if _require_value:
            raise ValidationError(
                f"Requirements file option {_context_options[-1]!r} requires a value"
            )

        if requirement_options and not requirement:
            raise ValidationError(
                f"Requirements file option(s) {requirement_options!r} can only be applied to a "
                "requirement"
            )

        return global_options, requirement_options, " ".join(requirement)


class PipRequirement:
    """Parse a requirement and its options from a requirement line."""

    URL_SCHEMES = {"http", "https", "ftp"}

    VCS_SCHEMES = {
        "bzr",
        "bzr+ftp",
        "bzr+http",
        "bzr+https",
        "git",
        "git+ftp",
        "git+http",
        "git+https",
        "hg",
        "hg+ftp",
        "hg+http",
        "hg+https",
        "svn",
        "svn+ftp",
        "svn+http",
        "svn+https",
    }

    # Regex used to determine if a direct access requirement specifies a
    # package name, e.g. "name @ https://..."
    HAS_NAME_IN_DIRECT_ACCESS_REQUIREMENT = re.compile(r"@.+://")

    def __init__(self):
        """Initialize a PipRequirement."""
        # The package name after it has been processed by setuptools, e.g. "_" are replaced
        # with "-"
        self.package = None
        # The package name as defined in the requirement line
        self.raw_package = None
        self.extras = []
        self.version_specs = []
        self.environment_marker = None
        self.hashes = []
        self.qualifiers = {}

        self.kind = None
        self.download_line = None

        self.options = []

        self._url = None

    @property
    def url(self):
        """Extract the URL from the download line of a VCS or URL requirement."""
        if self._url is None:
            if self.kind not in ("url", "vcs"):
                raise ValueError(f"Cannot extract URL from {self.kind} requirement")
            # package @ url ; environment_marker
            parts = self.download_line.split()
            self._url = parts[2]

        return self._url

    def __str__(self):
        """Return the string representation of the PipRequirement."""
        line = []
        line.extend(self.options)
        line.append(self.download_line)
        line.extend(f"--hash={h}" for h in self.hashes)
        return " ".join(line)

    def copy(self, url=None, hashes=None):
        """Duplicate this instance of PipRequirement.

        :param str url: set a new direct access URL for the requirement. If provided, the
            new requirement is always of ``url`` kind.
        :param list hashes: overwrite hash values for the new requirement
        :return: new PipRequirement instance
        """
        options = list(self.options)
        download_line = self.download_line
        if url:
            download_line_parts = []
            download_line_parts.append(self.raw_package)
            download_line_parts.append("@")

            qualifiers_line = "&".join(f"{key}={value}" for key, value in self.qualifiers.items())
            if qualifiers_line:
                download_line_parts.append(f"{url}#{qualifiers_line}")
            else:
                download_line_parts.append(url)

            if self.environment_marker:
                download_line_parts.append(";")
                download_line_parts.append(self.environment_marker)

            download_line = " ".join(download_line_parts)

            # Pip does not support editable mode for requirements installed via an URL, only
            # via VCS. Remove this option to avoid errors later on.
            options = list(set(self.options) - {"-e", "--editable"})
            if self.options != options:
                log.warning(
                    "Removed editable option when copying the requirement %r", self.raw_package
                )

        requirement = self.__class__()

        requirement.package = self.package
        requirement.raw_package = self.raw_package
        # Extras are incorrectly treated as part of the URL itself. If we're setting
        # the URL, clear them.
        requirement.extras = [] if url else list(self.extras)
        # Version specs are ignored by pip when applied to a URL, let's do the same.
        requirement.version_specs = [] if url else list(self.version_specs)
        requirement.environment_marker = self.environment_marker
        requirement.hashes = list(hashes or self.hashes)
        requirement.qualifiers = dict(self.qualifiers)
        requirement.kind = "url" if url else self.kind
        requirement.download_line = download_line
        requirement.options = options

        return requirement

    @classmethod
    def from_line(cls, line, options):
        """Create an instance of PipRequirement from the given requirement and its options.

        Only ``url`` and ``vcs`` direct access requirements are supported. ``file`` is not.

        :param str line: the requirement line
        :param str list: the options associated with the requirement
        :return: PipRequirement instance
        """
        to_be_parsed = line
        qualifiers = {}
        requirement = cls()

        direct_access_kind, is_direct_access = cls._assess_direct_access_requirement(line)
        if is_direct_access:
            if direct_access_kind in ["url", "vcs"]:
                requirement.kind = direct_access_kind
                to_be_parsed, qualifiers = cls._adjust_direct_access_requirement(to_be_parsed)
            else:
                raise ValidationError(
                    f"Direct references with {direct_access_kind!r} scheme are not supported, "
                    "{to_be_parsed!r}"
                )
        else:
            requirement.kind = "pypi"

        try:
            parsed = list(pkg_resources.parse_requirements(to_be_parsed))
        except pkg_resources.RequirementParseError as exc:
            raise ValidationError(f"Unable to parse the requirement {to_be_parsed!r}: {exc}")

        if not parsed:
            return None
        # parse_requirements is able to process a multi-line string, thus returning multiple
        # parsed requirements. However, since it cannot handle the additional syntax from a
        # requirements file, we parse each line individually. The conditional below should
        # never be reached, but is left here to aid diagnosis in case this assumption is
        # not correct.
        if len(parsed) > 1:
            raise ValidationError(f"Multiple requirements per line are not supported, {line!r}")
        parsed = parsed[0]

        hashes, options = cls._split_hashes_from_options(options)

        requirement.download_line = to_be_parsed
        requirement.options = options
        requirement.package = parsed.project_name
        requirement.raw_package = parsed.name
        requirement.version_specs = parsed.specs
        requirement.extras = parsed.extras
        requirement.environment_marker = str(parsed.marker) if parsed.marker else None
        requirement.hashes = hashes
        requirement.qualifiers = qualifiers

        return requirement

    @classmethod
    def _assess_direct_access_requirement(cls, line):
        """Determine if the line contains a direct access requirement.

        :param str line: the requirement line
        :return: two-item tuple where the first item is the kind of dicrect access requirement,
            e.g. "vcs", and the second item is a bool indicating if the requirement is a
            direct access requirement
        """
        direct_access_kind = None

        if ":" not in line:
            return None, False
        # Extract the scheme from the line and strip off the package name if needed
        # e.g. name @ https://...
        scheme_parts = line.split(":", 1)[0].split("@")
        if len(scheme_parts) > 2:
            raise ValidationError(
                f"Unable to extract scheme from direct access requirement {line!r}"
            )
        scheme = scheme_parts[-1].lower().strip()

        if scheme in cls.URL_SCHEMES:
            direct_access_kind = "url"
        elif scheme in cls.VCS_SCHEMES:
            direct_access_kind = "vcs"
        else:
            direct_access_kind = scheme

        return direct_access_kind, True

    @classmethod
    def _adjust_direct_access_requirement(cls, line):
        """Modify the requirement line so it can be parsed by pkg_resources and extract qualifiers.

        :param str line: a direct access requirement line
        :return: two-item tuple where the first item is a modified direct access requirement
            line that can be parsed by pkg_resources, and the second item is a dict of the
            qualifiers extracted from the direct access URL
        """
        package_name = None
        qualifiers = {}
        url = line
        environment_marker = None

        if cls.HAS_NAME_IN_DIRECT_ACCESS_REQUIREMENT.search(line):
            package_name, url = line.split("@", 1)

        # For direct access requirements, a space is needed after the semicolon.
        if "; " in url:
            url, environment_marker = url.split("; ", 1)

        parsed_url = urllib.parse.urlparse(url)
        if parsed_url.fragment:
            for section in parsed_url.fragment.split("&"):
                if "=" in section:
                    attr, value = section.split("=", 1)
                    qualifiers[attr] = value
                    if attr == "egg":
                        # Use the egg name as the package name to avoid ambiguity when both are
                        # provided. This matches the behavior of "pip install".
                        package_name = value

        if not package_name:
            raise ValidationError(f"Egg name could not be determined from the requirement {line!r}")

        requirement_parts = [package_name.strip(), "@", url.strip()]
        if environment_marker:
            # Although a space before the semicolon is not needed by pip, it is needed when
            # using pkg_resources later on.
            requirement_parts.append(";")
            requirement_parts.append(environment_marker.strip())
        return " ".join(requirement_parts), qualifiers

    @classmethod
    def _split_hashes_from_options(cls, options):
        """Separate the --hash options from the given options.

        :param list options: requirement options
        :return: two-item tuple where the first item is a list of hashes, and the second item
            is a list of options without any ``--hash`` options
        """
        hashes = []
        reduced_options = []
        is_hash = False

        for item in options:
            if is_hash:
                hashes.append(item)
                is_hash = False
                continue

            is_hash = item == "--hash"
            if not is_hash:
                reduced_options.append(item)

        return hashes, reduced_options


def prepare_nexus_for_pip_request(pip_repo_name, raw_repo_name):
    """
    Prepare Nexus so that Cachito can stage Python content.

    :param str pip_repo_name: the name of the pip repository for the request
    :param str raw_repo_name: the name of the raw repository for the request
    :raise CachitoError: if the script execution fails
    """
    payload = {
        "pip_repository_name": pip_repo_name,
        "raw_repository_name": raw_repo_name,
    }
    script_name = "pip_before_content_staged"
    try:
        nexus.execute_script(script_name, payload)
    except NexusScriptError:
        log.exception("Failed to execute the script %s", script_name)
        raise CachitoError("Failed to prepare Nexus for Cachito to stage Python content")


def finalize_nexus_for_pip_request(pip_repo_name, raw_repo_name, username):
    """
    Configure Nexus so that the request's Pyhton repositories are ready for consumption.

    :param str pip_repo_name: the name of the pip repository for the Cachito pip request
    :param str raw_repo_name: the name of the raw repository for the Cachito pip request
    :param str username: the username of the user to be created for the Cachito pip request
    :return: the password of the Nexus user that has access to the request's Python repositories
    :rtype: str
    :raise CachitoError: if the script execution fails
    """
    # Generate a 24-32 character (each byte is two hex characters) password
    password = secrets.token_hex(random.randint(12, 16))
    payload = {
        "password": password,
        "pip_repository_name": pip_repo_name,
        "raw_repository_name": raw_repo_name,
        "username": username,
    }
    script_name = "pip_after_content_staged"
    try:
        nexus.execute_script(script_name, payload)
    except NexusScriptError:
        log.exception("Failed to execute the script %s", script_name)
        raise CachitoError("Failed to configure Nexus Python repositories for final consumption")
    return password


def download_dependencies(request_id, requirements_file):
    """
    Download sdists (source distributions) of all dependencies in a requirements.txt file.

    :param int request_id: ID of the request these dependencies are being downloaded for
    :param PipRequirementsFile requirements_file: A requirements.txt file
    :return: Info about downloaded packages; all items will contain "kind" and "path" keys
        (and more based on kind, see _download_*_package functions for more details)
    :rtype: list[dict]
    """
    options = _process_options(requirements_file.options)

    if options["require_hashes"]:
        log.info("Global --require-hashes option used, will require hashes")
        require_hashes = True
    elif any(req.hashes for req in requirements_file.requirements):
        log.info("At least one dependency uses the --hash option, will require hashes")
        require_hashes = True
    else:
        log.info(
            "No hash options used, will not require hashes for non-HTTP(S) dependencies. "
            "HTTP(S) dependencies always require hashes (use the #cachito_hash URL qualifier)."
        )
        require_hashes = False

    _validate_requirements(requirements_file.requirements, require_hashes)

    bundle_dir = RequestBundleDir(request_id)
    bundle_dir.pip_deps_dir.mkdir(parents=True, exist_ok=True)

    config = get_worker_config()
    pypi_proxy_url = config.cachito_nexus_pypi_proxy_url
    pip_raw_repo_name = config.cachito_nexus_pip_raw_repo_name

    nexus_username, nexus_password = nexus.get_nexus_hoster_credentials()
    nexus_auth = requests.auth.HTTPBasicAuth(nexus_username, nexus_password)
    pypi_proxy_auth = nexus_auth

    downloads = []

    for req in requirements_file.requirements:
        log.info("Downloading %s", req.download_line)

        if req.kind == "pypi":
            download_info = _download_pypi_package(
                req, bundle_dir.pip_deps_dir, pypi_proxy_url, pypi_proxy_auth
            )
        elif req.kind == "vcs":
            download_info = _download_vcs_package(
                req, bundle_dir.pip_deps_dir, pip_raw_repo_name, nexus_auth
            )
        else:
            log.warning("Dependency type not yet supported: %s", req.download_line)
            continue

        log.info(
            "Successfully downloaded %s to %s",
            req.download_line,
            download_info["path"].relative_to(bundle_dir),
        )

        if require_hashes:
            _verify_hash(download_info["path"], req.hashes)

        download_info["kind"] = req.kind
        downloads.append(download_info)

    return downloads


def _process_options(options):
    """
    Process global options from a requirements.txt file.

    | Rejected option     | Reason                                                  |
    |---------------------|---------------------------------------------------------|
    | -i --index-url      | We only support the index which our proxy supports      |
    | --extra-index-url   | We only support one index                               |
    | --no-index          | Index is the only thing we support                      |
    | -f --find-links     | We only support index                                   |
    | --only-binary       | Only sdist                                              |

    | Ignored option      | Reason                                                  |
    |---------------------|---------------------------------------------------------|
    | -c --constraint     | All versions must already be pinned                     |
    | -e --editable       | Only relevant when installing                           |
    | --no-binary         | Implied                                                 |
    | --prefer-binary     | Prefer sdist                                            |
    | --pre               | We do not care if version is pre-release (it is pinned) |
    | --use-feature       | We probably do not have that feature                    |
    | -* --*              | Did not exist when this implementation was done         |

    | Undecided option    | Reason                                                  |
    |---------------------|---------------------------------------------------------|
    | -r --requirement    | We could support this but there is no good reason to    |
    | --trusted-host      | Could be relevant for Git and HTTP(S) dependencies      |

    | Relevant option     | Reason                                                  |
    |---------------------|---------------------------------------------------------|
    | --require-hashes    | Hashes are optional, so this makes sense                |

    :param list[str] options: Global options from a requirements file
    :return: Dict with all the relevant options and their values
    :raise ValidationError: If any option was rejected
    """
    reject = {
        "-i",
        "--index-url",
        "--extra-index-url",
        "--no-index",
        "-f",
        "--find-links",
        "--only-binary",
    }

    require_hashes = False
    ignored = []
    rejected = []

    for option in options:
        if option == "--require-hashes":
            require_hashes = True
        elif option in reject:
            rejected.append(option)
        elif option.startswith("-"):
            # This is a bit simplistic, option arguments may also start with a '-' but
            # should be good enough for a log message
            ignored.append(option)

    if ignored:
        msg = f"Cachito will ignore the following options: {', '.join(ignored)}"
        log.info(msg)

    if rejected:
        msg = f"Cachito does not support the following options: {', '.join(rejected)}"
        raise ValidationError(msg)

    return {
        "require_hashes": require_hashes,
    }


def _validate_requirements(requirements, require_hashes):
    """
    Validate that all requirements meet Cachito expectations.

    :param list[PipRequirement] requirements: All requirements from a file
    :param bool require_hashes: True if all requirements must specify a checksum
    :raise ValidationError: If any requirement does not meet expectations
    """
    # Fail if any PyPI dependency is not pinned to an exact version
    for req in filter(lambda r: r.kind == "pypi", requirements):
        vspec = req.version_specs
        if len(vspec) != 1 or vspec[0][0] not in ("==", "==="):
            msg = f"Requirement must be pinned to an exact version: {req.download_line}"
            raise ValidationError(msg)

    # Fail if any dependency requires a hash but does not specify one
    for req in requirements:
        hash_required = require_hashes or req.kind == "url"

        if hash_required and not req.hashes and not req.qualifiers.get("cachito_hash"):
            msg = f"Hash is required, dependency does not specify any: {req.download_line}"
            raise ValidationError(msg)

    # Fail if any VCS requirement uses any VCS other than git or does not have a valid ref
    for req in filter(lambda r: r.kind == "vcs", requirements):
        url = urllib.parse.urlparse(req.url)

        if not url.scheme.startswith("git"):
            raise ValidationError(f"Unsupported VCS for {req.download_line}: {url.scheme}")

        if not GIT_REF_IN_PATH.search(url.path):
            msg = f"No valid git ref in {req.download_line} (expected 40 hexadecimal characters)"
            raise ValidationError(msg)


def _download_pypi_package(requirement, pip_deps_dir, pypi_url, pypi_auth):
    """
    Download the sdist (source distribution) of a PyPI package.

    The package must be pinned to an exact version using the '==' (or '===') operator.
    While the specification defines the '==' operator as slightly magical (reference:
    https://www.python.org/dev/peps/pep-0440/#version-matching), we treat the version
    as exact.

    Does not download any dependencies (implied: ignores extras). Ignores environment
    markers (target environment is not known to Cachito).

    :param PipRequirement requirement: PyPI requirement from a requirement.txt file
    :param Path pip_deps_dir: The deps/pip directory in a Cachito request bundle
    :param str pypi_url: URL of PyPI (proxy) server
    :param requests.auth.AuthBase pypi_auth: Authorization for the PyPI server

    :return: Dict with package name, version and download path
    """
    package = requirement.package
    version = requirement.version_specs[0][1]

    # See https://warehouse.readthedocs.io/api-reference/json/
    package_url = f"{pypi_url.rstrip('/')}/pypi/{package}/{version}/json"
    try:
        pypi_resp = requests_session.get(package_url, auth=pypi_auth)
        pypi_resp.raise_for_status()
    except requests.RequestException as e:
        raise CachitoError(f"PyPI query failed: {e}")

    data = pypi_resp.json()
    sdists = [pkg for pkg in data.get("urls", []) if pkg.get("packagetype") == "sdist"]
    if not sdists:
        raise CachitoError(f"No sdists found for package {package}=={version}")

    # Choose best candidate based on sorting key
    sdist = max(sdists, key=_sdist_preference)
    if sdist.get("yanked", False):
        raise CachitoError(f"All sdists for package {package}=={version} are yanked")

    package_dir = pip_deps_dir / package
    package_dir.mkdir(exist_ok=True)
    download_path = package_dir / sdist["filename"]

    download_binary_file(sdist["url"], download_path)

    package_info = data.get("info", {})

    return {
        # Use canonical package name and version from PyPI response (if present)
        "package": package_info.get("name") or package,
        "version": package_info.get("version") or version,
        "path": download_path,
    }


def _sdist_preference(sdist_pkg):
    """
    Compute preference for a sdist package, can be used to sort in ascending order.

    Prefer files that are not yanked over ones that are.
    Within the same category (yanked vs. not), prefer .tar.gz > .zip > anything else.

    :param dict sdist_pkg: An item of the "urls" array in a PyPI response
    :return: Tuple of integers to use as sorting key
    """
    # Higher number = higher preference
    yanked_pref = 0 if sdist_pkg.get("yanked", False) else 1

    filename = sdist_pkg["filename"]
    if filename.endswith(".tar.gz"):
        filetype_pref = 2
    elif filename.endswith(".zip"):
        filetype_pref = 1
    else:
        filetype_pref = 0

    return yanked_pref, filetype_pref


def _download_vcs_package(requirement, pip_deps_dir, pip_raw_repo_name, nexus_auth):
    """
    Fetch the source for a Python package from VCS (only git is supported).

    After downloading, upload this package to the Pip raw repository on the Nexus hoster instance,
    and on subsequent downloads, reuse the uploaded asset instead of fetching from VCS again.

    :param PipRequirement requirement: VCS requirement from a requirements.txt file
    :param Path pip_deps_dir: The deps/pip directory in a Cachito request bundle
    :param str pip_raw_repo_name: Name of the Nexus raw repository for Pip
    :param requests.auth.AuthBase nexus_auth: Authorization for the Nexus raw repo

    :return: Dict with package name, name of raw component in Nexus, download path and git info
    """
    git_info = _extract_git_info(requirement.url)

    namespace_parts = git_info["namespace"].split("/")
    repo_name = git_info["repo"]
    ref = git_info["ref"]

    # Download to e.g. deps/pip/github.com/namespace/repo
    package_dir = pip_deps_dir.joinpath(git_info["host"], *namespace_parts, repo_name)
    package_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{repo_name}-external-gitcommit-{ref}.tar.gz"
    download_path = package_dir / filename

    # Check if we already have the raw component
    raw_component_name = f"{repo_name}/{filename}"
    log.debug("Looking for raw component %r in %r repo", raw_component_name, pip_raw_repo_name)
    download_url = nexus.get_raw_component_asset_url(pip_raw_repo_name, raw_component_name)

    if download_url is not None:
        log.debug("Found raw component, will download from %r", download_url)
        download_binary_file(download_url, download_path, auth=nexus_auth)
    else:
        log.debug("Raw component not found, will fetch from git")
        repo = Git(git_info["url"], ref)
        repo.fetch_source()
        log.debug("Fetched package, uploading as %r to %r", raw_component_name, pip_raw_repo_name)
        upload_raw_package(
            pip_raw_repo_name,
            repo.sources_dir.archive_path,
            dest_dir=repo_name,
            filename=filename,
            is_request_repository=False,
        )
        # Copy downloaded archive to expected download path
        shutil.copy(repo.sources_dir.archive_path, download_path)

    return {
        "package": requirement.package,
        "raw_component_name": raw_component_name,
        "path": download_path,
        **git_info,
    }


def _extract_git_info(vcs_url):
    """
    Extract important info from a VCS requirement URL.

    Given a URL such as git+https://user:pass@host:port/namespace/repo.git@123456?foo=bar#egg=spam
    this function will extract:
    - the "clean" URL: https://user:pass@host:port/namespace/repo.git?foo=bar#egg=spam
    - the git ref: 123456
    - the host, namespace and repo: host:port, namespace, repo

    The clean URL and ref can be passed straight to scm.Git to fetch the repo.
    The host, namespace and repo will be used to construct the file path under deps/pip.

    :param str vcs_url: The URL of a VCS requirement, must be valid (have git ref in path)
    :return: Dict with url, ref, host, namespace and repo keys
    """
    url = urllib.parse.urlparse(vcs_url)

    # If scheme is git+protocol://, keep only protocol://
    if url.scheme.startswith("git+"):
        clean_scheme = url.scheme[len("git+") :]
    else:
        clean_scheme = url.scheme

    ref = url.path[-40:]  # Take the last 40 characters (the git ref)
    clean_path = url.path[:-41]  # Drop the last 41 characters ('@' + git ref)

    clean_url = urllib.parse.ParseResult(
        scheme=clean_scheme,
        netloc=url.netloc,
        path=clean_path,
        params=url.params,
        query=url.query,
        fragment=url.fragment,
    )

    # Assume everything up to the last '@' is user:pass. This should be kept in the
    # clean URL used for fetching, but should not be considered part of the host.
    _, _, clean_netloc = url.netloc.rpartition("@")

    namespace_repo = clean_path.strip("/")
    if namespace_repo.endswith(".git"):
        namespace_repo = namespace_repo[: -len(".git")]

    # Everything up to the last '/' is namespace, the rest is repo
    namespace, _, repo = namespace_repo.rpartition("/")

    return {
        "url": clean_url.geturl(),
        "ref": ref.lower(),
        "host": clean_netloc,
        "namespace": namespace,
        "repo": repo,
    }


def _verify_hash(download_path, hashes):
    """
    Check that downloaded archive verifies against at least one of the provided hashes.

    :param Path download_path: Path to downloaded file
    :param list[str] hashes: All provided hashes for requirement
    :raise CachitoError: If computed hash does not match any of the provided hashes
    """
    log.info(f"Verifying checksum of {download_path.name}")

    checksums = []

    for hash_spec in hashes:
        algorithm, _, digest = hash_spec.partition(":")
        if not digest:
            msg = f"Not a valid hash specifier: {hash_spec!r} (expected algorithm:digest)"
            raise CachitoError(msg)

        checksums.append(ChecksumInfo(algorithm, digest))

    for checksum_info in checksums:
        try:
            verify_checksum(str(download_path), checksum_info)
            algorithm, digest = checksum_info
            log.info(f"Checksum of {download_path.name} matches: {algorithm}:{digest}")
            return
        except CachitoError as e:
            log.error("%s", e)

    msg = f"Failed to verify checksum of {download_path.name} against any of the provided hashes"
    raise CachitoError(msg)


def upload_pypi_package(repo_name, artifact_path):
    """
    Upload a PyPI Python package to a Nexus repository.

    :param str repo_name: the name of the hosted PyPI repository to upload the package to
    :param str artifact_path: the path for the PyPI package to be uploaded
    """
    log.debug("Uploading %r as a PyPI package to the %r Nexus repository", artifact_path, repo_name)
    # PyPI packages should always be uploaded to a hosted repository. Hence, we never use the
    # hoster instance, which holds only the PyPI proxy and the hosted raw repository.
    nexus.upload_asset_only_component(repo_name, "pypi", artifact_path, to_nexus_hoster=False)


def upload_raw_package(repo_name, artifact_path, dest_dir, filename, is_request_repository):
    """
    Upload a raw Python package to a Nexus repository.

    :param str repo_name: the name of the hosted raw repository to upload the package to
    :param str artifact_path: the path of the raw Python package to be uploaded
    :param str dest_dir: the path of the directory to where the raw Python package will be uploaded
        to in the Nexus repository
    :param str filename: the name to save the file with after it is uploaded to the dest_dir
    :param bool is_request_repository: whether to use the cachito nexus instance or the hoster one,
        if available
    """
    components = [{"path": artifact_path, "filename": filename}]
    to_nexus_hoster = not is_request_repository
    log.debug("Uploading %r as a raw package to the %r Nexus repository", artifact_path, repo_name)
    nexus.upload_raw_component(repo_name, dest_dir, components, to_nexus_hoster)


def get_pypi_hosted_repo_name(request_id):
    """
    Get the name of the Nexus PyPI hosted repository for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the name of the PyPI hosted repository for the request
    :rtype: str
    """
    config = get_worker_config()
    return f"{config.cachito_nexus_request_repo_prefix}pip-hosted-{request_id}"


def get_raw_hosted_repo_name(request_id):
    """
    Get the name of the Nexus raw hosted repository for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the name of the raw hosted repository for the request
    :rtype: str
    """
    config = get_worker_config()
    return f"{config.cachito_nexus_request_repo_prefix}pip-raw-{request_id}"


def get_pypi_hosted_repo_url(request_id):
    """
    Get the URL for the Nexus PyPI hosted repository for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the URL for the Nexus PyPI hosted repository for the request
    :rtype: str
    """
    config = get_worker_config()
    repo_name = get_pypi_hosted_repo_name(request_id)
    return f"{config.cachito_nexus_url.rstrip('/')}/repository/{repo_name}/"


def get_raw_hosted_repo_url(request_id):
    """
    Get the URL for the Nexus PyPI hosted repository for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the URL for the Nexus PyPI hosted repository for the request
    :rtype: str
    """
    config = get_worker_config()
    repo_name = get_raw_hosted_repo_name(request_id)
    return f"{config.cachito_nexus_url.rstrip('/')}/repository/{repo_name}/"


def get_hosted_repositories_username(request_id):
    """
    Get the username that has read access on the PyPI and raw hosted repositories for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the username
    :rtype: str
    """
    return f"cachito-pip-{request_id}"
