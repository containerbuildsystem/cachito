# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import json
import os
from pathlib import Path
from unittest import mock

import pytest

from cachito.common.packages_data import PackagesData
from cachito.common.paths import RequestBundleDir
from cachito.errors import (
    FileAccessError,
    InvalidRepoStructure,
    InvalidRequestData,
    UnsupportedFeature,
)
from cachito.workers import tasks
from cachito.workers.tasks import gomod


@pytest.mark.parametrize(
    "dep_replacements, expect_state_update, pkg_config, pkg_results",
    (
        (
            None,
            True,
            None,
            {"present": {".": True}, "relpath": {".": "./go.mod"}, "sourcedir": {".": "./"}},
        ),
        (
            None,
            False,
            None,
            {"present": {".": True}, "relpath": {".": "./go.mod"}, "sourcedir": {".": "./"}},
        ),
        (
            [{"name": "github.com/pkg/errors", "type": "gomod", "version": "v0.8.1"}],
            True,
            None,
            {"present": {".": True}, "relpath": {".": "./go.mod"}, "sourcedir": {".": "./"}},
        ),
        (
            None,
            True,
            [{"path": "bar"}, {"path": "foo"}],
            {
                "present": {"bar": True, "foo": True},
                "relpath": {"bar": "./bar/go.mod", "foo": "./foo/go.mod"},
                "sourcedir": {"bar": "./bar/", "foo": "./foo/"},
            },
        ),
        (
            None,
            True,
            [{"path": "."}, {"path": "foo"}],
            {
                "present": {".": True, "foo": True},
                "relpath": {".": "./go.mod", "foo": "./foo/go.mod"},
                "sourcedir": {".": "./", "foo": "./foo/"},
            },
        ),
        (
            [{"name": "github.com/pkg/errors", "type": "gomod", "version": "v0.8.1"}],
            False,
            [{"path": "."}, {"path": "foo"}],
            {
                "present": {".": True, "foo": True},
                "relpath": {".": "./go.mod", "foo": "./foo/go.mod"},
                "sourcedir": {".": "./", "foo": "./foo/"},
            },
        ),
    ),
)
@pytest.mark.parametrize("has_pkg_lvl_deps", (True, False))
@mock.patch("cachito.workers.tasks.gomod._fail_if_bundle_dir_has_workspaces")
@mock.patch("cachito.workers.tasks.gomod.RequestBundleDir")
@mock.patch("cachito.workers.tasks.gomod.update_request_env_vars")
@mock.patch("cachito.workers.tasks.gomod.set_request_state")
@mock.patch("cachito.workers.tasks.gomod.get_request")
@mock.patch("cachito.workers.tasks.gomod.resolve_gomod")
def test_fetch_gomod_source(
    mock_resolve_gomod,
    mock_get_request,
    mock_set_request_state,
    mock_update_request_env_vars,
    mock_bundle_dir,
    mock_fail_workspaces,
    dep_replacements,
    expect_state_update,
    pkg_config,
    pkg_results,
    has_pkg_lvl_deps,
    sample_deps_replace,
    sample_package,
    sample_pkg_deps,
    sample_pkg_lvl_pkg,
    sample_env_vars,
    task_passes_state_check,
    tmpdir,
):
    def directory_present(*args, **kwargs):
        mock_subpath = mock.Mock()
        (subpath,) = args
        mock_subpath.go_mod_file.exists.return_value = pkg_results["present"][subpath]
        mock_subpath.relpath.return_value = pkg_results["relpath"][subpath]
        mock_subpath.source_dir = pkg_results["sourcedir"][subpath]
        return mock_subpath

    mock_bundle_dir.return_value.app_subpath.side_effect = directory_present
    mock_bundle_dir.return_value.gomod_packages_data = Path(tmpdir, "gomod_packages_data.json")

    # Add the default environment variables from the configuration
    env_vars = {
        "GO111MODULE": {"value": "on", "kind": "literal"},
        "GOSUMDB": {"value": "off", "kind": "literal"},
        "GOTOOLCHAIN": {"value": "local", "kind": "literal"},
    }
    sample_env_vars.update(env_vars)

    mock_request = mock.Mock()
    mock_get_request.return_value = mock_request

    pkg_lvl_deps = []
    if has_pkg_lvl_deps:
        pkg_lvl_deps = sample_pkg_deps

    resolved_gomod_infos = [
        {
            "module": sample_package,
            "module_deps": sample_deps_replace,
            "packages": [{"pkg": sample_pkg_lvl_pkg, "pkg_deps": pkg_lvl_deps}],
        },
    ]

    if pkg_config:
        paths = [item["path"] for item in pkg_config]
        # make the gomod package for the second path
        second = copy.deepcopy(resolved_gomod_infos[0])
        # Change the version so that this second package can be collected into
        # packages JSON data.
        # Whatever the version is, as long as the version are different than
        # the one within the first resolved gomod info.
        second["module"]["version"] += "2"
        second["packages"][0]["pkg"]["version"] += "2"
        resolved_gomod_infos.append(second)
    else:
        paths = ["."]

    mock_resolve_gomod.side_effect = resolved_gomod_infos

    if dep_replacements is not None and len(paths) > 1:
        # This is unsupported and no other tests are necessary
        with pytest.raises(
            UnsupportedFeature,
            match="Dependency replacements are only supported for a single go module path.",
        ):
            tasks.fetch_gomod_source(1, dep_replacements, pkg_config)
        return

    tasks.fetch_gomod_source(1, dep_replacements, pkg_config)

    if expect_state_update:
        state_calls = []
        pkg_calls = []
        dep_calls = []

        for i, (path, gomod_info) in enumerate(zip(paths, resolved_gomod_infos)):
            state_calls.append(
                mock.call(
                    1,
                    "in_progress",
                    'Fetching the gomod dependencies at the "{}" directory'.format(path),
                )
            )
            if i == 0:
                mock_update_request_env_vars.assert_called_once_with(1, sample_env_vars)

            pkg_calls.append(
                mock.call(1, gomod_info["module"], sample_env_vars, package_subpath=path)
            )
            dep_calls.append(mock.call(1, gomod_info["module"], gomod_info["module_deps"]))
            # The calls for the package level package and dependencies
            for package in gomod_info["packages"]:
                pkg_calls.append(mock.call(1, package["pkg"], package_subpath=path))
                if has_pkg_lvl_deps:
                    dep_calls.append(mock.call(1, package["pkg"], package["pkg_deps"]))

        mock_set_request_state.assert_has_calls(state_calls)
        mock_get_request.assert_has_calls(mock.call(1) for _ in state_calls)

    gomod_calls = [
        mock.call(
            str(mock_bundle_dir().app_subpath(path).source_dir),
            mock_request,
            dep_replacements,
            mock_bundle_dir().source_dir,
        )
        for path in paths
    ]
    mock_resolve_gomod.assert_has_calls(gomod_calls)

    packages_data = PackagesData()
    for path, gomod_info in zip(paths, resolved_gomod_infos):
        module_info = gomod_info["module"]
        packages_data.add_package(module_info, path, gomod_info["module_deps"])
        for package in gomod_info["packages"]:
            pkg_info = package["pkg"]
            packages_data.add_package(pkg_info, path, package.get("pkg_deps", []))

    packages_data.sort()
    assert {"packages": packages_data._packages} == json.loads(
        mock_bundle_dir.return_value.gomod_packages_data.read_bytes()
    )


