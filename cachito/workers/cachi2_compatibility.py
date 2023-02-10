import logging
from pathlib import Path
from typing import Any, Literal, Optional

import pydantic
from cachi2.core import config as cachi2_config
from cachi2.core import errors as cachi2_errors
from cachi2.core.models.input import Request as Cachi2Request
from cachi2.core.models.output import Dependency, Package, PipDependency, RequestOutput
from cachi2.core.package_managers import gomod, pip

from cachito.common.packages_data import PackagesData
from cachito.errors import (
    CachitoError,
    ClientError,
    GoModError,
    InvalidFileFormat,
    InvalidRepoStructure,
    NetworkError,
    ServerError,
    UnsupportedFeature,
    ValidationError,
    WorkerConfigError,
)
from cachito.workers.config import Config
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers.general import update_request_env_vars

__all__ = ["Cachi2Adapter"]

log = logging.getLogger(__name__)

JSONObject = dict[str, Any]


def set_cachi2_config(worker_config) -> None:
    # it's not actually a Config object, but close enough
    cachito_config: Config = worker_config
    try:
        config = cachi2_config.Config(
            default_environment_variables=cachito_config.cachito_default_environment_variables,
            gomod_download_max_tries=cachito_config.cachito_gomod_download_max_tries,
            gomod_strict_vendor=cachito_config.cachito_gomod_strict_vendor,
            goproxy_url=cachito_config.cachito_athens_url,
            subprocess_timeout=cachito_config.cachito_subprocess_timeout,
        )
    except pydantic.ValidationError:
        log.exception("Failed to set configuration for the Cachi2 backend")
        raise WorkerConfigError("Failed to set configuration for the Cachi2 backend")

    cachi2_config.set_config(config)


class Cachi2Adapter:
    def __init__(
        self,
        request_json: JSONObject,
        request_bundle: RequestBundleDir,
        package_manager: Literal["gomod", "pip"],
    ) -> None:
        self.request_json = request_json
        self.request_bundle = request_bundle
        self.package_manager = package_manager

    def run_package_manager(
        self,
        package_configs_for_package_manager: list[JSONObject],
        dependency_replacements_for_package_manager: Optional[list[JSONObject]] = None,
    ) -> RequestOutput:

        if self.package_manager == "gomod":
            fetch_fn = gomod.fetch_gomod_source
        elif self.package_manager == "pip":
            fetch_fn = pip.fetch_pip_source
        else:
            raise RuntimeError("How did you get here? Typing-wise, this is impossible.")

        cachi2_request = self._to_cachi2_request(
            package_configs_for_package_manager,
            dependency_replacements_for_package_manager,
        )
        try:
            cachi2_output = fetch_fn(cachi2_request)
        except cachi2_errors.Cachi2Error as e:
            log.error(e.friendly_msg())
            raise _to_cachito_error(e) from e
        except pydantic.ValidationError:
            log.exception("The Cachi2 backend failed to produce valid output")
            raise ServerError("The Cachi2 backend failed to produce valid output")

        return cachi2_output

    def update_request_env_vars(self, cachi2_output: RequestOutput) -> None:
        env_vars = {
            ev.name: {"value": ev.value, "kind": ev.kind}
            for ev in cachi2_output.environment_variables
        }
        update_request_env_vars(self.request_json["id"], env_vars)

    def update_request_config_files(self, cachi2_output: RequestOutput) -> None:
        raise ValueError(
            "Cachi2's project files are not compatible with Cachito's config files. "
            "Add them differently please."
        )

    def update_request_packages(self, cachi2_output: RequestOutput) -> PackagesData:
        packages_data = PackagesData()

        def to_package_dict(pkg: Package) -> JSONObject:
            return {"type": pkg.type, "name": pkg.name, "version": pkg.version}

        def to_dependency_dict(dep: Dependency) -> JSONObject:
            attrs = {"type": dep.type, "name": dep.name, "version": dep.version}
            if isinstance(dep, PipDependency):
                attrs["dev"] = dep.dev
            return attrs

        for package in cachi2_output.packages:
            packages_data.add_package(
                pkg_info=to_package_dict(package),
                path=str(package.path),
                deps=[to_dependency_dict(dep) for dep in package.dependencies],
            )

        packages_file = getattr(self.request_bundle, f"{self.package_manager}_packages_data")
        packages_data.write_to_file(packages_file)
        return packages_data

    @property
    def _cachi2_source_dir(self) -> Path:
        return self.request_bundle.source_root_dir

    @property
    def _cachi2_output_dir(self) -> Path:
        return self.request_bundle

    def _to_cachi2_request(
        self,
        package_configs_for_package_manager: list[JSONObject],
        dependency_replacements_for_package_manager: Optional[list[JSONObject]] = None,
    ) -> Cachi2Request:

        packages = [
            {"type": self.package_manager, **package}
            for package in package_configs_for_package_manager
        ]
        try:
            cachi2_request = Cachi2Request.parse_obj(
                {
                    "source_dir": self._cachi2_source_dir,
                    "output_dir": self._cachi2_output_dir,
                    "packages": packages,
                    "flags": self.request_json["flags"],
                    "dep_replacements": dependency_replacements_for_package_manager,
                }
            )
        except pydantic.ValidationError:
            log.exception("Failed to pass Cachito request to the Cachi2 backend")
            raise ServerError("Failed to pass Cachito request to the Cachi2 backend")

        return cachi2_request


def _to_cachito_error(e: cachi2_errors.Cachi2Error) -> CachitoError | ClientError | ServerError:
    if isinstance(e, cachi2_errors.InvalidInput):
        return ValidationError(str(e))
    if isinstance(e, cachi2_errors.UnexpectedFormat):
        return InvalidFileFormat(str(e))
    if isinstance(e, cachi2_errors.PackageRejected):
        # TODO: will often be wrong
        return InvalidRepoStructure(str(e))
    if isinstance(e, cachi2_errors.UnsupportedFeature):
        return UnsupportedFeature(str(e))
    if isinstance(e, cachi2_errors.FetchError):
        return NetworkError(str(e))
    if isinstance(e, cachi2_errors.GoModError):
        return GoModError(str(e))
    return CachitoError(str(e))
