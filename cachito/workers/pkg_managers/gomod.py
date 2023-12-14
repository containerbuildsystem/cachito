# SPDX-License-Identifier: GPL-3.0-or-later
import fnmatch
import functools
import logging
import os
import os.path
import re
import shutil
import tempfile
from datetime import datetime
from itertools import chain
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Iterator, List, Literal, Optional, Tuple, Union

import backoff
import git
import pydantic
import semver
from opentelemetry import trace

# if a namespace alias isn't used, flake8 starts complaining whenever version.Version is used:
# 'local variable 'version' defined in enclosing scope on line N referenced before assignment'
from packaging import version as pkgver

from cachito.errors import (
    GoModError,
    InvalidFileFormat,
    RepositoryAccessError,
    UnsupportedFeature,
    ValidationError,
)
from cachito.workers import load_json_stream, run_cmd
from cachito.workers.config import get_worker_config
from cachito.workers.errors import CachitoCalledProcessError
from cachito.workers.paths import RequestBundleDir

__all__ = [
    "get_golang_version",
    "resolve_gomod",
    "contains_package",
    "path_to_subpackage",
    "match_parent_module",
]

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

MODULE_VERSION_RE = re.compile(r"/v\d+$")


class _GolangModel(pydantic.BaseModel):
    """Attributes automatically get PascalCase aliases to make parsing Golang JSON easier.

    >>> class SomeModel(_GolangModel):
            some_attribute: str

    >>> SomeModel.parse_obj({"SomeAttribute": "hello"})
    SomeModel(some_attribute="hello")
    """

    class Config:
        @staticmethod
        def alias_generator(attr_name: str) -> str:
            return "".join(word.capitalize() for word in attr_name.split("_"))

        # allow SomeModel(some_attribute="hello"), not just SomeModel(SomeAttribute="hello")
        allow_population_by_field_name = True


class GoModule(_GolangModel):
    """A Go module as returned by the -json option of various commands (relevant fields only).

    See:
        go help mod download    (Module struct)
        go help list            (Module struct)
    """

    path: str
    version: Optional[str] = None
    main: bool = False
    replace: Optional["GoModule"] = None


class GoPackage(_GolangModel):
    """A Go package as returned by the -json option of go list (relevant fields only).

    See:
        go help list    (Package struct)
    """

    import_path: str
    standard: bool = False
    module: Optional[GoModule]
    deps: list[str] = []


class Go:
    """High level wrapper over the 'go' CLI command.

    Provides convenient methods to download project dependencies, alternative toolchains,
    parses various Go files, etc.
    """

    def __init__(
        self,
        binary: Union[str, os.PathLike[str]] = "go",
        release: Optional[str] = None,
    ) -> None:
        """Initialize the Go toolchain wrapper.

        :param binary: path-like string to the Go binary or direct command (in PATH)
        :param release: Go release version string, e.g. go1.20, go1.21.10
        :returns: a callable instance
        """
        # run_cmd will take care of checking any bogus passed in 'binary'
        self._bin = str(binary)
        self._release = release

        if self._release:
            self._bin = f"/usr/local/go/{release}/bin/go"

        self._version: Optional[pkgver.Version] = None

    @tracer.start_as_current_span("Go.__call__")
    def __call__(self, cmd: list[str], params: Optional[dict] = None, retry: bool = False) -> str:
        """Run a Go command using the underlying toolchain, same as running Go()().

        :param cmd: Go CLI options
        :param params: additional subprocess arguments, e.g. 'env'
        :param retry: whether the command should be retried on failure (e.g. network actions)
        :returns: Go command's output
        """
        if params is None:
            params = {}

        cmd = [self._bin] + cmd
        if retry:
            return self._retry(cmd, **params)

        return self._run(cmd, **params)

    @property
    def version(self) -> pkgver.Version:
        """Version of the Go toolchain as a packaging.version.Version object."""
        if not self._version:
            self._version = pkgver.Version(self.release[2:])
        return self._version

    @property
    def release(self) -> str:
        """Release name of the Go Toolchain, e.g. go1.20 ."""
        # lazy evaluation: defer running 'go'
        if not self._release:
            output = self(["version"])
            log.info(f"Go release: {output}")
            release_pattern = f"go{pkgver.VERSION_PATTERN}"

            # packaging.version requires passing the re.VERBOSE|re.IGNORECASE flags [1]
            # [1] https://packaging.pypa.io/en/latest/version.html#packaging.version.VERSION_PATTERN
            if match := re.search(release_pattern, output, re.VERBOSE | re.IGNORECASE):
                self._release = match.group(0)
            else:
                # This should not happen, otherwise we must figure out a more reliable way of
                # extracting Go version
                raise GoModError(
                    f"Could not extract Go toolchain version from Go's output: '{output}'",
                )
        return self._release

    def _retry(self, cmd: list[str], **kwargs: Any) -> str:
        """Run gomod command in a networking context.

        Commands that involve networking, such as dependency downloads, may fail due to network
        errors (go is bad at retrying), so the entire operation will be retried a configurable
        number of times.

        The same cache directory will be use between retries, so Go will not have to download the
        same artifact (e.g. dependency) twice. The backoff is exponential, Cachito will wait 1s ->
        2s -> 4s -> ... before retrying.
        """
        n_tries = get_worker_config().cachito_gomod_download_max_tries

        @backoff.on_exception(
            backoff.expo,
            GoModError,
            jitter=None,  # use deterministic backoff, do not apply jitter
            max_tries=n_tries,
            logger=log,
        )
        def run_go(_cmd: list[str], **kwargs: Any) -> str:
            return self._run(_cmd, **kwargs)

        try:
            return run_go(cmd, **kwargs)
        except GoModError:
            err_msg = (
                f"Go execution failed: Cachito re-tried running `{' '.join(cmd)}` command "
                f"{n_tries} times."
            )
            raise GoModError(err_msg) from None

    def _run(self, cmd: list[str], **kwargs: Any) -> str:
        try:
            log.debug(f"Running '{cmd}'")
            return run_cmd(cmd, kwargs)
        except CachitoCalledProcessError as e:
            rc = e.retcode
            raise GoModError(f"Go execution failed: `{' '.join(cmd)}` failed with {rc=}") from e