@pytest.mark.parametrize(
    "ignore_missing_gomod_file, exception_expected, pkg_config, pkg_results",
    (
        (
            True,
            False,
            None,
            {
                "present": {".": False},
                "relpath": {".": "./go.mod"},
                "missing_files": "./go.mod",
                "file_plurality": "",
            },
        ),
        (
            False,
            True,
            None,
            {
                "present": {".": False},
                "relpath": {".": "./go.mod"},
                "missing_files": "./go.mod",
                "file_plurality": "",
            },
        ),
        (
            False,
            True,
            [{"path": "bar"}, {"path": "foo"}],
            {
                "present": {"bar": True, "foo": False},
                "relpath": {"bar": "./bar/go.mod", "foo": "./foo/go.mod"},
                "missing_files": "./foo/go.mod",
                "file_plurality": "",
            },
        ),
        (
            False,
            True,
            [{"path": "bar"}, {"path": "foo"}],
            {
                "present": {"bar": False, "foo": False},
                "relpath": {"bar": "./bar/go.mod", "foo": "./foo/go.mod"},
                "missing_files": "./bar/go.mod; ./foo/go.mod",
                "file_plurality": "s",
            },
        ),
        (
            True,
            True,
            [{"path": "bar"}, {"path": "foo"}],
            {
                "present": {"bar": False, "foo": False},
                "relpath": {"bar": "./bar/go.mod", "foo": "./foo/go.mod"},
                "missing_files": "./bar/go.mod; ./foo/go.mod",
                "file_plurality": "s",
            },
        ),
    ),
)
@mock.patch("cachito.workers.tasks.gomod._fail_if_bundle_dir_has_workspaces")
@mock.patch("cachito.workers.tasks.gomod.get_worker_config")
@mock.patch("cachito.workers.tasks.gomod.RequestBundleDir")
@mock.patch("cachito.workers.tasks.gomod.resolve_gomod")
def test_fetch_gomod_source_no_go_mod_file(
    mock_resolve_gomod,
    mock_bundle_dir,
    mock_gwc,
    mock_fail_workspaces,
    ignore_missing_gomod_file,
    exception_expected,
    pkg_config,
    pkg_results,
    task_passes_state_check,
):
    def directory_present(*args, **kwargs):
        mock_subpath = mock.Mock()
        (subpath,) = args
        mock_subpath.go_mod_file.exists.return_value = pkg_results["present"][subpath]
        mock_subpath.relpath.return_value = pkg_results["relpath"][subpath]
        return mock_subpath

    mock_config = mock.Mock()
    mock_config.cachito_gomod_ignore_missing_gomod_file = ignore_missing_gomod_file
    mock_gwc.return_value = mock_config
    mock_bundle_dir.return_value.app_subpath.side_effect = directory_present
    if exception_expected:
        with pytest.raises(
            FileAccessError,
            match="The {} file{} must be present for the gomod package manager".format(
                pkg_results["missing_files"], pkg_results["file_plurality"]
            ),
        ):
            tasks.fetch_gomod_source(1, package_configs=pkg_config)
    else:
        tasks.fetch_gomod_source(1)

    mock_resolve_gomod.assert_not_called()


