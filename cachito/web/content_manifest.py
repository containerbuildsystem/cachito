# SPDX-License-Identifier: GPL-3.0-or-later
import os
import re
import urllib.parse
from copy import deepcopy
from typing import List, Optional

import flask
import pkg_resources

from cachito.errors import ContentManifestError
from cachito.web.utils import deep_sort_icm
from cachito.workers.pkg_managers import gomod

PARENT_PURL_PLACEHOLDER = "PARENT_PURL"

VERSION = 1
JSON_SCHEMA_URL = (
    "https://raw.githubusercontent.com/containerbuildsystem/atomic-reactor/"
    "f4abcfdaf8247a6b074f94fa84f3846f82d781c6/atomic_reactor/schemas/content_manifest.json"
)
UNKNOWN_LAYER_INDEX = -1

# A base (empty) image content manifest which will be used as a template to
# fill in the generated image_contents.
# NOTE THAT, the image_contents must be filled in a copy of this base icm.
BASE_ICM = {
    "metadata": {
        "icm_version": VERSION,
        "icm_spec": JSON_SCHEMA_URL,
        "image_layer_index": UNKNOWN_LAYER_INDEX,
    },
    "image_contents": [],
}


class ContentManifest:
    """A content manifest associated with a Cachito request."""

    def __init__(self, request, packages):
        """
        Initialize ContentManifest.

        :param Request request: the request to generate a ContentManifest for
        :param list[Package] packages: the packages contained in the request
        """
        self.request = request
        self.packages = packages
        # dict to store go package level data; uses the package id as key to identify a package
        self._gopkg_data = {}
        # dict to store go module level purl dependencies. Module names are used as keys
        self._gomod_data = {}
        # dict to store npm package data; uses the package id as key to identify a package
        self._npm_data = {}
        # dict to store pip package data; uses the package id as key to identify a package
        self._pip_data = {}
        # dict to store yarn package data; uses the package id as key to identify a package
        self._yarn_data = {}
        # dict to store gitsubmodule package level data; uses the package id as key to identify a
        # package
        self._gitsubmodule_data = {}

    def process_gomod(self, package, dependency):
        """
        Process gomod package.

        :param Package package: the gomod package to process
        :param Dependency dependency: the gomod package dependency to process
        """
        if dependency.type == "gomod":
            parent_purl = self._gomod_data[package.name]["purl"]
            dep_purl = dependency.to_purl().replace(PARENT_PURL_PLACEHOLDER, parent_purl)
            icm_source = {"purl": dep_purl}
            self._gomod_data[package.name]["dependencies"].append(icm_source)

    def process_go_package(self, package, dependency):
        """
        Process go-package package.

        :param Package package: the go-package package to process
        :param Dependency dependency: the go-package package dependency to process
        """
        if dependency.type == "go-package":
            icm_dependency = {"purl": dependency.to_purl()}
            self._gopkg_data[package]["dependencies"].append(icm_dependency)

    def set_go_package_sources(self):
        """
        Adjust source level dependencies for go packages.

        Go packages are not related to Go modules in cachito's DB. However, Go
        sources are retreived in a per module basis. To set the proper source
        in each content manifest entry, we associate each Go package to a Go
        module based on their names.
        """
        for package_id, pkg_data in self._gopkg_data.items():
            pkg_name = pkg_data.pop("name")

            if pkg_name in self._gomod_data:
                module_name = pkg_name
            else:
                module_name = gomod.match_parent_module(pkg_name, self._gomod_data.keys())

            if module_name is not None:
                module = self._gomod_data[module_name]
                self._gopkg_data[package_id]["sources"] = module["dependencies"]
                self._replace_parent_purl_gopkg(self._gopkg_data[package_id], module["purl"])
            else:
                flask.current_app.logger.warning(
                    "Could not find a Go module for %s", pkg_data["purl"]
                )

    def _replace_parent_purl_gopkg(self, go_pkg: dict, module_purl: str):
        """
        Replace PARENT_PURL_PLACEHOLDER in go-package dependencies with the parent module purl.

        The purl of the package itself cannot contain a placeholder. The purls of all of its
        sources will have been replaced at this point already (they come from the parent module).
        Only dependencies need to be replaced here.
        """
        for dep in go_pkg["dependencies"]:
            dep["purl"] = dep["purl"].replace(PARENT_PURL_PLACEHOLDER, module_purl)

    def process_npm_package(self, package, dependency):
        """
        Process npm package.

        :param Package package: the npm package to process
        :param Dependency dependency: the npm package dependency to process
        """
        if dependency.type == "npm":
            self._process_standard_package("npm", package, dependency)

    def process_pip_package(self, package, dependency):
        """
        Process pip package.

        :param Package package: the pip package to process
        :param Dependency dependency: the pip package dependency to process
        """
        if dependency.type == "pip":
            self._process_standard_package("pip", package, dependency)

    def process_yarn_package(self, package, dependency):
        """
        Process yarn package.

        :param Package package: the yarn package to process
        :param Dependency dependency: the yarn package dependency to process
        """
        if dependency.type == "yarn":
            self._process_standard_package("yarn", package, dependency)

    def _process_standard_package(self, pkg_type, package, dependency):
        """
        Process a standard package (standard = does not require the same magic as go packages).

        Currently, all package types except for gomod and go-package are standard.
        """
        pkg_type_data = getattr(self, f"_{pkg_type}_data")

        icm_dependency = {"purl": dependency.to_purl()}
        pkg_type_data[package]["sources"].append(icm_dependency)
        if not dependency.dev:
            pkg_type_data[package]["dependencies"].append(icm_dependency)

    def to_json(self):
        """
        Generate the JSON representation of the content manifest.

        :return: the JSON form of the ContentManifest object
        :rtype: OrderedDict
        """
        self._gopkg_data = {}
        self._gomod_data = {}
        self._npm_data = {}
        self._pip_data = {}
        self._yarn_data = {}
        self._gitsubmodule_data = {}

        for package in self.packages:

            if package.type == "go-package":
                purl = package.to_top_level_purl(self.request, subpath=package.path)
                self._gopkg_data.setdefault(
                    package,
                    {"name": package.name, "purl": purl, "dependencies": [], "sources": []},
                )
            elif package.type == "gomod":
                purl = package.to_top_level_purl(self.request, subpath=package.path)
                self._gomod_data.setdefault(package.name, {"purl": purl, "dependencies": []})
            elif package.type in ("npm", "pip", "yarn"):
                purl = package.to_top_level_purl(self.request, subpath=package.path)
                data = getattr(self, f"_{package.type}_data")
                data.setdefault(package, {"purl": purl, "dependencies": [], "sources": []})
            elif package.type == "git-submodule":
                purl = package.to_top_level_purl(self.request, subpath=package.path)
                self._gitsubmodule_data.setdefault(
                    package, {"purl": purl, "dependencies": [], "sources": []}
                )
            else:
                flask.current_app.logger.debug(
                    "No ICM implementation for '%s' packages", package.type
                )

        for package in self.packages:
            for dependency in package.dependencies:
                if package.type == "go-package":
                    self.process_go_package(package, dependency)
                elif package.type == "gomod":
                    self.process_gomod(package, dependency)
                elif package.type == "npm":
                    self.process_npm_package(package, dependency)
                elif package.type == "pip":
                    self.process_pip_package(package, dependency)
                elif package.type == "yarn":
                    self.process_yarn_package(package, dependency)

        # Adjust source level dependencies for go packages
        self.set_go_package_sources()

        top_level_packages = [
            *self._gopkg_data.values(),
            *self._npm_data.values(),
            *self._pip_data.values(),
            *self._yarn_data.values(),
            *self._gitsubmodule_data.values(),
        ]
        return self.generate_icm(top_level_packages)

    def generate_icm(self, image_contents=None):
        """
        Generate a content manifest with the given image contents.

        :param list image_contents: List with components for the ICM's ``image_contents`` field
        :return: a valid Image Content Manifest
        :rtype: OrderedDict
        """
        icm = deepcopy(BASE_ICM)
        icm["image_contents"] = image_contents or []
        return deep_sort_icm(icm)


