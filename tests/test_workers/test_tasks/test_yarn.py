# SPDX-License-Identifier: GPL-3.0-or-later
import json
from textwrap import dedent
from unittest import mock

import pytest

from cachito.common.paths import RequestBundleDir
from cachito.errors import CachitoError, ValidationError
from cachito.workers.tasks import yarn


@mock.patch("cachito.workers.tasks.yarn.nexus.execute_script")
def test_cleanup_yarn_request(mock_exec_script):
    yarn.cleanup_yarn_request(42)

    expected_payload = {
        "repository_name": "cachito-yarn-42",
        "username": "cachito-yarn-42",
    }
    mock_exec_script.assert_called_once_with("js_cleanup", expected_payload)


def mock_bundle_dir(tmp_path):
    root_dir = tmp_path / "temp" / "1" / "app"
    root_dir.mkdir(parents=True)

    sub_dir = root_dir / "sub"
    sub_dir.mkdir()

    bundle_dir = RequestBundleDir(1, str(tmp_path))
    return bundle_dir, root_dir, sub_dir


def test_verify_yarn_files(tmp_path):
    bundle_dir, root, sub = mock_bundle_dir(tmp_path)

    (root / "package.json").touch()
    (root / "yarn.lock").touch()

    (sub / "package.json").touch()
    (sub / "yarn.lock").touch()

    bundle_dir = RequestBundleDir(1, str(tmp_path))
    yarn._verify_yarn_files(bundle_dir, [".", "sub"])


@pytest.mark.parametrize("missing_file", ["package.json", "yarn.lock"])
@pytest.mark.parametrize(
    "missing_in, expect_error",
    [
        (".", "File check failed for yarn: the {missing_file} file must be present"),
        ("sub", "File check failed for yarn: the sub/{missing_file} file must be present"),
    ],
)
def test_verify_yarn_files_missing_file(missing_file, missing_in, expect_error, tmp_path):
    bundle_dir, root, sub = mock_bundle_dir(tmp_path)

    if missing_in == "sub":
        (root / missing_file).touch()
    else:
        (sub / missing_file).touch()

    present_file = "package.json" if missing_file == "yarn.lock" else "yarn.lock"
    (root / present_file).touch()
    (sub / present_file).touch()

    with pytest.raises(ValidationError, match=expect_error.format(missing_file=missing_file)):
        yarn._verify_yarn_files(bundle_dir, [".", "sub"])


@pytest.mark.parametrize("present_file", ["package-lock.json", "npm-shrinkwrap.json"])
@pytest.mark.parametrize(
    "is_in, expect_error",
    [
        (".", "File check failed for yarn: the {present_file} file must not be present"),
        ("sub", "File check failed for yarn: the sub/{present_file} file must not be present"),
    ],
)
def test_verify_yarn_files_unwanted_npm_file(present_file, is_in, expect_error, tmp_path):
    bundle_dir, root, sub = mock_bundle_dir(tmp_path)

    (root / "package.json").touch()
    (root / "yarn.lock").touch()

    (sub / "package.json").touch()
    (sub / "yarn.lock").touch()

    if is_in == ".":
        (root / present_file).touch()
    else:
        (sub / present_file).touch()

    with pytest.raises(ValidationError, match=expect_error.format(present_file=present_file)):
        yarn._verify_yarn_files(bundle_dir, [".", "sub"])


@pytest.mark.parametrize(
    "is_in, expect_error",
    [
        (".", "File check failed for yarn: the node_modules directory must not be present"),
        ("sub", "File check failed for yarn: the sub/node_modules directory must not be present"),
    ],
)
def test_verify_yarn_files_node_modules(is_in, expect_error, tmp_path):
    bundle_dir, root, sub = mock_bundle_dir(tmp_path)

    (root / "package.json").touch()
    (root / "yarn.lock").touch()

    (sub / "package.json").touch()
    (sub / "yarn.lock").touch()

    if is_in == ".":
        (root / "node_modules").mkdir()
    else:
        (sub / "node_modules").mkdir()

    with pytest.raises(ValidationError, match=expect_error):
        yarn._verify_yarn_files(bundle_dir, [".", "sub"])