@pytest.mark.parametrize(
    "module_name, package_name, module_subpath, expect_subpath",
    [
        ("github.com/foo", "github.com/foo", ".", "."),
        ("github.com/foo", "github.com/foo", "bar", "bar"),
        ("github.com/foo", "github.com/foo/bar", ".", "bar"),
        ("github.com/foo", "github.com/foo/bar", "src", "src/bar"),
    ],
)
def test_package_subpath(module_name, package_name, module_subpath, expect_subpath):
    assert gomod._package_subpath(module_name, package_name, module_subpath) == expect_subpath


@pytest.mark.parametrize(
    "repo, subpath, expected_result",
    [
        ("repo", "workspace", True),
        ("repo", "workspace/mod_a", True),
        ("repo", "workspace/mod_b", True),
        ("repo", "nonworspace", False),
        ("repo", "nonworspace/mod_c", False),
        ("repo", "randompath", False),
        ("anotherrepo", ".", True),
    ],
)
def test_is_workspace(repo, subpath, expected_result, tmpdir):
    tmpdir.mkdir("repo")
    tmpdir.mkdir("repo/workspace")
    tmpdir.mkdir("repo/workspace/mod_a")
    tmpdir.mkdir("repo/workspace/mod_b")
    tmpdir.mkdir("repo/nonworspace")
    tmpdir.mkdir("repo/nonworspace/mod_c")
    tmpdir.mkdir("anotherrepo")

    Path(tmpdir / "repo/workspace" / "go.work").touch()
    Path(tmpdir / "anotherrepo" / "go.work").touch()

    repo_root = Path(tmpdir / repo)
    result = gomod._is_workspace(repo_root, subpath)

    assert result == expected_result


@pytest.mark.parametrize("add_go_work_file", [True, False])
def test_fail_if_bundle_dir_has_workspaces(add_go_work_file, tmpdir):
    tmpdir.mkdir("temp")
    tmpdir.mkdir("temp/1")
    tmpdir.mkdir("temp/1/app")

    bundle_dir = RequestBundleDir(1, tmpdir)

    if add_go_work_file:
        Path(bundle_dir.source_root_dir / "go.work").touch()
        with pytest.raises(InvalidRepoStructure):
            gomod._fail_if_bundle_dir_has_workspaces(bundle_dir, ["."])
    else:
        gomod._fail_if_bundle_dir_has_workspaces(bundle_dir, ["."])


