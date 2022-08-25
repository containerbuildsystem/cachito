# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import json
import os
from pathlib import Path
from textwrap import dedent
from unittest import mock
from unittest.mock import call

import pytest

from cachito.errors import CachitoError
from cachito.workers.paths import RequestBundleDir
from cachito.workers.tasks import rubygems


@mock.patch("cachito.workers.tasks.rubygems.nexus.execute_script")
def test_cleanup_rubygems_request(mock_exec_script):
    rubygems.cleanup_rubygems_request(42)

    expected_payload = {
        "rubygems_repository_name": "cachito-rubygems-hosted-42",
        "username": "cachito-rubygems-42",
    }
    mock_exec_script.assert_called_once_with("rubygems_cleanup", expected_payload)


class MockBundleDir(type(Path())):
    """Mocked RequestBundleDir."""

    def __new__(cls, *args, **kwargs):
        """Make a new MockBundleDir."""
        self = super().__new__(cls, *args, **kwargs)
        self.source_root_dir = self.joinpath("app")
        self.rubygems_deps_dir = self / "deps" / "rubygems"
        return self


@pytest.mark.parametrize("exists", [True, False])
@pytest.mark.parametrize("ca_cert_path", [None, "app/rubygems-proxy-ca.pem"])
def test_get_config_file(tmp_path, exists, ca_cert_path):
    bundle_dir = MockBundleDir(tmp_path)

    pkg_and_deps_info = {
        "dependencies": [
            {
                "name": "rspec-core.3",
                "path": bundle_dir.rubygems_deps_dir / "rspec-core.3" / "some-path",
                "kind": "GIT",
            }
        ],
    }
    rubygems_hosted_repo = "https://admin:admin@hosted.com"
    package_root = bundle_dir.source_root_dir / "pkg1"

    if exists:
        config_file = package_root / Path(".bundle/config")
        config_file.parent.mkdir(parents=True)
        config_file.touch()
        msg = (
            f"Cachito wants to create a config file at location {config_file}, "
            f"but it already exists."
        )
        with pytest.raises(CachitoError, match=msg):
            rubygems._get_config_file_for_given_package(
                pkg_and_deps_info["dependencies"],
                bundle_dir,
                package_root,
                rubygems_hosted_repo,
                ca_cert_path,
            )
    else:
        dep = rubygems._get_config_file_for_given_package(
            pkg_and_deps_info["dependencies"],
            bundle_dir,
            package_root,
            rubygems_hosted_repo,
            ca_cert_path,
        )

        assert dep["path"] == "app/pkg1/.bundle/config"
        assert dep["type"] == "base64"
        contents = base64.b64decode(dep["content"]).decode()
        assert f'BUNDLE_MIRROR__ALL: "{rubygems_hosted_repo}"' in contents
        git_dep = 'BUNDLE_LOCAL__RSPEC___CORE__3: "../../deps/rubygems/rspec-core.3/some-path/app"'
        assert git_dep in contents
        if ca_cert_path is not None:
            assert "BUNDLE_SSL_CA_CERT: ../rubygems-proxy-ca.pem" in contents


