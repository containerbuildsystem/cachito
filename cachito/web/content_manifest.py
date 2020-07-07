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

    def process_go_package(self, package, dependency):
        """
        Process gomod or go-package package.

        :param Package package: the gomod or go-package package to process
        :param Dependency dependency: the gomod or go-package package dependency to process
        """
        purl = package.to_purl()
        if dependency.type == "go-package":
            icm_dependency = {"purl": dependency.to_purl()}
            self._gopkg_data[purl]["dependencies"].append(icm_dependency)
        elif dependency.type == "gomod":
            icm_source = {"purl": dependency.to_purl()}
            self._gopkg_data[purl]["sources"].append(icm_source)

    def to_json(self):
        """
        Generate the JSON representation of the content manifest.

        :return: the JSON form of the ContentManifest object
        :rtype: dict
        """
        self._gopkg_data = {}

        # Address the possibility of packages having no dependencies
        for package in self.request.packages:
            if package.type in ("gomod", "go-package"):
                purl = package.to_purl()
                self._gopkg_data.setdefault(purl, {"purl": purl, "dependencies": [], "sources": []})
            else:
                flask.current_app.logger.debug(
                    "No ICM implementation for '%s' packages", package.type
                )

        for req_dep in self.request.request_dependencies:
            if req_dep.package.type in ("gomod", "go-package"):
                self.process_go_package(req_dep.package, req_dep.dependency)

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