@mock.patch("cachito.workers.tasks.yarn.pyarn.lockfile.Lockfile")
def test_yarn_lock_to_str(mock_lockfile):
    rv = yarn._yarn_lock_to_str({})
    assert rv == mock_lockfile.return_value.to_str.return_value
    mock_lockfile.assert_called_once_with("1", {})


@mock.patch("cachito.workers.tasks.yarn.RequestBundleDir")
@mock.patch("cachito.workers.tasks.yarn.validate_yarn_config")
@mock.patch("cachito.workers.tasks.yarn._verify_yarn_files")
@mock.patch("cachito.workers.tasks.yarn.set_request_state")
@mock.patch("cachito.workers.tasks.yarn.get_request")
@mock.patch("cachito.workers.tasks.yarn.get_yarn_proxy_repo_name")
@mock.patch("cachito.workers.tasks.yarn.prepare_nexus_for_js_request")
@mock.patch("cachito.workers.tasks.yarn.resolve_yarn")
@mock.patch("cachito.workers.tasks.yarn.make_base64_config_file")
@mock.patch("cachito.workers.tasks.yarn._yarn_lock_to_str")
@mock.patch("cachito.workers.tasks.yarn.get_worker_config")
@mock.patch("cachito.workers.tasks.yarn.update_request_env_vars")
@mock.patch("cachito.workers.tasks.yarn.get_yarn_proxy_repo_username")
@mock.patch("cachito.workers.tasks.yarn.finalize_nexus_for_js_request")
@mock.patch("cachito.workers.tasks.yarn.get_yarn_proxy_repo_url")
@mock.patch("cachito.workers.tasks.yarn.generate_npmrc_config_files")
@mock.patch("cachito.workers.tasks.yarn.update_request_with_config_files")
def test_fetch_yarn(
    mock_update_config_files,
    mock_generate_npmrc,
    mock_get_yarn_repo_url,
    mock_finalize_nexus,
    mock_get_yarn_username,
    mock_update_env_vars,
    mock_worker_config,
    mock_yarn_lock_to_str,
    mock_b64_config_file,
    mock_resolve_yarn,
    mock_prepare_nexus,
    mock_get_yarn_repo_name,
    mock_get_request,
    mock_set_state,
    mock_verify_files,
    mock_validate_config,
    mock_request_bundle_dir,
    tmp_path,
    task_passes_state_check,
):
    # SETUP
    bundle_dir, root, sub = mock_bundle_dir(tmp_path)
    mock_request_bundle_dir.return_value = bundle_dir

    dep1 = {"name": "dep1", "version": "1.0.0", "dev": False, "type": "yarn"}
    dep2 = {"name": "dep2", "version": "2.0.0", "dev": False, "type": "yarn"}

    rv1 = {
        "deps": [dep1],
        "downloaded_deps": {"dep1@1.0.0"},
        "lock_file": None,
        "package": {"name": "pkg1", "version": "1.1.1", "type": "yarn"},
        "package.json": None,
    }
    rv2 = {
        "deps": [dep2],
        "downloaded_deps": {"bar@2.0.0"},
        "lock_file": {"dep2@^2.0.0": {"version": "2.0.0-external"}},
        "package": {"name": "pkg2", "version": "2.2.2", "type": "yarn"},
        "package.json": {
            "name": "pkg2",
            "version": "2.2.2",
            "dependencies": {"dep2": "2.0.0-external"},
        },
    }
    mock_resolve_yarn.side_effect = [rv1, rv2]

    yarnlock_str = dedent(
        """\
        dep2@^2.0.0:
          version "2.0.0-external"
        """
    )
    mock_yarn_lock_to_str.return_value = yarnlock_str

    packjson_str = dedent(
        """\
        {
          "name": "pkg2",
          "version": "2.2.2",
          "dependencies": {
            "dep2": "2.0.0-external"
          }
        }"""
    )

    mock_worker_config.return_value.cachito_default_environment_variables = {
        "npm": {"A": "1", "B": "2"},
        "yarn": {"B": "3", "C": "4"},
    }

    packjson_cfg = mock.Mock()
    yarnlock_cfg = mock.Mock()
    yarnrc_1_cfg = mock.Mock()
    yarnrc_2_cfg = mock.Mock()
    mock_b64_config_file.side_effect = [packjson_cfg, yarnlock_cfg, yarnrc_1_cfg, yarnrc_2_cfg]

    npmrc_1_cfg = mock.Mock()
    npmrc_2_cfg = mock.Mock()
    mock_generate_npmrc.return_value = [npmrc_1_cfg, npmrc_2_cfg]
    # /SETUP

    yarn.fetch_yarn_source(1, [{"path": "."}, {"path": "sub"}])

    # VALIDATION
    mock_validate_config.assert_called_once()
    mock_verify_files.assert_called_once_with(bundle_dir, [".", "sub"])
    mock_set_state.assert_has_calls(
        [
            mock.call(1, "in_progress", "Configuring Nexus for yarn"),
            mock.call(1, "in_progress", 'Fetching the yarn dependencies at the "." directory'),
            mock.call(1, "in_progress", 'Fetching the yarn dependencies at the "sub" directory'),
            mock.call(1, "in_progress", "Finalizing the Nexus configuration for yarn"),
        ]
    )
    mock_get_request.assert_has_calls(mock.call(1) for _ in range(2))
    mock_get_yarn_repo_name.assert_called_once_with(1)
    mock_prepare_nexus.assert_called_once_with(mock_get_yarn_repo_name.return_value)
    mock_resolve_yarn.assert_has_calls(
        [
            mock.call(str(root), mock_get_request.return_value, skip_deps=set()),
            mock.call(str(sub), mock_get_request.return_value, skip_deps=rv1["downloaded_deps"]),
        ]
    )
    mock_worker_config.assert_called_once()
    mock_update_env_vars.assert_called_once_with(1, {"A": "1", "B": "3", "C": "4"})
    mock_get_yarn_username.assert_called_once_with(1)
    mock_finalize_nexus.assert_called_once_with(
        mock_get_yarn_username.return_value, mock_get_yarn_repo_name.return_value
    )
    mock_get_yarn_repo_url.assert_called_once_with(1)
    mock_generate_npmrc.assert_called_once_with(
        mock_get_yarn_repo_url.return_value,
        mock_get_yarn_username.return_value,
        mock_finalize_nexus.return_value,  # password
        [".", "sub"],
    )
    mock_b64_config_file.assert_has_calls(
        [
            mock.call(packjson_str, "app/sub/package.json"),
            mock.call(yarnlock_str, "app/sub/yarn.lock"),
            mock.call("", "app/.yarnrc"),
            mock.call("", "app/sub/.yarnrc"),
        ]
    )
    mock_update_config_files.assert_called_once_with(
        1, [packjson_cfg, yarnlock_cfg, npmrc_1_cfg, npmrc_2_cfg, yarnrc_1_cfg, yarnrc_2_cfg]
    )

    expected = {
        "packages": [
            {"name": "pkg1", "version": "1.1.1", "type": "yarn", "dependencies": rv1["deps"]},
            {
                "name": "pkg2",
                "version": "2.2.2",
                "type": "yarn",
                "path": "sub",
                "dependencies": rv2["deps"],
            },
        ],
    }
    assert expected == json.loads(bundle_dir.yarn_packages_data.read_bytes())
    # /VALIDATION