class GoCacheTemporaryDirectory(tempfile.TemporaryDirectory):
    """
    A wrapper around the TemporaryDirectory context manager to also run `go clean -modcache`.

    The files in the Go cache are read-only by default and cause the default clean up behavior of
    tempfile.TemporaryDirectory to fail with a permission error. A way around this is to run
    `go clean -modcache` before the default clean up behavior is run.
    """

    def __exit__(self, exc, value, tb):
        """Clean up the temporary directory by first cleaning up the Go cache."""
        try:
            env = {"GOPATH": self.name, "GOCACHE": self.name}
            go = Go()
            go(["clean", "-modcache"], {"env": env})
        finally:
            super().__exit__(exc, value, tb)


def contains_package(parent_name: str, package_name: str) -> bool:
    """
    Check that parent module/package contains specified package.

    :param parent_name: name of parent module or package
    :param package_name: name of package to check
    :return: True if package belongs to parent, False otherwise
    """
    if not package_name.startswith(parent_name):
        return False
    if len(package_name) > len(parent_name):
        # Check that the subpackage is {parent_name}/* and not {parent_name}*/*
        return package_name[len(parent_name)] == "/"
    # At this point package_name == parent_name, every package contains itself
    return True


def path_to_subpackage(parent_name: str, subpackage_name: str) -> str:
    """
    Get relative path from parent module/package to subpackage inside the parent.

    If the subpackage and parent names are identical, returns empty string.
    The subpackage name must start with the parent name.

    :param parent_name: name of parent module or package
    :param subpackage_name: name of subpackage inside the parent module/package
    :return: relative path from parent to subpackage
    :raises ValueError: if subpackage name does not start with parent name
    """
    if not contains_package(parent_name, subpackage_name):
        raise ValueError(f"Package {subpackage_name} does not belong to {parent_name}")
    return subpackage_name[len(parent_name) :].lstrip("/")


def match_parent_module(package_name: str, module_names: Iterable[str]) -> Optional[str]:
    """
    Find parent module for package in iterable of module names.

    Picks the longest module name that matches the package name
    (the package name must start with the module name).

    :param package_name: name of package
    :param module_names: iterable of module names
    :return: longest matching module name or None (no module matches)
    """
    contains_this_package = functools.partial(contains_package, package_name=package_name)
    return max(
        filter(contains_this_package, module_names),
        key=len,  # type: ignore
        default=None,
    )