class Package:
    """
    A package within a content manifest.

    It is used primarily to generate a package URL (purl).
    """

    __slots__ = ("name", "type", "version", "dev", "dependencies", "path")

    def __init__(
        self,
        name: str,
        type: str,
        version: str,
        dev: bool = False,
        path: Optional[str] = None,
        dependencies: Optional[List] = None,
    ):
        """Initialize package data."""
        self.name = name
        self.type = type
        self.version = version
        self.dev = dev
        self.dependencies = [] if dependencies is None else dependencies
        self.path = path

    def __repr__(self):
        return (
            f"<{self.__class__.__name__} name={self.name}, type={self.type}, "
            f"version={self.version}>"
        )

    def __hash__(self):
        return hash((self.name, self.type, self.version, self.dev))

    def __eq__(self, other):
        return (
            isinstance(other, Package)
            and self.name == other.name
            and self.type == other.type
            and self.version == other.version
            and self.dev == other.dev
        )

    @classmethod
    def from_json(cls, package):
        """
        Create a Package object from JSON.

        All dependencies will also be converted to Package objects.

        :param dict package: the dictionary representing the package
        :return: the Package object
        :rtype: Package
        """
        dependencies = [
            Package.from_json(dependency) for dependency in package.get("dependencies", [])
        ]

        return cls(
            name=package["name"],
            type=package["type"],
            version=package["version"],
            dev=package.get("dev", False),
            dependencies=dependencies,
            path=package.get("path"),
        )

    def to_purl(self):
        """
        Generate the PURL representation of the package.

        :return: the PURL string of the Package object
        :rtype: str
        :raise ContentManifestError: if the there is no implementation for the package type
        """
        if self.type in ("go-package", "gomod"):
            if self.version.startswith("."):
                # Package is relative to the parent module
                normpath = os.path.normpath(self.version)
                return f"{PARENT_PURL_PLACEHOLDER}#{normpath}"

            # Use only the PURL "name" field to avoid ambiguity for Go modules/packages
            # see https://github.com/package-url/purl-spec/issues/63 for further reference
            purl_name = urllib.parse.quote(self.name, safe="")
            return f"pkg:golang/{purl_name}@{self.version}"

        elif self.type == "npm" or self.type == "yarn":
            purl_name = urllib.parse.quote(self.name)
            match = re.match(
                r"(?P<protocol>[^:]+):(?P<has_authority>//)?(?P<suffix>.+)", self.version
            )
            if not match:
                return f"pkg:npm/{purl_name}@{self.version}"
            protocol = match.group("protocol")
            suffix = match.group("suffix")
            has_authority = match.group("has_authority")
            if protocol == "file":
                qualifier = urllib.parse.quote(self.version, safe="")
                return f"generic/{purl_name}?{qualifier}"
            elif not has_authority:
                # github:namespace/name#ref or gitlab:ns1/ns2/name#ref
                match_forge = re.match(
                    r"(?P<namespace>.+)/(?P<name>[^#/]+)#(?P<version>.+)$", suffix
                )
                if not match_forge:
                    raise ContentManifestError(f"Could not convert version {self.version} to purl")
                forge = match_forge.groupdict()
                return f"pkg:{protocol}/{forge['namespace']}/{forge['name']}@{forge['version']}"
            elif protocol in ("git", "git+http", "git+https", "git+ssh"):
                qualifier = urllib.parse.quote(self.version, safe="")
                return f"pkg:generic/{purl_name}?vcs_url={qualifier}"
            elif protocol in ("http", "https"):
                qualifier = urllib.parse.quote(self.version, safe="")
                return f"pkg:generic/{purl_name}?download_url={qualifier}"
            else:
                raise ContentManifestError(
                    f"Unknown protocol in {self.type} package version: {self.version}"
                )

        elif self.type == "pip":
            # As per the purl spec, PyPI names should be normalized by lowercasing and
            # converting '_' to '-'. The safe_name() function does the latter but not the
            # former. It is not necessary to escape characters in the name, safe_name()
            # also replaces everything except alphanumeric chars and '.' with '-'.
            name = pkg_resources.safe_name(self.name.lower())
            parsed_url = urllib.parse.urlparse(self.version)

            if not parsed_url.scheme:
                # Version is a PyPI version string
                return f"pkg:pypi/{name}@{self.version}"
            elif parsed_url.scheme.startswith("git+"):
                # Version is git+<git_url>
                scheme = parsed_url.scheme[len("git+") :]
                vcs_url = f"{scheme}://{parsed_url.netloc}{parsed_url.path}"
                repo_url, ref = vcs_url.rsplit("@", 1)
                return self.to_vcs_purl(repo_url, ref)
            else:
                # Version is a plain URL
                fragments = urllib.parse.parse_qs(parsed_url.fragment)
                checksum = fragments["cachito_hash"][0]
                quoted_url = urllib.parse.quote(self.version, safe="")
                return f"pkg:generic/{name}?download_url={quoted_url}&checksum={checksum}"

        elif self.type == "git-submodule":
            # Version is a submodule repository url followed by `#` separator and
            # `submodule-commit-ref`, e.g.
            # https://github.com/org-name/submodule-name.git#522fb816eec295ad58bc488c74b2b46748d471b2
            repo_url, ref = self.version.rsplit("#", 1)
            return self.to_vcs_purl(repo_url, ref)

        else:
            raise ContentManifestError(f"The PURL spec is not defined for {self.type} packages")

    def to_vcs_purl(self, repo_url, ref):
        """
        Generate the vcs purl representation of the package.

        Use the most specific purl type possible, e.g. pkg:github if repo comes from
        github.com. Fall back to using pkg:generic with a ?vcs_url qualifier.

        :param str repo_url: url of git repository for package
        :param str ref: git ref of package
        :return: the PURL string of the Package object
        :rtype: str
        """
        repo_url = repo_url.rstrip("/")
        parsed_url = urllib.parse.urlparse(repo_url)

        pkg_type_for_hostname = {
            "github.com": "github",
            "bitbucket.org": "bitbucket",
        }
        pkg_type = pkg_type_for_hostname.get(parsed_url.hostname, "generic")

        if pkg_type == "generic":
            vcs_url = urllib.parse.quote(f"{repo_url}@{ref}", safe="")
            purl = f"pkg:generic/{self.name}?vcs_url={vcs_url}"
        else:
            # pkg:github and pkg:bitbucket use the same format
            namespace, repo = parsed_url.path.lstrip("/").rsplit("/", 1)
            if repo.endswith(".git"):
                repo = repo[: -len(".git")]
            purl = f"pkg:{pkg_type}/{namespace.lower()}/{repo.lower()}@{ref}"

        return purl

    def to_top_level_purl(self, request, subpath=None):
        """
        Generate the purl representation of a top-level package (not a dependency).

        In Cachito, all top-level packages come from the git repository that the user
        requested. Generate a purl that properly conveys this information.

        The relation between Package and Request is many-to-many, therefore the caller
        must specify the request to use when generating the purl.

        :param Request request: the request that contains this package
        :param str subpath: relative path to package from root of repository
        :return: the PURL string of the Package object
        :rtype: str
        """
        if self.type in ("gomod", "go-package", "git-submodule"):
            purl = self.to_purl()
            # purls for git submodules point to a different repo, path is neither needed nor valid
            # golang package and module names should reflect the path already
            include_path = False
        elif self.type in ("npm", "pip", "yarn"):
            purl = self.to_vcs_purl(request.repo, request.ref)
            include_path = True
        else:
            raise ContentManifestError(f"{self.type!r} is not a valid top level package")

        if subpath and include_path:
            purl = f"{purl}#{subpath}"

        return purl