@pytest.mark.parametrize("with_cert", [True, False])
@pytest.mark.parametrize("package_subpath", [None, ".", "some/path"])
@mock.patch("cachito.workers.tasks.rubygems.get_rubygems_hosted_url_with_credentials")
@mock.patch("cachito.workers.tasks.rubygems.nexus.get_ca_cert")
@mock.patch("cachito.workers.tasks.rubygems.resolve_rubygems")
@mock.patch("cachito.workers.tasks.rubygems.finalize_nexus_for_rubygems_request")
@mock.patch("cachito.workers.tasks.rubygems.prepare_nexus_for_rubygems_request")
@mock.patch("cachito.workers.tasks.rubygems.update_request_with_config_files")
@mock.patch("cachito.workers.tasks.rubygems.set_request_state")
@mock.patch("cachito.workers.tasks.utils.get_request_state")
@mock.patch("cachito.workers.tasks.rubygems.get_request")
def test_fetch_rubygems_source(
    mock_get_request,
    mock_get_state,
    mock_set_state,
    mock_update_cfg,
    mock_prepare_nexus,
    mock_finalize_nexus,
    mock_resolve,
    mock_cert,
    mock_get_url,
    package_subpath,
    with_cert,
    tmpdir,
):
    # Setup
    pkg_data = {
        "package": {
            "name": "pkg_name",
            "version": "1.0.0",
            "type": "rubygems",
            "path": package_subpath,
        },
        "dependencies": [
            {
                "kind": "GEM",
                "name": "ci_reporter",
                "version": "2.0.0",
                "path": "some/path",
                "type": "rubygems",
            },
        ],
    }
    request = {"id": 1}
    password = "password"

    mock_get_state.return_value = "in_progress"
    mock_resolve.return_value = pkg_data
    mock_finalize_nexus.return_value = password
    mock_get_request.return_value = request
    stub_url = "stub_url"
    mock_get_url.return_value = stub_url

    if package_subpath:
        package_configs = [{"path": package_subpath}]
    else:
        package_configs = None

    if package_subpath is None or package_subpath == ".":
        bundle_config_path = "app/.bundle/config"
        nexus_ca_path = "rubygems-proxy-ca.pem"
    else:
        bundle_config_path = f"app/{package_subpath}/.bundle/config"
        nexus_ca_path = "../../rubygems-proxy-ca.pem"

    cfg = []
    cert_contents = "stub_cert"
    if with_cert:
        mock_cert.return_value = cert_contents
        b64_cert_contents = base64.b64encode(cert_contents.encode()).decode()
        cfg.append(
            {"content": b64_cert_contents, "path": "app/rubygems-proxy-ca.pem", "type": "base64"}
        )
    else:
        mock_cert.return_value = None

    bundle_config = get_bundle_base_config(stub_url)
    if with_cert:
        bundle_config += f"\nBUNDLE_SSL_CA_CERT: {nexus_ca_path}"
    b64_bundle_config = base64.b64encode(bundle_config.encode()).decode()
    cfg.append({"content": b64_bundle_config, "path": bundle_config_path, "type": "base64"})

    # Exercise
    rubygems.fetch_rubygems_source(request["id"], package_configs=package_configs)

    # Verify
    mock_prepare_nexus.assert_called_once()
    mock_finalize_nexus.assert_called_once()

    calls = call(request["id"], cfg)
    assert calls == mock_update_cfg.call_args

    expected = pkg_data["package"].copy()
    expected_dependencies = pkg_data["dependencies"]
    del expected_dependencies[0]["path"]
    expected["dependencies"] = expected_dependencies
    if not package_subpath or package_subpath == os.curdir:
        del expected["path"]
    assert {"packages": [expected]} == json.loads(
        RequestBundleDir(1).rubygems_packages_data.read_bytes()
    )


def get_bundle_base_config(rubygems_hosted_url):
    return dedent(
        f"""
        # Sets mirror for all RubyGems sources
        BUNDLE_MIRROR__ALL: "{rubygems_hosted_url}"
        # Turn off the probing
        BUNDLE_MIRROR__ALL__FALLBACK_TIMEOUT: "false"
        # Install only ruby platform gems (=> gems with native extensions are compiled from source).
        # All gems should be platform independent already, so why not keep it here.
        BUNDLE_FORCE_RUBY_PLATFORM: "true"
        BUNDLE_DEPLOYMENT: "true"
        # Defaults to true when deployment is set to true
        BUNDLE_FROZEN: "true"
        # For local Git replacements, branches don't have to be specified (commit hash is enough)
        BUNDLE_DISABLE_LOCAL_BRANCH_CHECK: "true"
    """
    )