@tracer.start_as_current_span("resolve_gomod")
def resolve_gomod(app_source_path, request, dep_replacements=None, git_dir_path=None):
    """
    Resolve and fetch gomod dependencies for given app source archive.

    :param str app_source_path: the full path to the application source code
    :param dict request: the Cachito request this is for
    :param list dep_replacements: dependency replacements with the keys "name" and "version"; this
        results in a series of `go mod edit -replace` commands
    :param RequestBundleDir git_dir_path: the full path to the application's git repository
    :return: a dict containing the Go module itself ("module" key), the list of dictionaries
        representing the dependencies ("module_deps" key), the top package level dependency
        ("pkg" key), and a list of dictionaries representing the package level dependencies
        ("pkg_deps" key)
    :rtype: dict
    :raises GoModError: if fetching dependencies fails
    """
    if git_dir_path is None:
        git_dir_path = app_source_path
    if not dep_replacements:
        dep_replacements = []

    worker_config = get_worker_config()
    athens_url = worker_config.cachito_athens_url
    with GoCacheTemporaryDirectory(prefix="cachito-") as temp_dir:
        env = {
            "GOPATH": temp_dir,
            "GO111MODULE": "on",
            "GOCACHE": temp_dir,
            "GOPROXY": f"{athens_url}|{athens_url}",
            "PATH": os.environ.get("PATH", ""),
            "GOMODCACHE": "{}/pkg/mod".format(temp_dir),
            "GOTOOLCHAIN": "local",
        }
        if "cgo-disable" in request.get("flags", []):
            env["CGO_ENABLED"] = "0"

        run_params = {"env": env, "cwd": app_source_path}

        go = _select_go_toolchain(git_dir_path)

        # Collect all the dependency names that are being replaced to later report which
        # dependencies were replaced
        deps_to_replace = set()
        for dep_replacement in dep_replacements:
            name = dep_replacement["name"]
            deps_to_replace.add(name)
            new_name = dep_replacement.get("new_name", name)
            version = dep_replacement["version"]
            log.info("Applying the gomod replacement %s => %s@%s", name, new_name, version)
            go(["mod", "edit", "-replace", f"{name}={new_name}@{version}"], run_params)

        # Vendor dependencies if the gomod-vendor flag is set
        flags = request.get("flags", [])
        should_vendor, can_make_changes = _should_vendor_deps(
            flags, app_source_path, worker_config.cachito_gomod_strict_vendor
        )
        if should_vendor:
            downloaded_modules = _vendor_deps(go, run_params, can_make_changes, git_dir_path)
        else:
            log.info("Downloading the gomod dependencies")
            download_opts = ["mod", "download", "-json"]
            downloaded_modules = [
                GoModule.parse_obj(obj)
                for obj in load_json_stream(go(download_opts, run_params, retry=True))
            ]

        if "force-gomod-tidy" in flags or dep_replacements:
            go(["mod", "tidy"], run_params)

        bundle_dir = RequestBundleDir(request["id"])
        if should_vendor:
            # Create an empty gomod cache in the bundle directory so that any Cachito
            # user does not have to guard against this directory not existing
            bundle_dir.gomod_download_dir.mkdir(exist_ok=True, parents=True)
        else:
            # Add the gomod cache to the bundle the user will later download
            tmp_download_cache_dir = os.path.join(
                temp_dir, RequestBundleDir.go_mod_cache_download_part
            )
            if not os.path.exists(tmp_download_cache_dir):
                os.makedirs(tmp_download_cache_dir, exist_ok=True)

            log.debug(
                "Adding dependencies from %s to %s",
                tmp_download_cache_dir,
                bundle_dir.gomod_download_dir,
            )
            _merge_bundle_dirs(tmp_download_cache_dir, str(bundle_dir.gomod_download_dir))

        go_list = ["list", "-e"]
        if not should_vendor:
            # Make Go ignore the vendor dir even if there is one
            go_list.extend(["-mod", "readonly"])

        main_module_name = go([*go_list, "-m"], run_params).rstrip()
        main_module_version = get_golang_version(
            main_module_name,
            git_dir_path,
            request["ref"],
            update_tags=True,
            subpath=(
                None
                if app_source_path == str(git_dir_path)
                else app_source_path.replace(f"{git_dir_path}/", "")
            ),
        )
        main_module = {
            "type": "gomod",
            "name": main_module_name,
            "version": main_module_version,
        }

        def go_list_deps(pattern: Literal["./...", "all"]) -> Iterator[GoPackage]:
            """Run go list -deps -json and return the parsed list of packages.

            The "./..." pattern returns the list of packages compiled into the final binary.

            The "all" pattern includes dependencies needed only for tests. Use it to get a more
            complete module list (roughly matching the list of downloaded modules).
            """
            opts = [*go_list, "-deps", "-json=ImportPath,Module,Standard,Deps", pattern]
            return map(GoPackage.parse_obj, load_json_stream(go(opts, run_params)))

        package_modules = (
            mod for pkg in go_list_deps("all") if (mod := pkg.module) and not mod.main
        )
        main_module_deps = _deduplicate_to_gomod_dicts(
            chain(package_modules, downloaded_modules), deps_to_replace
        )

        log.info("Retrieving the list of packages")
        main_packages: list[dict[str, Any]] = []
        main_packages_deps = set()
        all_packages = {pkg.import_path: pkg for pkg in go_list_deps("./...")}

        # The go list -deps command lists dependencies first and then the package which
        # depends on them. We want the opposite order: package first, then its dependencies.
        for pkg in reversed(all_packages.values()):
            if not pkg.module or not pkg.module.main:
                continue  # This is a dependency, not a top-level package

            if pkg.import_path in main_packages_deps:
                # If a top-level package is already listed as a dependency, we do not list it here,
                # since its dependencies are already listed in the parent package.
                log.debug(
                    "Package %s is already listed as a package dependency. Skipping...",
                    pkg.import_path,
                )
                continue

            pkg_deps = []
            for dep_name in pkg.deps:
                dep = all_packages[dep_name]
                main_packages_deps.add(dep_name)

                if dep.standard:
                    dep_version = None
                elif not dep.module or dep.module.main:
                    # Standard=false, Module.Main=true
                    # Standard=false, Module=null   <- probably a to-be-generated package
                    dep_version = main_module_version
                else:
                    _, dep_version = _get_name_and_version(dep.module)

                pkg_deps.append({"type": "go-package", "name": dep_name, "version": dep_version})

            main_pkg = {
                "type": "go-package",
                "name": pkg.import_path,
                "version": main_module_version,
            }
            main_packages.append({"pkg": main_pkg, "pkg_deps": pkg_deps})

        allowlist = _get_allowed_local_deps(main_module_name)
        log.debug("Allowed local dependencies for %s: %s", main_module_name, allowlist)
        _vet_local_deps(
            main_module_deps, main_module_name, allowlist, app_source_path, git_dir_path
        )
        for pkg in main_packages:
            # Local dependencies are always relative to the main module, even for subpackages
            _vet_local_deps(
                pkg["pkg_deps"], main_module_name, allowlist, app_source_path, git_dir_path
            )
            _set_full_local_dep_relpaths(pkg["pkg_deps"], main_module_deps)

        return {
            "module": main_module,
            "module_deps": main_module_deps,
            "packages": main_packages,
        }


