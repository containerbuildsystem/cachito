import functools
import logging
import os
import random
import secrets
import shutil
import tempfile
import textwrap
from pathlib import Path
from urllib.parse import urlparse

from cachito.errors import CachitoError
from cachito.workers import nexus
from cachito.workers.config import get_worker_config
from cachito.workers.errors import NexusScriptError
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers.general import run_cmd

__all__ = [
    "get_bundler_proxy_repo_name",
    "get_bundler_proxy_repo_url",
    "get_bundler_proxy_repo_username",
    "generate_bundle_config_content",
    "resolve_bundler",
]

log = logging.getLogger(__name__)
run_bundler_cmd = functools.partial(run_cmd, exc_msg='Processing bundler dependencies failed')


RUBYGEMS_REGISTRY_CNAMES = ("rubygems.org")
RUBYGEMS_REGISTRY_HTTP = "http://rubygems.org"
RUBYGEMS_REGISTRY_HTTPS = "https://rubygems.org"


def get_bundler_proxy_repo_name(request_id):
    """
    Get the name of the rubygems proxy repository for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the cachito-rubygems-<REQUEST_ID> string, representing the temporary repository name
    :rtype: str
    """
    config = get_worker_config()
    return f"{config.cachito_nexus_request_repo_prefix}rubygems-{request_id}"


def get_bundler_proxy_repo_url(request_id):
    """
    Get the URL for the Nexus rubygems proxy repository for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the URL for the Nexus cachito-rubygems-<REQUEST_ID> repository
    :rtype: str
    """
    config = get_worker_config()
    repo_name = get_bundler_proxy_repo_name(request_id)
    return f"{config.cachito_nexus_url.rstrip('/')}/repository/{repo_name}/"


def get_bundler_proxy_repo_username(request_id):
    """
    Get the username that has read access on the rubygems proxy repository for the request.

    :param int request_id: the ID of the request this repository is for
    :return: the cachito-rubygems-<REQUEST_ID> string, representing the user
        who will access the temporary Nexus repository
    :rtype: str
    """
    return f"cachito-rubygems-{request_id}"


def _is_from_rubygems_registry(pkg_url):
    """
    Check if package is from the Rubygems.org registry.

    :param str pkg_url: url of the package, in Gemfile.lock
    :rtype: bool
    """
    return urlparse(pkg_url).hostname in RUBYGEMS_REGISTRY_CNAMES


def _get_package(request):
    """
    Get the main package and dependencies based on the lock file.

    :param (str | Path) gemfile_path: the path to the Gemfile
    :param (str | Path) gemfile_lock_path: the path to Gemfile.lock
    :return: a dictionary that has the following keys:
        "package": the dictionary describing the main package
    :rtype: dict
    """
    try:
        package = {
            'name': request['repo'],
            'type': 'bundler',
            'version': request['ref'],
        }

    except KeyError:
        raise CachitoError("The request is missing required data (repo, ref)")

    return {
        "package": package,
    }


def resolve_bundler(app_source_path, request, skip_deps=None):
    """
    Resolve and fetch bundler dependencies for the given app source archive.

    :param (str | Path) app_source_path: the full path to the application source code
    :param dict request: the Cachito request this is for
    :param set skip_deps: a set of dependency identifiers to not download because they've already
        been downloaded for this request
    :return: a dictionary that has the following keys:
        ``deps`` which is the list of dependencies,
        ``downloaded_deps`` which is a set of the dependency identifiers of the dependencies that
        were downloaded as part of this function's execution,
        ``lock_file`` which is the lock file if it was modified,
        ``package`` which is the dictionary describing the main package, and
        ``Gemfile`` which is the Gemfile file if it was modified.
    :rtype: dict
    :raises CachitoError: if fetching the dependencies fails or required files are missing
    """
    app_source_path = Path(app_source_path)

    package_and_deps_info = _get_package(request)

    # By downloading the dependencies, it stores the tarballs in the bundle and also stages the
    # content in the yarn repository for the request
    proxy_repo_url = get_bundler_proxy_repo_url(request["id"])

    package_and_deps_info["downloaded_deps"] = download_dependencies(
        request["id"],
        app_source_path,
        proxy_repo_url,
        pkg_manager="bundler",
    )

    # deps and downloaded_deps are the same for bundler
    package_and_deps_info["deps"] = package_and_deps_info["downloaded_deps"]

    return package_and_deps_info


