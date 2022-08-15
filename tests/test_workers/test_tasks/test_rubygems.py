# SPDX-License-Identifier: GPL-3.0-or-later
import base64
from pathlib import Path
from unittest import mock

import pytest

from cachito.errors import CachitoError
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
def test_get_config_file(tmp_path, exists):
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
                pkg_and_deps_info["dependencies"], bundle_dir, package_root, rubygems_hosted_repo
            )
    else:
        dep = rubygems._get_config_file_for_given_package(
            pkg_and_deps_info["dependencies"], bundle_dir, package_root, rubygems_hosted_repo
        )

        assert dep["path"] == "app/pkg1/.bundle/config"
        assert dep["type"] == "base64"
        contents = base64.b64decode(dep["content"]).decode()
        assert f'BUNDLE_MIRROR__ALL: "{rubygems_hosted_repo}"' in contents
        git_dep = 'BUNDLE_LOCAL__RSPEC___CORE__3: "../../deps/rubygems/rspec-core.3/some-path/app"'
        assert git_dep in contents