def _deduplicate_to_gomod_dicts(
    modules: Iterable[GoModule], user_specified_deps_to_replace: set[str]
) -> list[dict[str, Any]]:
    modules_by_name_and_version: dict[tuple[str, str], dict[str, Any]] = {}
    for mod in modules:
        name, version = _get_name_and_version(mod)
        # get the module dict for this name+version or create a new one
        gomodule = modules_by_name_and_version.setdefault(
            (name, version),
            {"type": "gomod", "name": name, "version": version, "replaces": None},
        )
        # report user-specified replacements (note that those must replace to a version, not a path)
        if mod.replace and mod.replace.version and mod.path in user_specified_deps_to_replace:
            gomodule["replaces"] = {"type": "gomod", "name": mod.path, "version": mod.version}
    return [module for _, module in sorted(modules_by_name_and_version.items())]


def _get_name_and_version(module: GoModule) -> tuple[str, str]:
    if not (replace := module.replace):
        name = module.path
        version = module.version
    elif replace.version:
        # module/name v1.0.0 => replace/name v1.2.3
        name = replace.path
        version = replace.version
    else:
        # module/name v1.0.0 => ./local/path
        name = module.path
        version = replace.path
    if not version:
        # should be impossible for modules other than the main module
        # (don't call this function on the main module)
        raise RuntimeError(f"versionless module: {module}")
    return name, version


def _should_vendor_deps(flags: List[str], app_dir: str, strict: bool) -> Tuple[bool, bool]:
    """
    Determine if Cachito should vendor dependencies and if it is allowed to make changes.

    This is based on the presence of flags:
    - gomod-vendor-check => should vendor, can only make changes if vendor dir does not exist
    - gomod-vendor => should vendor, can make changes

    :param flags: flags from the Cachito request
    :param app_dir: absolute path to the app directory
    :param strict: fail the request if the vendor dir is present but the flags are not used?
    :return: (should vendor: bool, allowed to make changes in the vendor directory: bool)
    :raise ValidationError: if the vendor dir is present, the flags are not used and we are strict
    """
    vendor = Path(app_dir) / "vendor"

    if "gomod-vendor-check" in flags:
        return True, not vendor.exists()
    if "gomod-vendor" in flags:
        return True, True

    if strict and vendor.is_dir():
        raise ValidationError(
            'The "gomod-vendor" or "gomod-vendor-check" flag must be set when your repository has '
            "vendored dependencies."
        )

    return False, False


@tracer.start_as_current_span("_vendor_deps")
def _vendor_deps(go: Go, run_params: dict, can_make_changes: bool, git_dir: str) -> list[GoModule]:
    """
    Vendor golang dependencies.

    If Cachito is not allowed to make changes, it will verify that the vendor directory already
    contained the correct content.

    :param run_params: common params for the subprocess calls to `go`
    :param can_make_changes: is Cachito allowed to make changes?
    :param git_dir: path to the repository root
    :raise ValidationError: if vendor directory changed and Cachito is not allowed to make changes
    """
    log.info("Vendoring the gomod dependencies")
    go(["mod", "vendor"], run_params)
    app_dir = run_params["cwd"]
    if not can_make_changes and _vendor_changed(git_dir, app_dir):
        raise ValidationError(
            "The content of the vendor directory is not consistent with go.mod. Run "
            "`go mod vendor` locally to fix this problem. See the logs for more details."
        )
    return _parse_vendor(app_dir)