def download_dependencies(request_id, app_source_path, proxy_repo_url, pkg_manager="bundler"):
    """
    Download the list of ruby gem dependencies using bundle package to the vendor directory.

    By downloading the dependencies, this stages the content in the request specific rubygems proxy.

    :param int request_id: the ID of the request these dependencies are being downloaded for
    :param app_source_path:
 not download because they've already
        been downloaded for this request
    :param str proxy_repo_url: the Nexus proxy repository URL to use as the registry
    :param str pkg_manager: the name of the package manager to download dependencies for, affects
        destination directory and logging output (bundler is used to do the actual download regardless)
    :return: a set of dependency identifiers that were downloaded
    :rtype: set
    :raises CachitoError: if any of the downloads fail
    """

    conf = get_worker_config()
    with tempfile.TemporaryDirectory(prefix="cachito-") as temp_dir:
        app_source_path.joinpath('.bundle').mkdir(exist_ok=True)
        bundle_config_path = os.path.join(app_source_path, '.bundle')
        bundle_config_file = os.path.join(bundle_config_path, "config")

        if conf.cachito_nexus_ca_cert and os.path.exists(conf.cachito_nexus_ca_cert):
            nexus_ca = conf.cachito_nexus_ca_cert
        else:
            nexus_ca = None
        # The token must be privileged so that it has access to the cachito-rubygems repository

        generate_and_write_bundle_config_file(
            bundle_config_file,
            proxy_repo_url,
            conf.cachito_nexus_username,
            conf.cachito_nexus_password,
            custom_ca_path=nexus_ca,
        )

        bundle_dir = RequestBundleDir(request_id)
        if pkg_manager == "bundler":
            deps_download_dir = bundle_dir.bundler_deps_dir
        else:
            raise ValueError(f"Invalid package manager: {pkg_manager!r}")

        deps_download_dir.mkdir(exist_ok=True)

        env = {
            'BUNDLE_PATH': temp_dir,
            'BUNDLE_CACHE_PATH': deps_download_dir,
            'BUNDLE_DISABLE_SHARED_GEMS': '1',
            'PATH': os.environ.get('PATH', ''),
        }

        # Download the dependencies directly in the bundle directory
        run_params = {"env": env, "cwd": str(app_source_path)}

        # by setting the proxy repo as a mirror of rubygems.org, all dependencies will be pulled into the proxy repo.
        # Bundler will try downloading from the proxy repo, and Nexus - in turn - will download from rubygems.org.
        run_bundler_cmd(
            ('bundle', 'config', f'mirror.{RUBYGEMS_REGISTRY_HTTP}', proxy_repo_url),
            run_params
        )
        run_bundler_cmd(
            ('bundle', 'config', f'mirror.{RUBYGEMS_REGISTRY_HTTPS}', proxy_repo_url),
            run_params
        )

        log.info('Downloading the bundler dependencies through Nexus proxy...')

        bundle_package_output = run_bundler_cmd(
            ('bundle', 'package', '--no-install', '--all'),
            run_params
        )

        downloaded_deps = list()

        for line in bundle_package_output.splitlines():
            log.debug('read line: %s', line)
            if line.strip().startswith('Fetching'):
                parts = [part for part in line.split(' ') if part != '']
                if len(parts) == 3:
                    downloaded_deps.append({
                        'name': parts[1],
                        'type': 'bundler',
                        'version': parts[2],
                    })
                    log.debug('Added dependency: %s @ version: %s', parts[1], parts[2])
                else:
                    log.warning('Unexpected bundler list output: %s', line)

        return downloaded_deps


def get_rubygems_hosted_repo_name():
    """
    Get the name of Rubygems hosted repository.

    :return: the name of Rubygems hosted repository
    :rtype: str
    """
    config = get_worker_config()
    return config.cachito_nexus_rubygems_hosted_repo_name


def generate_bundle_config_content(proxy_repo_url, username, password, custom_ca_path=None):
    """
    Generate a .bundle/config file with the registry and authentication configured.

    :param str proxy_repo_url: the rubygems registry URL
    :param str username: the username of the user to use for authenticating to the registry
    :param str password: the password of the user to use for authenticating to the registry
    :param str custom_ca_path: the path to set ``BUNDLE_SSL_CA_CERT`` to in the .bundle/config file; if not provided,
        this option will be omitted
    :return: the contents of the .bundle/config file
    :rtype: str
    """
    # Instead of getting the token from Nexus, use basic authentication as supported by Nexus:
    # https://help.sonatype.com/repomanager3/formats/npm-registry#npmRegistry-AuthenticationUsingBasicAuth
    # token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("utf-8")
    # The bundler config requires the
    formatted_proxy_repo_host = proxy_repo_url.replace(".", "__").upper()
    bundle_config = textwrap.dedent(
        f"""\
        BUNDLE_{formatted_proxy_repo_host}: "{username}:{password}"
        BUNDLE_MIRROR__HTTP://RUBYGEMS__ORG/: "{proxy_repo_url}"
        BUNDLE_MIRROR__HTTPS://RUBYGEMS__ORG/: "{proxy_repo_url}"
        BUNDLE_CACHE_ALL: true
        BUNDLE_REDIRECT: "5"
        BUNDLE_TIMEOUT: "30"
        BUNDLE_RETRY: "5"
        """
    )

    if custom_ca_path:
        bundle_config += f'BUNDLE_SSL_CA_CERT: "{custom_ca_path}"\n'

    return bundle_config


def generate_and_write_bundle_config_file(bundle_config_path, proxy_repo_url, username, password, custom_ca_path=None):
    """
    Generate a .bundle/config file at the input location with the registry and authentication configured.

    :param str bundle_config_path: the path to create the .bundle/config file
    :param str proxy_repo_url: the rubygems registry URL
    :param str username: the username of the user to use for authenticating to the registry
    :param str password: the password of the user to use for authenticating to the registry
    :param str custom_ca_path: the path to set ``cafile`` to in the .bundle/config file; if not provided,
        this option will be omitted
    """
    log.debug("Generating a .bundle/config file at %s", bundle_config_path)
    with open(bundle_config_path, "w") as f:
        f.write(
            generate_bundle_config_content(
                proxy_repo_url, username, password, custom_ca_path=custom_ca_path
            )
        )
