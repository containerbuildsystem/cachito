# SPDX-License-Identifier: GPL-3.0-or-later
import flask


class ContentManifest:
    """A content manifest associated with a Cacihto request."""

    version = 1
    json_schema_url = (
        "https://raw.githubusercontent.com/containerbuildsystem/atomic-reactor/"
        "f4abcfdaf8247a6b074f94fa84f3846f82d781c6/atomic_reactor/schemas/content_manifest.json"
    )
    unknown_layer_index = -1

    def __init__(self, request=None):
        """
        Initialize ContentManifest.

        :param Request request: the request to generate a ContentManifest for
        """
        self.request = request
        # dict to store go package level data; uses the package purl as key to identify a package
        self._gopkg_data = {}
        # dict to store go module level purl dependencies. Module names are used as keys
        self._gomod_data = {}

    def process_gomod(self, package, dependency):
        """
        Process gomod package.

        :param Package package: the gomod package to process
        :param Dependency dependency: the gomod package dependency to process
        """
        if dependency.type == "gomod":
            icm_source = {"purl": dependency.to_purl()}
            self._gomod_data[package.name].append(icm_source)

    def process_go_package(self, package, dependency):
        """
        Process go-package package.

        :param Package package: the go-package package to process
        :param Dependency dependency: the go-package package dependency to process
        """
        purl = package.to_purl()
        if dependency.type == "go-package":
            icm_dependency = {"purl": dependency.to_purl()}
            self._gopkg_data[purl]["dependencies"].append(icm_dependency)

    def set_go_package_sources(self):
        """
        Adjust source level dependencies for go packages.

        Go packages are not related to Go modules in cachito's DB. However, Go
        sources are retreived in a per module basis. To set the proper source
        in each content manifest entry, we associate each Go package to a Go
        module based on their names.
        """
        for purl, pkg_data in self._gopkg_data.items():
            if pkg_data["name"] in self._gomod_data:
                self._gopkg_data[purl]["sources"] = self._gomod_data[pkg_data["name"]]
            else:
                # We use the longest module available in the request that matches the package name
                previous_length = 0
                for mod_name, sources in self._gomod_data.items():
                    if pkg_data["name"].startswith(mod_name) and len(mod_name) > previous_length:
                        self._gopkg_data[purl]["sources"] = sources
                        previous_length = len(mod_name)

                if not previous_length:
                    flask.current_app.logger.warning("Could not find a Go module for %s", purl)
            pkg_data.pop("name")

    def to_json(self):
        """
        Generate the JSON representation of the content manifest.

        :return: the JSON form of the ContentManifest object
        :rtype: dict
        """
        self._gopkg_data = {}
        self._gomod_data = {}

        # Address the possibility of packages having no dependencies
        for package in self.request.packages:
            if package.type == "go-package":
                purl = package.to_purl()
                self._gopkg_data.setdefault(
                    purl, {"name": package.name, "purl": purl, "dependencies": [], "sources": []}
                )
            elif package.type == "gomod":
                self._gomod_data.setdefault(package.name, [])
            else:
                flask.current_app.logger.debug(
                    "No ICM implementation for '%s' packages", package.type
                )

        for req_dep in self.request.request_dependencies:
            if req_dep.package.type == "go-package":
                self.process_go_package(req_dep.package, req_dep.dependency)
            if req_dep.package.type == "gomod":
                self.process_gomod(req_dep.package, req_dep.dependency)

        # Adjust source level dependencies for go packages
        self.set_go_package_sources()

        top_level_packages = list(self._gopkg_data.values())
        return self.generate_icm(top_level_packages)

    def generate_icm(self, image_contents=None):
        """
        Generate a content manifest with the given image contents.

        :param list image_contents: List with components for the ICM's ``image_contents`` field
        :return: a valid Image Content Manifest
        :rtype: dict
        """
        icm = {
            "metadata": {
                "icm_version": self.version,
                "icm_spec": self.json_schema_url,
                "image_layer_index": self.unknown_layer_index,
            },
        }
        icm["image_contents"] = image_contents or []

        return icm