def _parse_vendor(module_dir: Union[str, Path]) -> list[GoModule]:
    """Parse modules from vendor/modules.txt."""
    modules_txt = Path(module_dir) / "vendor/modules.txt"
    if not modules_txt.exists():
        return []

    def parse_module_line(line: str) -> GoModule:
        parts = line.removeprefix("# ").split()
        # name version
        if len(parts) == 2:
            name, version = parts
            return GoModule(path=name, version=version)
        # name => path
        if len(parts) == 3 and parts[1] == "=>":
            name, _, path = parts
            return GoModule(path=name, replace=GoModule(path=path))
        # name => new_name new_version
        if len(parts) == 4 and parts[1] == "=>":
            name, _, new_name, new_version = parts
            return GoModule(path=name, replace=GoModule(path=new_name, version=new_version))
        # name version => path
        if len(parts) == 4 and parts[2] == "=>":
            name, version, _, path = parts
            return GoModule(path=name, version=version, replace=GoModule(path=path))
        # name version => new_name new_version
        if len(parts) == 5 and parts[2] == "=>":
            name, version, _, new_name, new_version = parts
            return GoModule(
                path=name,
                version=version,
                replace=GoModule(path=new_name, version=new_version),
            )
        raise InvalidFileFormat(f"vendor/modules.txt: unexpected module line format: {line!r}")

    modules: list[GoModule] = []
    module_has_packages: list[bool] = []

    for line in modules_txt.read_text().splitlines():
        if line.startswith("# "):  # module line
            modules.append(parse_module_line(line))
            module_has_packages.append(False)
        elif not line.startswith("#"):  # package line
            if not modules:
                raise InvalidFileFormat(f"vendor/modules.txt: package has no parent module: {line}")
            module_has_packages[-1] = True
        elif not line.startswith("##"):  # marker line
            raise InvalidFileFormat(f"vendor/modules.txt: unexpected format: {line!r}")

    return [module for module, has_packages in zip(modules, module_has_packages) if has_packages]


@tracer.start_as_current_span("_vendor_changed")
def _vendor_changed(git_dir: str, app_dir: str) -> bool:
    """Check for changes in the vendor directory."""
    vendor = Path(app_dir).relative_to(git_dir).joinpath("vendor")
    modules_txt = vendor / "modules.txt"

    repo = git.Repo(git_dir)
    # Add untracked files but do not stage them
    repo.git.add("--intent-to-add", "--force", "--", app_dir)

    try:
        # Diffing modules.txt should catch most issues and produce relatively useful output
        modules_txt_diff = repo.git.diff("--", str(modules_txt))
        if modules_txt_diff:
            log.error("%s changed after vendoring:\n%s", modules_txt, modules_txt_diff)
            return True

        # Show only if files were added/deleted/modified, not the full diff
        vendor_diff = repo.git.diff("--name-status", "--", str(vendor))
        if vendor_diff:
            log.error("%s directory changed after vendoring:\n%s", vendor, vendor_diff)
            return True
    finally:
        repo.git.reset("--", app_dir)

    return False


def _get_allowed_local_deps(module_name: str) -> List[str]:
    """
    Get allowed local dependencies for module.

    If module name contains a version and is not present in the allowlist, also try matching
    without the version. E.g. if example.org/module/v2 is not present in the allowlist, return
    allowed deps for example.org/module.
    """
    allowlist = get_worker_config().cachito_gomod_file_deps_allowlist
    allowed_deps = allowlist.get(module_name)
    if allowed_deps is None:
        versionless_module_name = MODULE_VERSION_RE.sub("", module_name)
        allowed_deps = allowlist.get(versionless_module_name)
    return allowed_deps or []


def _vet_local_deps(
    dependencies: List[dict],
    module_name: str,
    allowed_patterns: List[str],
    app_source_path: str,
    git_dir_path: str,
) -> None:
    """
    Fail if any dependency is replaced by a local path unless the module is allowlisted.

    Also fail if the module is allowlisted but the path is absolute or outside repository.
    """
    for dep in dependencies:
        name = dep["name"]
        version = dep["version"]

        if not version:
            continue  # go stdlib

        if version.startswith("."):
            log.debug(
                "Module %s wants to replace %s with a local dependency: %s",
                module_name,
                name,
                version,
            )
            _fail_unless_allowed(module_name, name, allowed_patterns)
            _validate_local_dependency_path(app_source_path, git_dir_path, version)
        elif version.startswith("/") or PureWindowsPath(version).root:
            # This will disallow paths starting with '/', '\' or '<drive letter>:\'
            raise UnsupportedFeature(
                f"Absolute paths to gomod dependencies are not supported: {version}"
            )


def _validate_local_dependency_path(app_source_path: str, git_dir_path: str, dep_path: str) -> None:
    """
    Validate that the local dependency path is not outside the repository.

    :param str app_source_path: the full path to the application source code
    :param str git_dir_path: the full path to the git repository
    :param str dep_path: the relative path for local replacements (the dep version)
    :raise ValidationError: if the local dependency path is invalid
    """
    try:
        resolved_dep_path = Path(app_source_path, dep_path).resolve()
        resolved_dep_path.relative_to(Path(git_dir_path).resolve())
    except ValueError:
        raise ValidationError(f"The local dependency path {dep_path} is outside the repository")


def _fail_unless_allowed(module_name: str, package_name: str, allowed_patterns: List[str]):
    """
    Fail unless the module is allowed to replace the package with a local dependency.

    When packages are allowed to be replaced:
    * package_name is a submodule of module_name
    * package_name replacement is allowed according to allowed_patterns
    """
    versionless_module_name = MODULE_VERSION_RE.sub("", module_name)
    is_submodule = contains_package(versionless_module_name, package_name)
    if not is_submodule and not any(fnmatch.fnmatch(package_name, pat) for pat in allowed_patterns):
        raise UnsupportedFeature(
            f"The module {module_name} is not allowed to replace {package_name} with a local "
            f"dependency. Please contact the maintainers of this Cachito instance about adding "
            "an exception."
        )