@mock.patch("cachito.workers.tasks.yarn.RequestBundleDir")
@mock.patch("cachito.workers.tasks.yarn.validate_yarn_config")
@mock.patch("cachito.workers.tasks.yarn._verify_yarn_files")
@mock.patch("cachito.workers.tasks.yarn.set_request_state")
@mock.patch("cachito.workers.tasks.yarn.get_request")
@mock.patch("cachito.workers.tasks.yarn.get_yarn_proxy_repo_name")
@mock.patch("cachito.workers.tasks.yarn.prepare_nexus_for_js_request")
@mock.patch("cachito.workers.tasks.yarn.resolve_yarn")
@mock.patch("cachito.workers.tasks.yarn.make_base64_config_file")
@mock.patch("cachito.workers.tasks.yarn._yarn_lock_to_str")
@mock.patch("cachito.workers.tasks.yarn.get_worker_config")
@mock.patch("cachito.workers.tasks.yarn.update_request_env_vars")
@mock.patch("cachito.workers.tasks.yarn.get_yarn_proxy_repo_username")
@mock.patch("cachito.workers.tasks.yarn.finalize_nexus_for_js_request")
@mock.patch("cachito.workers.tasks.yarn.get_yarn_proxy_repo_url")
@mock.patch("cachito.workers.tasks.yarn.generate_npmrc_config_files")
@mock.patch("cachito.workers.tasks.yarn.update_request_with_config_files")
def test_fetch_yarn_no_configs(
    mock_update_config_files,
    mock_generate_npmrc,
    mock_get_yarn_repo_url,
    mock_finalize_nexus,
    mock_get_yarn_username,
    mock_update_env_vars,
    mock_worker_config,
    mock_yarn_lock_to_str,
    mock_b64_config_file,
    mock_resolve_yarn,
    mock_prepare_nexus,
    mock_get_yarn_repo_name,
    mock_get_request,
    mock_set_state,
    mock_verify_files,
    mock_validate_config,
    mock_request_bundle_dir,
    tmp_path,
    task_passes_state_check,
):
    bundle_dir, root, sub = mock_bundle_dir(tmp_path)
    mock_request_bundle_dir.return_value = bundle_dir

    mock_resolve_yarn.return_value = {
        "package.json": None,
        "lock_file": None,
        "downloaded_deps": set(),
        # Following ensures the packages JSON data collection works
        "package": {"name": "pkg1", "type": "yarn", "version": "1.0.0"},
        "deps": [],
        # Adding this to ensure this is a complete fake resolved yarn package info.
        "lock_file_name": "",
    }

    yarn.fetch_yarn_source(1)

    # Just do a sanity-check on calls where subpaths are relevant
    mock_verify_files.assert_called_once_with(bundle_dir, ["."])
    mock_resolve_yarn.assert_called_once_with(
        str(root), mock_get_request.return_value, skip_deps=set()
    )
    mock_generate_npmrc.assert_called_once_with(
        mock_get_yarn_repo_url.return_value,
        mock_get_yarn_username.return_value,
        mock_finalize_nexus.return_value,  # password
        ["."],
    )
    mock_b64_config_file.assert_called_once_with("", "app/.yarnrc")


@mock.patch("cachito.workers.tasks.yarn.RequestBundleDir")
@mock.patch("cachito.workers.tasks.yarn.validate_yarn_config")
@mock.patch("cachito.workers.tasks.yarn._verify_yarn_files")
@mock.patch("cachito.workers.tasks.yarn.set_request_state")
@mock.patch("cachito.workers.tasks.yarn.get_request")
@mock.patch("cachito.workers.tasks.yarn.get_yarn_proxy_repo_name")
@mock.patch("cachito.workers.tasks.yarn.prepare_nexus_for_js_request")
@mock.patch("cachito.workers.tasks.yarn.resolve_yarn")
def test_fetch_yarn_resolve_fails(
    mock_resolve_yarn,
    mock_prepare_nexus,
    mock_get_yarn_repo_name,
    mock_set_state,
    mock_verify_files,
    mock_validate_config,
    mock_request_bundle_dir,
    tmp_path,
    task_passes_state_check,
):
    bundle_dir, root, sub = mock_bundle_dir(tmp_path)
    mock_request_bundle_dir.return_value = bundle_dir

    mock_resolve_yarn.side_effect = [CachitoError("oops")]

    with pytest.raises(CachitoError, match="oops"):
        yarn.fetch_yarn_source(1)