@pytest.mark.parametrize(
    "packages",
    [
        pytest.param(
            [
                {
                    "name": "spam/v3",
                    "type": "gomod",
                    "version": "1.0.0",
                    "dependencies": [],
                },
                {
                    "name": "spam/eggs/v3",
                    "type": "gomod",
                    "version": "1.0.0",
                    "dependencies": [
                        # module's name matches (normpath doesn't)
                        {
                            "name": "spam/v3",
                            "type": "gomod",
                            "version": "../",  # spam/eggs/v3/.. == spam/eggs
                        },
                        # package's name matches (normpath doesn't)
                        {
                            "name": "spam/v3/client",  # spam/v3/client is a package from spam/v3
                            # technically, a `gomod` package would never have a
                            # `go-package` dependency, but good enough for a unit test
                            "type": "go-package",
                            "version": "../client",
                        },
                    ],
                },
            ],
            id="only_name_match",
        ),
        pytest.param(
            [
                {
                    "name": "k8s.io/kubernetes",
                    "version": "1.0.0",
                    "type": "gomod",
                    "dependencies": [],
                },
                {
                    "name": "k8s.io/kubernetes/some-module",
                    "version": "1.0.0",
                    "type": "gomod",
                    "dependencies": [
                        # module's normpath matches (name doesn't)
                        {
                            "name": "k8s.io/api",
                            "type": "gomod",
                            "version": "../staging/src/k8s.io/api",
                        },
                        # package's normpath matches (name doesn't)
                        {
                            "name": "k8s.io/api/node",
                            "type": "go-package",
                            "version": "../staging/src/k8s.io/api/node",
                        },
                    ],
                },
            ],
            id="only_normpath_match",
        ),
        pytest.param(
            [
                {
                    "name": "foo",
                    "type": "gomod",
                    "version": "1.0.0",
                    "dependencies": [],
                },
                {
                    "name": "foo/bar",
                    "type": "gomod",
                    "version": "1.0.0",
                    "dependencies": [
                        # both name and normpath match
                        {
                            "name": "foo",
                            "type": "gomod",
                            "version": "../",  # foo/bar/.. == foo
                        },
                        {
                            "name": "foo/bar/baz",
                            "type": "go-package",
                            "version": "../bar/baz",
                        },
                    ],
                },
            ],
            id="both_name_and_normpath_match",
        ),
    ],
)
def test_fail_if_parent_replacement_not_included(packages):
    packages_json_data = PackagesData()

    for package in packages:
        packages_json_data.add_package(package, os.curdir, package.get("dependencies", []))

    gomod._fail_if_parent_replacement_not_included(packages_json_data)


def test_fail_if_parent_replacement_not_included_no_dep_module():
    packages = [
        {
            "name": "foo/bar",
            "type": "gomod",
            "version": "1.0.0",
            "dependencies": [
                {"name": "foo", "type": "gomod", "version": "../"},
                {"name": "foo/bar/baz", "type": "go-package", "version": "1.0.0"},
            ],
        }
    ]
    packages_json_data = PackagesData()
    for package in packages:
        packages_json_data.add_package(package, os.curdir, package.get("dependencies", []))
    msg = (
        "Could not find a Go module in this request containing foo while processing "
        "dependency {'name': 'foo', 'type': 'gomod', 'version': '../'} of package foo/bar. "
        "Please tell Cachito to process the module which contains the dependency. "
        "Perhaps the parent module of foo/bar?"
    )

    with pytest.raises(InvalidRequestData, match=msg):
        gomod._fail_if_parent_replacement_not_included(packages_json_data)


def test_fail_if_parent_replacement_not_included_no_pkg_module():
    packages = [
        {
            "name": "foo/bar",
            "type": "go-package",
            "version": "1.0.0",
            "dependencies": [{"name": "foo", "type": "go-package", "version": "../foo/baz"}],
        }
    ]
    packages_json_data = PackagesData()
    for package in packages:
        packages_json_data.add_package(package, os.curdir, package.get("dependencies", []))
    msg = "Could not find parent Go module for package: foo/bar"

    with pytest.raises(RuntimeError, match=msg):
        gomod._fail_if_parent_replacement_not_included(packages_json_data)