def _set_full_local_dep_relpaths(pkg_deps: List[dict], main_module_deps: List[dict]):
    """
    Set full relative paths for all local go-package dependencies.

    The path that you see in the go list -deps output points only to the module that contains
    the package. To get the full path to the package, take the relative path from the module
    to the package (based on the package name relative to the module name) and join it with the
    module path.
    """
    locally_replaced_mod_names = [
        module["name"] for module in main_module_deps if module["version"].startswith(".")
    ]

    for dep in pkg_deps:
        dep_name = dep["name"]
        dep_path = dep["version"]

        if not dep_path or not dep_path.startswith("."):
            continue

        # The gomod module that contains this go-package dependency
        dep_module_name = match_parent_module(dep_name, locally_replaced_mod_names)
        if dep_module_name is None:
            # This should be impossible
            raise RuntimeError(f"Could not find parent Go module for local dependency: {dep_name}")

        path_from_module_to_pkg = path_to_subpackage(dep_module_name, dep_name)
        if path_from_module_to_pkg:
            dep["version"] = os.path.join(dep_path, path_from_module_to_pkg)


@tracer.start_as_current_span("_merge_bundle_dirs")
def _merge_bundle_dirs(root_src_dir, root_dst_dir):
    """
    Merge two bundle directories together.

    The contents of root_src_dir will be copied into root_dst_dir, overwriting any files
    that might already be present. For a description of the algorithm, see
    https://lukelogbook.tech/2018/01/25/merging-two-folders-in-python/

    In addition to that merge algorithm, however, we also need to make sure that we merge
    the list file to ensure all versions are represented. In order to protect against merging
    extra files, we are also checking for the presence of the list.lock file since it should
    be present according to https://github.com/golang/go/issues/29434

    :param str root_src_dir: the root path to the source directory
    :param str root_dst_dir: the root path to the destination directory
    :return: None
    """
    for src_dir, dirs, files in os.walk(root_src_dir):
        dst_dir = src_dir.replace(root_src_dir, root_dst_dir, 1)
        if not os.path.exists(dst_dir):
            os.makedirs(dst_dir)
        for file_ in files:
            src_file = os.path.join(src_dir, file_)
            dst_file = os.path.join(dst_dir, file_)
            if os.path.exists(dst_file):
                # check to see if we are trying to merge the `list` file
                # since we have to treat that seperately. We don't want to
                # delete it or overwrite it -- we need to merge it.
                if (
                    file_ == "list"
                    and os.path.isfile(src_file)
                    and os.path.exists("{}.lock".format(src_file))
                ):
                    _merge_files(src_file, dst_file)
                continue
            shutil.copy2(src_file, dst_dir)


def _merge_files(src_file, dst_file):
    """
    Merge two files so that we ensure that all packages are represented.

    The dst_file will be updated by inserting the lines from the src_file,
    sorting all lines, and removing duplicate lines.

    :param str src_file: the source file (to be merged)
    :param str dst_file: the destination file (to be merged into)
    :return: None
    """
    with open(src_file, "r") as file1:
        source_content = [line.rstrip() for line in file1.readlines()]
    with open(dst_file, "r") as file2:
        dest_content = [line.rstrip() for line in file2.readlines()]

    with open(dst_file, "w") as target:
        for line in sorted(set(source_content + dest_content)):
            if line == "":
                continue
            target.write(str(line) + "\n")


def _get_golang_pseudo_version(commit, tag=None, module_major_version=None, subpath=None):
    """
    Get the Go module's pseudo-version when a non-version commit is used.

    For a description of the algorithm, see https://tip.golang.org/cmd/go/#hdr-Pseudo_versions.

    :param git.Commit commit: the commit object of the Go module
    :param git.Tag tag: the highest semantic version tag with a matching major version before the
        input commit. If this isn't specified, it is assumed there was no previous valid tag.
    :param int module_major_version: the Go module's major version as stated in its go.mod file. If
        this and "tag" are not provided, 0 is assumed.
    :param str subpath: path to the module, relative to the root repository folder
    :return: the Go module's pseudo-version as returned by `go list`
    :rtype: str
    """
    # Use this instead of commit.committed_datetime so that the datetime object is UTC
    committed_dt = datetime.utcfromtimestamp(commit.committed_date)
    commit_timestamp = committed_dt.strftime(r"%Y%m%d%H%M%S")
    commit_hash = commit.hexsha[0:12]

    # vX.0.0-yyyymmddhhmmss-abcdefabcdef is used when there is no earlier versioned commit with an
    # appropriate major version before the target commit
    if tag is None:
        # If the major version isn't in the import path and there is not a versioned commit with the
        # version of 1, the major version defaults to 0.
        return f'v{module_major_version or "0"}.0.0-{commit_timestamp}-{commit_hash}'

    tag_semantic_version = _get_semantic_version_from_tag(tag.name, subpath)

    # An example of a semantic version with a prerelease is v2.2.0-alpha
    if tag_semantic_version.prerelease:
        # vX.Y.Z-pre.0.yyyymmddhhmmss-abcdefabcdef is used when the most recent versioned commit
        # before the target commit is vX.Y.Z-pre
        version_seperator = "."
        pseudo_semantic_version = tag_semantic_version
    else:
        # vX.Y.(Z+1)-0.yyyymmddhhmmss-abcdefabcdef is used when the most recent versioned commit
        # before the target commit is vX.Y.Z
        version_seperator = "-"
        pseudo_semantic_version = tag_semantic_version.bump_patch()

    return f"v{pseudo_semantic_version}{version_seperator}0.{commit_timestamp}-{commit_hash}"


@tracer.start_as_current_span("_get_highest_semver_tag")
def _get_highest_semver_tag(repo, target_commit, major_version, all_reachable=False, subpath=None):
    """
    Get the highest semantic version tag related to the input commit.

    :param Git.Repo repo: the Git repository object to search
    :param int major_version: the major version of the Go module as in the go.mod file to use as a
        filter for major version tags
    :param bool all_reachable: if False, the search is constrained to the input commit. If True,
        then the search is constrained to the input commit and preceding commits.
    :param str subpath: path to the module, relative to the root repository folder
    :return: the highest semantic version tag if one is found
    :rtype: git.Tag
    """
    try:
        g = git.Git(repo.working_dir)
        if all_reachable:
            # Get all the tags on the input commit and all that precede it.
            # This is based on:
            # https://github.com/golang/go/blob/0ac8739ad5394c3fe0420cf53232954fefb2418f/src/cmd/go/internal/modfetch/codehost/git.go#L659-L695
            cmd = [
                "git",
                "for-each-ref",
                "--format",
                "%(refname:lstrip=2)",
                "refs/tags",
                "--merged",
                target_commit.hexsha,
            ]
        else:
            # Get the tags that point to this commit
            cmd = ["git", "tag", "--points-at", target_commit.hexsha]
        tag_names = g.execute(cmd).splitlines()
    except git.GitCommandError:
        msg = f"Failed to get the tags associated with the reference {target_commit.hexsha}"
        log.exception(msg)
        raise RepositoryAccessError(msg)

    # Keep only semantic version tags related to the path being processed
    prefix = f"{subpath}/v" if subpath else "v"
    filtered_tags = [tag_name for tag_name in tag_names if tag_name.startswith(prefix)]

    not_semver_tag_msg = "%s is not a semantic version tag"
    highest = None

    for tag_name in filtered_tags:
        try:
            semantic_version = _get_semantic_version_from_tag(tag_name, subpath)
        except ValueError:
            log.debug(not_semver_tag_msg, tag_name)
            continue

        # If the major version of the semantic version tag doesn't match the Go module's major
        # version, then ignore it
        if semantic_version.major != major_version:
            continue

        if highest is None or semantic_version > highest["semver"]:
            highest = {"tag": tag_name, "semver": semantic_version}

    if highest:
        return repo.tags[highest["tag"]]

    return None


def _get_semantic_version_from_tag(tag_name, subpath=None):
    """
    Parse a version tag to a semantic version.

    A Go version follows the format "v0.0.0", but it needs to have the "v" removed in
    order to be properly parsed by the semver library.

    In case `subpath` is defined, it will be removed from the tag_name, e.g. `subpath/v0.1.0`
    will be parsed as `0.1.0`.

    :param str tag_name: tag to be converted into a semver object
    :param str subpath: path to the module, relative to the root repository folder
    :rtype: semver.VersionInfo
    """
    if subpath:
        semantic_version = tag_name.replace(f"{subpath}/v", "")
    else:
        semantic_version = tag_name[1:]

    return semver.VersionInfo.parse(semantic_version)


@tracer.start_as_current_span("get_golang_version")
def get_golang_version(module_name, git_path, commit_sha, update_tags=False, subpath=None):
    """
    Get the version of the Go module in the input Git repository in the same format as `go list`.

    If commit doesn't point to a commit with a semantically versioned tag, a pseudo-version
    will be returned.

    :param str module_name: the Go module's name
    :param str git_path: the path to the Git repository
    :param str commit_sha: the Git commit SHA1 of the Go module to get the version for
    :param bool update_tags: determines if `git fetch --tags --force` should be run before
        determining the version. If this fails, it will be logged as a warning.
    :param str subpath: path to the module, relative to the root repository folder
    :return: a version as `go list` would provide
    :rtype: str
    :raises RepositoryAccessError: if failed to fetch the tags on the Git repository
    """
    # If the module is version v2 or higher, the major version of the module is included as /vN at
    # the end of the module path. If the module is version v0 or v1, the major version is omitted
    # from the module path.
    module_major_version = None
    match = re.match(r"(?:.+/v)(?P<major_version>\d+)$", module_name)
    if match:
        module_major_version = int(match.groupdict()["major_version"])

    repo = git.Repo(git_path)
    if update_tags:
        try:
            repo.remote().fetch(force=True, tags=True)
        except Exception as ex:
            raise RepositoryAccessError(
                "Failed to fetch the tags on the Git repository (%s) for %s ",
                type(ex).__name__,
                module_name,
            )

    if module_major_version:
        major_versions_to_try = (module_major_version,)
    else:
        # Prefer v1.x.x tags but fallback to v0.x.x tags if both are present
        major_versions_to_try = (1, 0)

    commit = repo.commit(commit_sha)
    for major_version in major_versions_to_try:
        # Get the highest semantic version tag on the commit with a matching major version
        tag_on_commit = _get_highest_semver_tag(repo, commit, major_version, subpath=subpath)
        if not tag_on_commit:
            continue

        log.debug(
            "Using the semantic version tag of %s for commit %s", tag_on_commit.name, commit_sha
        )

        # We want to preserve the version in the "v0.0.0" format, so the subpath is not needed
        return tag_on_commit.name if not subpath else tag_on_commit.name.replace(f"{subpath}/", "")

    log.debug("No semantic version tag was found on the commit %s", commit_sha)

    # This logic is based on:
    # https://github.com/golang/go/blob/a23f9afd9899160b525dbc10d01045d9a3f072a0/src/cmd/go/internal/modfetch/coderepo.go#L511-L521
    for major_version in major_versions_to_try:
        # Get the highest semantic version tag before the commit with a matching major version
        pseudo_base_tag = _get_highest_semver_tag(
            repo, commit, major_version, all_reachable=True, subpath=subpath
        )
        if not pseudo_base_tag:
            continue

        log.debug(
            "Using the semantic version tag of %s as the pseudo-base for the commit %s",
            pseudo_base_tag.name,
            commit_sha,
        )
        pseudo_version = _get_golang_pseudo_version(
            commit, pseudo_base_tag, major_version, subpath=subpath
        )
        log.debug("Using the pseudo-version %s for the commit %s", pseudo_version, commit_sha)
        return pseudo_version

    log.debug("No valid semantic version tag was found")
    # Fall-back to a vX.0.0-yyyymmddhhmmss-abcdefabcdef pseudo-version
    return _get_golang_pseudo_version(
        commit, module_major_version=module_major_version, subpath=subpath
    )


def _get_gomod_version(source_dir: Path) -> Optional[str]:
    """Return the required/recommended version of Go from go.mod.

    We need to extract the desired version of Go ourselves as older versions of Go might fail
    due to e.g. unknown keywords or unexpected format of the version (yes, Go always performs
    validation of go.mod).

    If we cannot extract a version from the 'go' line, we return None, leaving it up to the caller
    to decide what to do next.
    """
    go_mod = source_dir / "go.mod"
    with open(go_mod) as f:
        reg = re.compile(r"^\s*go\s+(?P<ver>\d\.\d+(:?.\d+)?)\s*$")
        for line in f:
            if match := re.match(reg, line):
                return match.group("ver")
    return None


def _select_go_toolchain(source_dir: Path) -> Go:
    go = Go()
    go1_21 = pkgver.Version("1.21")
    go_base_version = go.version
    go_mod_version_msg = "go.mod recommends/requires Go version: {}"

    if (modfile_version := _get_gomod_version(source_dir)) is None:
        # Go added the 'go' directive to go.mod in 1.12 [1]. If missing, 1.16 is assumed [2].
        # For our version comparison purposes we set the version explicitly to 1.20 if missing.
        # [1] https://go.dev/doc/go1.12#modules
        # [2] https://go.dev/ref/mod#go-mod-file-go
        modfile_version = "1.20"
        go_mod_version_msg += " " + "(cachito enforced)"

    go_mod_version = pkgver.Version(modfile_version)

    log.info(go_mod_version_msg.format(go_mod_version))

    if go_mod_version >= go1_21 and go_base_version < go1_21:
        # our base Go installation is too old and we need a newer one to support new keywords
        go = Go(release="go1.21.0")
    elif go_mod_version < go1_21 and go_base_version >= go1_21:
        # Starting with Go 1.21, Go doesn't try to be semantically backwards compatible in that
        # the 'go X.Y' line now denotes the minimum required version of Go, no a "suggested"
        # version. What it means in practice is that a Go toolchain >= 1.21 enforces the
        # biggest common toolchain denominator across all dependencies and so if the input
        # project specifies e.g. 'go 1.19' and **any** of its dependencies specify 'go 1.21'
        # (or higher), then the default 1.21 toolchain will bump the input project's go.mod
        # file to make sure the minimum required Go version is met across all dependencies.
        # That is a problem, because it'll lead to fatal build failures forcing everyone to
        # update their build recipes. Note that at some point they'll have to do that anyway,
        # but until majority of projects in the ecosystem adopt 1.21, we need a fallback to an
        # older toolchain version.
        go = Go(release="go1.20")
    return go
