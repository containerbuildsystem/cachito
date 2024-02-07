# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import re
import subprocess
import tarfile
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory as tempDir
from textwrap import dedent
from typing import Any, Optional, Union
from unittest import mock

import git
import pytest
from packaging.version import Version

from cachito.errors import GoModError, InvalidFileFormat, UnsupportedFeature, ValidationError
from cachito.workers import safe_extract
from cachito.workers.errors import CachitoCalledProcessError
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers import gomod
from tests.helper_utils import assert_directories_equal, write_file_tree


def setup_module():
    """Re-enable logging that was disabled at some point in previous tests."""
    gomod.log.disabled = False
    gomod.log.setLevel("DEBUG")


def get_mocked_data(filepath: Union[str, Path]) -> str:
    gomod_mocks = Path(__file__).parent / "data" / "gomod-mocks"
    return gomod_mocks.joinpath(filepath).read_text()


@pytest.fixture
def go_mod_file(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    output_file = tmp_path / "go.mod"

    with open(output_file, "w") as f:
        f.write(request.param)


RETRODEP_PRE_REPLACE = "github.com/release-engineering/retrodep/v2"
RETRODEP_POST_REPLACE = "github.com/cachito-testing/retrodep/v2"


@pytest.mark.parametrize(
    "dep_replacement, expected_replace",
    (
        (None, None),
        (
            # note: this replacement is already mocked in the gomod-mocks data
            # by setting the same replacement in dep_replacements, the only difference
            # should be that Cachito will report the replaced module
            {
                "name": RETRODEP_PRE_REPLACE,
                "new_name": RETRODEP_POST_REPLACE,
                "type": "gomod",
                "version": "v2.1.1",
            },
            f"{RETRODEP_PRE_REPLACE}={RETRODEP_POST_REPLACE}@v2.1.1",
        ),
    ),
)
@pytest.mark.parametrize("cgo_disable", [False, True])
@pytest.mark.parametrize("force_gomod_tidy", [False, True])
@mock.patch("cachito.workers.pkg_managers.gomod.Go.release", new_callable=mock.PropertyMock)
@mock.patch("cachito.workers.pkg_managers.gomod._get_gomod_version")
@mock.patch("cachito.workers.pkg_managers.gomod.get_golang_version")
@mock.patch("cachito.workers.pkg_managers.gomod.GoCacheTemporaryDirectory")
@mock.patch("cachito.workers.pkg_managers.gomod._merge_bundle_dirs")
@mock.patch("cachito.workers.pkg_managers.gomod._get_allowed_local_deps")
@mock.patch("cachito.workers.pkg_managers.gomod._vet_local_deps")
@mock.patch("cachito.workers.pkg_managers.gomod._set_full_local_dep_relpaths")
@mock.patch("subprocess.run")
def test_resolve_gomod(
    mock_run: mock.Mock,
    mock_set_full_relpaths: mock.Mock,
    mock_vet_local_deps: mock.Mock,
    mock_get_allowed_local_deps: mock.Mock,
    mock_merge_tree: mock.Mock,
    mock_temp_dir: mock.Mock,
    mock_golang_version: mock.Mock,
    mock_get_gomod_version: mock.Mock,
    mock_go_release: mock.PropertyMock,
    dep_replacement: Optional[dict[str, Any]],
    expected_replace: Optional[str],
    cgo_disable: bool,
    force_gomod_tidy: bool,
    tmp_path: Path,
):
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmp_path)

    # Mock the "subprocess.run" calls
    run_side_effects = []
    if dep_replacement:
        run_side_effects.append(mock.Mock(returncode=0, stdout=None))  # go mod edit -replace
    run_side_effects.append(
        # go mod download -json
        mock.Mock(returncode=0, stdout=get_mocked_data("non-vendored/go_mod_download.json"))
    )
    if force_gomod_tidy or dep_replacement:
        run_side_effects.append(mock.Mock(returncode=0, stdout=None))  # go mod tidy
    run_side_effects.append(
        # go list -m
        mock.Mock(returncode=0, stdout="github.com/cachito-testing/gomod-pandemonium")
    )
    run_side_effects.append(
        # go list -deps -json all
        mock.Mock(returncode=0, stdout=get_mocked_data("non-vendored/go_list_deps_all.json"))
    )
    run_side_effects.append(
        # go list -deps -json ./...
        mock.Mock(returncode=0, stdout=get_mocked_data("non-vendored/go_list_deps_threedot.json"))
    )
    mock_run.side_effect = run_side_effects

    mock_golang_version.return_value = "v0.1.0"
    mock_go_release.return_value = "go0.1.0"
    mock_get_gomod_version.return_value = "0.1.1"

    mock_get_allowed_local_deps.return_value = ["*"]

    module_dir = str(tmp_path / "path/to/module")
    request = {"id": 3, "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848", "flags": []}
    if cgo_disable:
        request["flags"].append("cgo-disable")
    if force_gomod_tidy:
        request["flags"].append("force-gomod-tidy")

    expect_gomod = json.loads(get_mocked_data("expected-results/resolve_gomod.json"))
    if dep_replacement is None:
        gomod_ = gomod.resolve_gomod(module_dir, request)
    else:
        gomod_ = gomod.resolve_gomod(module_dir, request, [dep_replacement])
        # modify the expected data (Cachito should report the replaced module)
        for mod in expect_gomod["module_deps"]:
            if mod["name"] == RETRODEP_POST_REPLACE:
                mod["replaces"] = {
                    "name": RETRODEP_PRE_REPLACE,
                    "version": "v2.1.0",
                    "type": "gomod",
                }

    assert gomod_ == expect_gomod

    if expected_replace:
        assert mock_run.call_args_list[0][0][0] == [
            "go",
            "mod",
            "edit",
            "-replace",
            expected_replace,
        ]
        assert mock_run.call_args_list[2][0][0] == ["go", "mod", "tidy"]
    elif force_gomod_tidy:
        assert mock_run.call_args_list[1][0][0] == ["go", "mod", "tidy"]

    # when not vendoring, go list should be called with -mod readonly
    listdeps_cmd = [
        "go",
        "list",
        "-e",
        "-mod",
        "readonly",
        "-deps",
        "-json=ImportPath,Module,Standard,Deps",
    ]
    assert mock_run.call_args_list[-2][0][0] == [*listdeps_cmd, "all"]
    assert mock_run.call_args_list[-1][0][0] == [*listdeps_cmd, "./..."]

    for call in mock_run.call_args_list:
        env = call.kwargs["env"]
        if cgo_disable:
            assert env["CGO_ENABLED"] == "0"
        else:
            assert "CGO_ENABLED" not in env

    mock_merge_tree.assert_called_once_with(
        str(tmp_path / RequestBundleDir.go_mod_cache_download_part),
        str(RequestBundleDir(request["id"]).gomod_download_dir),
    )

    expect_module_name = expect_gomod["module"]["name"]
    expect_module_deps = expect_gomod["module_deps"]
    expect_pkg_deps = expect_gomod["packages"][0]["pkg_deps"]

    mock_get_allowed_local_deps.assert_called_once_with(expect_module_name)
    mock_vet_local_deps.assert_has_calls(
        [
            mock.call(expect_module_deps, expect_module_name, ["*"], module_dir, module_dir),
            mock.call(
                expect_pkg_deps,
                expect_module_name,
                ["*"],
                module_dir,
                module_dir,
            ),
        ],
    )
    mock_set_full_relpaths.assert_called_once_with(expect_pkg_deps, expect_module_deps)


@pytest.mark.parametrize("force_gomod_tidy", [False, True])
@mock.patch("cachito.workers.pkg_managers.gomod.Go.release", new_callable=mock.PropertyMock)
@mock.patch("cachito.workers.pkg_managers.gomod._get_gomod_version")
@mock.patch("cachito.workers.pkg_managers.gomod.get_golang_version")
@mock.patch("cachito.workers.pkg_managers.gomod.GoCacheTemporaryDirectory")
@mock.patch("subprocess.run")
@mock.patch("cachito.workers.pkg_managers.gomod.RequestBundleDir")
def test_resolve_gomod_vendor_dependencies(
    mock_bundle_dir: mock.Mock,
    mock_run: mock.Mock,
    mock_temp_dir: mock.Mock,
    mock_golang_version: mock.Mock,
    mock_get_gomod_version: mock.Mock,
    mock_go_release: mock.PropertyMock,
    force_gomod_tidy: bool,
    tmp_path: Path,
) -> None:
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmp_path)

    # Mock the "subprocess.run" calls
    run_side_effects = []
    run_side_effects.append(mock.Mock(returncode=0, stdout=None))  # go mod vendor
    if force_gomod_tidy:
        run_side_effects.append(mock.Mock(returncode=0, stdout=None))  # go mod tidy
    run_side_effects.append(
        # go list -m
        mock.Mock(returncode=0, stdout="github.com/cachito-testing/gomod-pandemonium")
    )
    run_side_effects.append(
        # go list -deps -json all
        mock.Mock(returncode=0, stdout=get_mocked_data("vendored/go_list_deps_all.json"))
    )
    run_side_effects.append(
        # go list -deps -json ./...
        mock.Mock(returncode=0, stdout=get_mocked_data("vendored/go_list_deps_threedot.json"))
    )
    mock_run.side_effect = run_side_effects

    mock_golang_version.return_value = "v0.1.0"
    mock_go_release.return_value = "go0.1.0"
    mock_get_gomod_version.return_value = "0.1.1"

    module_dir = tmp_path / "path/to/module"
    module_dir.joinpath("vendor").mkdir(parents=True)
    module_dir.joinpath("vendor/modules.txt").write_text(get_mocked_data("vendored/modules.txt"))

    request = {
        "id": 3,
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "flags": ["gomod-vendor"],
    }
    if force_gomod_tidy:
        request["flags"].append("force-gomod-tidy")

    gomod_ = gomod.resolve_gomod(str(module_dir), request)

    assert mock_run.call_args_list[0][0][0] == ["go", "mod", "vendor"]
    # when vendoring, go list should be called without -mod readonly
    assert mock_run.call_args_list[-2][0][0] == [
        "go",
        "list",
        "-e",
        "-deps",
        "-json=ImportPath,Module,Standard,Deps",
        "all",
    ]

    assert gomod_ == json.loads(get_mocked_data("expected-results/resolve_gomod_vendored.json"))

    # Ensure an empty directory is created at bundle_dir.gomod_download_dir
    mock_bundle_dir.return_value.gomod_download_dir.mkdir.assert_called_once_with(
        exist_ok=True, parents=True
    )


@mock.patch("cachito.workers.pkg_managers.gomod.Go.release", new_callable=mock.PropertyMock)
@mock.patch("cachito.workers.pkg_managers.gomod._get_gomod_version")
@mock.patch("cachito.workers.pkg_managers.gomod.GoCacheTemporaryDirectory")
@mock.patch("subprocess.run")
@mock.patch("cachito.workers.pkg_managers.gomod.get_golang_version")
@mock.patch("cachito.workers.pkg_managers.gomod.get_worker_config")
@pytest.mark.parametrize("strict_vendor", [True, False])
def test_resolve_gomod_strict_mode_raise_error(
    mock_gwc: mock.Mock,
    mock_golang_version: mock.Mock,
    mock_run: mock.Mock,
    mock_temp_dir: mock.Mock,
    mock_get_gomod_version: mock.Mock,
    mock_go_release: mock.PropertyMock,
    tmp_path: Path,
    strict_vendor: bool,
) -> None:
    # Mock the get_worker_config
    mock_config = mock.Mock()
    mock_config.cachito_gomod_strict_vendor = strict_vendor
    mock_config.cachito_athens_url = "http://athens:3000"
    mock_gwc.return_value = mock_config
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmp_path)
    mock_golang_version.return_value = "v2.1.1"
    mock_go_release.return_value = "go0.1.0"
    mock_get_gomod_version.return_value = "0.1.1"

    # Mock the "subprocess.run" calls
    mock_run.side_effect = [
        mock.Mock(returncode=0, stdout=""),  # go mod download
        mock.Mock(returncode=0, stdout="pizza"),  # go list -m
        mock.Mock(returncode=0, stdout=""),  # go list -deps -json all
        mock.Mock(returncode=0, stdout=""),  # go list -deps -json ./...
    ]

    module_dir = str(tmp_path)
    tmp_path.joinpath("vendor").mkdir()

    request = {"id": 3, "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848"}
    expected_error = (
        'The "gomod-vendor" or "gomod-vendor-check" flag must be set when your repository has '
        "vendored dependencies."
    )
    with strict_vendor and pytest.raises(ValidationError, match=expected_error) or nullcontext():
        gomod.resolve_gomod(module_dir, request)


@pytest.mark.parametrize("force_gomod_tidy", [False, True])
@mock.patch("cachito.workers.pkg_managers.gomod.Go.release", new_callable=mock.PropertyMock)
@mock.patch("cachito.workers.pkg_managers.gomod._get_gomod_version")
@mock.patch("cachito.workers.pkg_managers.gomod.get_golang_version")
@mock.patch("cachito.workers.pkg_managers.gomod.GoCacheTemporaryDirectory")
@mock.patch("cachito.workers.pkg_managers.gomod._merge_bundle_dirs")
@mock.patch("subprocess.run")
@mock.patch("os.makedirs")
def test_resolve_gomod_no_deps(
    mock_makedirs: mock.Mock,
    mock_run: mock.Mock,
    mock_merge_tree: mock.Mock,
    mock_temp_dir: mock.Mock,
    mock_golang_version: mock.Mock,
    mock_get_gomod_version: mock.Mock,
    mock_go_release: mock.PropertyMock,
    force_gomod_tidy: bool,
    tmp_path: Path,
) -> None:
    mock_pkg_deps_no_deps = dedent(
        """
        {
            "ImportPath": "github.com/release-engineering/retrodep/v2",
            "Module": {
                "Path": "github.com/release-engineering/retrodep/v2",
                "Main": true
            }
        }
        """
    )

    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmp_path)

    # Mock the "subprocess.run" calls
    run_side_effects = []
    run_side_effects.append(mock.Mock(returncode=0, stdout=""))  # go mod download -json
    if force_gomod_tidy:
        run_side_effects.append(mock.Mock(returncode=0, stdout=None))  # go mod tidy
    run_side_effects.append(
        mock.Mock(returncode=0, stdout="github.com/release-engineering/retrodep/v2")  # go list -m
    )
    run_side_effects.append(
        # go list -deps -json all
        mock.Mock(returncode=0, stdout=mock_pkg_deps_no_deps)
    )
    run_side_effects.append(
        # go list -deps -json ./...
        mock.Mock(returncode=0, stdout=mock_pkg_deps_no_deps)
    )
    mock_run.side_effect = run_side_effects

    mock_golang_version.return_value = "v2.1.1"
    mock_go_release.return_value = "go0.1.0"
    mock_get_gomod_version.return_value = "0.1.1"

    module_dir = str(tmp_path / "/path/to/module")

    request = {"id": 3, "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848"}
    if force_gomod_tidy:
        request["flags"] = ["force-gomod-tidy"]

    gomod_ = gomod.resolve_gomod(module_dir, request)

    assert gomod_["module"] == {
        "type": "gomod",
        "name": "github.com/release-engineering/retrodep/v2",
        "version": "v2.1.1",
    }
    assert not gomod_["module_deps"]
    assert len(gomod_["packages"]) == 1
    assert gomod_["packages"][0]["pkg"] == {
        "type": "go-package",
        "name": "github.com/release-engineering/retrodep/v2",
        "version": "v2.1.1",
    }
    assert not gomod_["packages"][0]["pkg_deps"]

    # The second one ensures the source cache directory exists
    mock_makedirs.assert_called_once_with(
        str(tmp_path / RequestBundleDir.go_mod_cache_download_part), exist_ok=True
    )

    bundle_dir = RequestBundleDir(request["id"])
    mock_merge_tree.assert_called_once_with(
        str(tmp_path / RequestBundleDir.go_mod_cache_download_part),
        str(bundle_dir.gomod_download_dir),
    )


@pytest.mark.parametrize(("go_mod_rc", "go_list_rc"), ((0, 1), (1, 0)))
@mock.patch("cachito.workers.pkg_managers.gomod.Go.release", new_callable=mock.PropertyMock)
@mock.patch("cachito.workers.pkg_managers.gomod._get_gomod_version")
@mock.patch("cachito.workers.pkg_managers.gomod.GoCacheTemporaryDirectory")
@mock.patch("cachito.workers.pkg_managers.gomod.get_worker_config")
@mock.patch("subprocess.run")
def test_go_list_cmd_failure(
    mock_run: mock.Mock,
    mock_worker_config: mock.Mock,
    mock_temp_dir: mock.Mock,
    mock_get_gomod_version: mock.Mock,
    mock_go_release: mock.PropertyMock,
    tmp_path: Path,
    go_mod_rc: int,
    go_list_rc: int,
) -> None:
    module_dir = "/path/to/module"
    request = {"id": 3, "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848"}

    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmp_path)
    mock_worker_config.return_value.cachito_gomod_download_max_tries = 1
    mock_go_release.return_value = "go0.1.0"
    mock_get_gomod_version.return_value = "0.1.1"

    # Mock the "subprocess.run" calls
    mock_run.side_effect = [
        mock.Mock(returncode=go_mod_rc, stdout=""),  # go mod download -json
        mock.Mock(returncode=go_list_rc, stdout=""),  # go list -m
    ]

    expect_error = "Go execution failed: "
    if go_mod_rc == 0:
        expect_error += "`go list -e -mod readonly -m` failed with rc=1"
    else:
        expect_error += "Cachito re-tried running `go mod download -json` command 1 times."

    with pytest.raises((CachitoCalledProcessError, GoModError), match=expect_error):
        gomod.resolve_gomod(module_dir, request)


@pytest.mark.parametrize(
    "module_suffix, ref, expected, subpath",
    (
        # First commit with no tag
        (
            "",
            "78510c591e2be635b010a52a7048b562bad855a3",
            "v0.0.0-20191107200220-78510c591e2b",
            None,
        ),
        # No prior tag at all
        (
            "",
            "5a6e50a1f0e3ce42959d98b3c3a2619cb2516531",
            "v0.0.0-20191107202433-5a6e50a1f0e3",
            None,
        ),
        # Only a non-semver tag (v1)
        (
            "",
            "7911d393ab186f8464884870fcd0213c36ecccaf",
            "v0.0.0-20191107202444-7911d393ab18",
            None,
        ),
        # Directly maps to a semver tag (v1.0.0)
        ("", "d1b74311a7bf590843f3b58bf59ab047a6f771ae", "v1.0.0", None),
        # One commit after a semver tag (v1.0.0)
        (
            "",
            "e92462c73bbaa21540f7385e90cb08749091b66f",
            "v1.0.1-0.20191107202936-e92462c73bba",
            None,
        ),
        # A semver tag (v2.0.0) without the corresponding go.mod bump, which happens after a v1.0.0
        # semver tag
        (
            "",
            "61fe6324077c795fc81b602ee27decdf4a4cf908",
            "v1.0.1-0.20191107202953-61fe6324077c",
            None,
        ),
        # A semver tag (v2.1.0) after the go.mod file was bumped
        ("/v2", "39006a0b5b0654a299cc43f71e0dc1aa50c2bc72", "v2.1.0", None),
        # A pre-release semver tag (v2.2.0-alpha)
        ("/v2", "0b3468852566617379215319c0f4dfe7f5948a8f", "v2.2.0-alpha", None),
        # Two commits after a pre-release semver tag (v2.2.0-alpha)
        (
            "/v2",
            "863073fae6efd5e04bb972a05db0b0706ec8276e",
            "v2.2.0-alpha.0.20191107204050-863073fae6ef",
            None,
        ),
        # Directly maps to a semver non-annotated tag (v2.2.0)
        ("/v2", "709b220511038f443fe1b26ac09c3e6c06c9f7c7", "v2.2.0", None),
        # A non-semver tag (random-tag)
        (
            "/v2",
            "37cea8ddd9e6b6b81c7cfbc3223ce243c078388a",
            "v2.2.1-0.20191107204245-37cea8ddd9e6",
            None,
        ),
        # The go.mod file is bumped but there is no versioned commit
        (
            "/v2",
            "6c7249e8c989852f2a0ee0900378d55d8e1d7fe0",
            "v2.0.0-20191108212303-6c7249e8c989",
            None,
        ),
        # Three semver annotated tags on the same commit
        ("/v2", "a77e08ced4d6ae7d9255a1a2e85bd3a388e61181", "v2.2.5", None),
        # A non-annotated semver tag and an annotated semver tag
        ("/v2", "bf2707576336626c8bbe4955dadf1916225a6a60", "v2.3.3", None),
        # Two non-annotated semver tags
        ("/v2", "729d0e6d60317bae10a71fcfc81af69a0f6c07be", "v2.4.1", None),
        # Two semver tags, with one having the wrong major version and the other with the correct
        # major version
        ("/v2", "3decd63971ed53a5b7ff7b2ca1e75f3915e99cf2", "v2.5.0", None),
        # A semver tag that is incorrectly lower then the preceding semver tag
        ("/v2", "0dd249ad59176fee9b5451c2f91cc859e5ddbf45", "v2.0.1", None),
        # A commit after the incorrect lower semver tag
        (
            "/v2",
            "2883f3ddbbc811b112ff1fe51ba2ee7596ddbf24",
            "v2.5.1-0.20191118190931-2883f3ddbbc8",
            None,
        ),
        # Newest semver tag is applied to a submodule, but the root module is being processed
        (
            "/v2",
            "f3ee3a4a394fb44b055ed5710b8145e6e98c0d55",
            "v2.5.1-0.20211209210936-f3ee3a4a394f",
            None,
        ),
        # Submodule has a semver tag applied to it
        ("/v2", "f3ee3a4a394fb44b055ed5710b8145e6e98c0d55", "v2.5.1", "submodule"),
        # A commit after a submodule tag
        (
            "/v2",
            "cc6c9f554c0982786ff9e077c2b37c178e46828c",
            "v2.5.2-0.20211223131312-cc6c9f554c09",
            "submodule",
        ),
        # A commit with multiple tags in different submodules
        ("/v2", "5401bdd8a8ebfcccd2eea9451d407a5fdae6fc76", "v2.5.3", "submodule"),
        # Malformed semver tag, root module being processed
        ("/v2", "4a481f0bae82adef3ea6eae3d167af6e74499cb2", "v2.6.0", None),
        # Malformed semver tag, submodule being processed
        ("/v2", "4a481f0bae82adef3ea6eae3d167af6e74499cb2", "v2.6.0", "submodule"),
    ),
)
def test_get_golang_version(tmpdir, module_suffix, ref, expected, subpath):
    # Extract the Git repository of a Go module to verify the correct versions are computed
    repo_archive_path = os.path.join(os.path.dirname(__file__), "golang_git_repo.tar.gz")
    with tarfile.open(repo_archive_path, "r:*") as archive:
        safe_extract(archive, tmpdir)
    repo_path = os.path.join(tmpdir, "golang_git_repo")

    module_name = f"github.com/mprahl/test-golang-pseudo-versions{module_suffix}"
    version = gomod.get_golang_version(module_name, repo_path, ref, subpath=subpath)
    assert version == expected


@pytest.mark.parametrize(
    "tree_1, tree_2, result_tree, merge_file_executions",
    (
        (
            {"foo": {"bar": {"baz": "buzz"}}},
            {"foo": {"baz": {"bar": "buzz"}}},
            {"foo": {"baz": {"bar": "buzz"}, "bar": {"baz": "buzz"}}},
            0,
        ),
        (
            {"foo": {"bar": {"list.lock": ""}}},
            {"foo": {"bar": {"list.lock": ""}}},
            {"foo": {"bar": {"list.lock": ""}}},
            0,
        ),
        (
            {"foo": {"bar": {"list": "buzz v0.0.1", "list.lock": ""}}},
            {"foo": {"baz": {"list": "buzz v0.0.2", "list.lock": ""}}},
            {
                "foo": {
                    "baz": {"list": "buzz v0.0.2", "list.lock": ""},
                    "bar": {"list": "buzz v0.0.1", "list.lock": ""},
                }
            },
            0,
        ),
        (
            # If a file exists in both directories, the destination will not be overwritten
            {"foo": {"bar": {"list": "buzz v0.0.1"}}},
            {"foo": {"bar": {"list": "buzz v0.0.2"}}},
            {"foo": {"bar": {"list": "buzz v0.0.2"}}},
            0,
        ),
        (
            # This has a valid merging of list, but the file is not going to be
            # actually merged since we are mocking it out. The content of the file
            # will be identical to the destination file.
            {"foo": {"bar": {"list": "buzz v0.0.1", "list.lock": ""}}},
            {"foo": {"bar": {"list": "buzz v0.0.2", "list.lock": ""}}},
            {"foo": {"bar": {"list": "buzz v0.0.2", "list.lock": ""}}},
            1,
        ),
    ),
)
@mock.patch("cachito.workers.pkg_managers.gomod._merge_files")
def test_merge_bundle_dirs(mock_merge_files, tree_1, tree_2, result_tree, merge_file_executions):
    with tempDir() as dir_1, tempDir() as dir_2, tempDir() as dir_3:
        write_file_tree(tree_1, dir_1)
        write_file_tree(tree_2, dir_2)
        write_file_tree(result_tree, dir_3)
        gomod._merge_bundle_dirs(dir_1, dir_2)
        assert_directories_equal(dir_2, dir_3)
    assert mock_merge_files.call_count == merge_file_executions


@pytest.mark.parametrize(
    "file_1_content, file_2_content, result_file_content",
    (
        (
            dedent(
                """\
                package1: v1.0.0
                package2 -- 1.2.3-gah!
                """
            ),
            dedent(
                """\
                package1: v1.0.0
                package3: v0.1.0-incompatible
                """
            ),
            dedent(
                """\
                package1: v1.0.0
                package2 -- 1.2.3-gah!
                package3: v0.1.0-incompatible
                """
            ),
        ),
        (
            dedent(
                """\
                package1: v1.0.0\n
                """
            ),
            dedent(
                """\
                package1: v1.0.0 """
            ),
            dedent(
                """\
                package1: v1.0.0
                """
            ),
        ),
    ),
)
def test_merge_files(file_1_content, file_2_content, result_file_content):
    with tempDir() as dir_1, tempDir() as dir_2, tempDir() as dir_3:
        write_file_tree({"list": file_1_content}, dir_1)
        write_file_tree({"list": file_2_content}, dir_2)
        write_file_tree({"list": result_file_content}, dir_3)
        gomod._merge_files("{}/list".format(dir_1), "{}/list".format(dir_2))
        with open("{}/list".format(dir_2), "r") as f:
            print(f.read())
        with open("{}/list".format(dir_3), "r") as f:
            print(f.read())
        assert_directories_equal(dir_2, dir_3)


@mock.patch("cachito.workers.pkg_managers.gomod._validate_local_dependency_path")
@mock.patch("cachito.workers.pkg_managers.gomod._fail_unless_allowed")
def test_vet_local_deps(mock_fail_allowlist, mock_validate_dep_path):
    dependencies = [
        {"name": "foo", "version": "./local/foo"},
        {"name": "bar", "version": "v1.0.0"},
        {"name": "baz", "version": "./local/baz"},
    ]
    module_name = "some-module"
    app_dir = "/repo/some-module"
    git_dir = "/repo"
    mock_validate_dep_path.return_value = None

    gomod._vet_local_deps(dependencies, module_name, ["foo", "baz"], app_dir, git_dir)

    mock_fail_allowlist.assert_has_calls(
        [
            mock.call("some-module", "foo", ["foo", "baz"]),
            mock.call("some-module", "baz", ["foo", "baz"]),
        ],
    )
    mock_validate_dep_path.assert_has_calls(
        [
            mock.call(app_dir, git_dir, "./local/foo"),
            mock.call(app_dir, git_dir, "./local/baz"),
        ],
    )


@pytest.mark.parametrize(
    "platform_specific_path",
    [
        "/home/user/go/src/k8s.io/kubectl",
        "\\Users\\user\\go\\src\\k8s.io\\kubectl",
        "C:\\Users\\user\\go\\src\\k8s.io\\kubectl",
    ],
)
def test_vet_local_deps_abspath(platform_specific_path):
    dependencies = [{"name": "foo", "version": platform_specific_path}]
    app_dir = "/some/path"

    expect_error = re.escape(
        f"Absolute paths to gomod dependencies are not supported: {platform_specific_path}"
    )
    with pytest.raises(UnsupportedFeature, match=expect_error):
        gomod._vet_local_deps(dependencies, "some-module", [], app_dir, app_dir)


@pytest.mark.parametrize(
    "app_dir, git_dir, dep_path, expect_error",
    [
        ("foo", "foo", "./..", True),
        ("foo/bar", "foo", "./..", False),
        ("foo/bar", "foo", "./../..", True),
    ],
)
def test_validate_local_dependency_path(
    tmp_path: Path, app_dir: str, git_dir: str, dep_path: str, expect_error: bool
):
    tmp_git_dir = tmp_path / git_dir
    tmp_app_dir = tmp_path / app_dir
    tmp_app_dir.mkdir(parents=True, exist_ok=True)

    if expect_error:
        with pytest.raises(ValidationError):
            gomod._validate_local_dependency_path(tmp_app_dir, tmp_git_dir, dep_path)
    else:
        gomod._validate_local_dependency_path(tmp_app_dir, tmp_git_dir, dep_path)


@pytest.mark.parametrize(
    "module_name, package_name, allowed_patterns, expect_error",
    [
        ("example/module", "example/package", ["example/package"], None),
        ("example/module", "example/package", ["example/*"], None),
        ("example/module", "example/package", ["*/package"], None),
        ("example/module", "example/package", ["*/*"], None),
        ("example/module", "example/package", ["*"], None),
        ("example/module", "example/module/submodule", [], None),
        ("example/module/v1", "example/module/submodule", [], None),
        ("example/module/v1", "example/module/submodule/v2", [], None),
        ("example/module", "example/module/submodule/v1", [], None),
        (
            "example/module",
            "example/package",
            [],
            "The module example/module is not allowed to replace example/package",
        ),
        (
            "example/module",
            "example/package",
            ["example"],
            "The module example/module is not allowed to replace example/package",
        ),
        (
            "example/module",
            "example/package",
            ["package"],
            "The module example/module is not allowed to replace example/package",
        ),
        (
            "example/module",
            "example/package",
            ["other-example/*"],
            "The module example/module is not allowed to replace example/package",
        ),
        (
            "example/module",
            "example/package",
            ["*/other-package"],
            "The module example/module is not allowed to replace example/package",
        ),
        (
            "example/module",
            "example/package",
            ["example/package/*"],
            "The module example/module is not allowed to replace example/package",
        ),
        (
            "example/module",
            "example/package",
            ["*/example/package"],
            "The module example/module is not allowed to replace example/package",
        ),
        (
            "example/module/v",
            "example/module/submodule",
            [],
            "The module example/module/v is not allowed to replace example/module/submodule",
        ),
    ],
)
def test_fail_unless_allowed(module_name, package_name, allowed_patterns, expect_error):
    if expect_error:
        with pytest.raises(UnsupportedFeature, match=re.escape(expect_error)):
            gomod._fail_unless_allowed(module_name, package_name, allowed_patterns)
    else:
        gomod._fail_unless_allowed(module_name, package_name, allowed_patterns)


@pytest.mark.parametrize(
    "main_module_deps, pkg_deps_pre, pkg_deps_post",
    [
        (
            # module deps
            [{"name": "example.org/foo", "version": "./src/foo"}],
            # package deps pre
            [{"name": "example.org/foo", "version": "./src/foo"}],
            # package deps post (package name was the same as module name, no change)
            [{"name": "example.org/foo", "version": "./src/foo"}],
        ),
        (
            [{"name": "example.org/foo", "version": "./src/foo"}],
            [{"name": "example.org/foo/bar", "version": "./src/foo"}],
            # path is changed
            [{"name": "example.org/foo/bar", "version": "./src/foo/bar"}],
        ),
        (
            [{"name": "example.org/foo", "version": "./src/foo"}],
            [
                {"name": "example.org/foo/bar", "version": "./src/foo"},
                {"name": "example.org/foo/bar/baz", "version": "./src/foo"},
            ],
            # both packages match, both paths are changed
            [
                {"name": "example.org/foo/bar", "version": "./src/foo/bar"},
                {"name": "example.org/foo/bar/baz", "version": "./src/foo/bar/baz"},
            ],
        ),
        (
            [
                {"name": "example.org/foo", "version": "./src/foo"},
                {"name": "example.org/foo/bar", "version": "./src/bar"},
            ],
            [{"name": "example.org/foo/bar", "version": "./src/bar"}],
            # longer match wins, no change
            [{"name": "example.org/foo/bar", "version": "./src/bar"}],
        ),
        (
            [
                {"name": "example.org/foo", "version": "./src/foo"},
                {"name": "example.org/foo/bar", "version": "./src/bar"},
            ],
            [{"name": "example.org/foo/bar/baz", "version": "./src/bar"}],
            # longer match wins, path is changed
            [{"name": "example.org/foo/bar/baz", "version": "./src/bar/baz"}],
        ),
        (
            [
                {"name": "example.org/foo", "version": "./src/foo"},
                {"name": "example.org/foo/bar", "version": "v1.0.0"},
            ],
            [{"name": "example.org/foo/bar", "version": "./src/foo"}],
            # longer match does not have a local replacement, shorter match used
            # this can happen if replacement is only applied to a specific version of a module
            [{"name": "example.org/foo/bar", "version": "./src/foo/bar"}],
        ),
        (
            [{"name": "example.org/foo", "version": "./src/foo"}],
            [{"name": "example.org/foo/bar", "version": "v1.0.0"}],
            # Package does not have a local replacement, no change
            [{"name": "example.org/foo/bar", "version": "v1.0.0"}],
        ),
    ],
)
def test_set_full_local_dep_relpaths(main_module_deps, pkg_deps_pre, pkg_deps_post):
    gomod._set_full_local_dep_relpaths(pkg_deps_pre, main_module_deps)
    # pkg_deps_pre should be modified in place
    assert pkg_deps_pre == pkg_deps_post


def test_set_full_local_dep_relpaths_no_match():
    pkg_deps = [{"name": "example.org/foo", "version": "./src/foo"}]
    err_msg = "Could not find parent Go module for local dependency: example.org/foo"

    with pytest.raises(RuntimeError, match=err_msg):
        gomod._set_full_local_dep_relpaths(pkg_deps, [])


@pytest.mark.parametrize(
    "allowlist, module_name, expect_allowed",
    [
        (
            # simple match
            {"example.org/foo": ["example.org/*"]},
            "example.org/foo",
            ["example.org/*"],
        ),
        (
            # versionless match
            {"example.org/foo": ["example.org/*"]},
            "example.org/foo/v2",
            ["example.org/*"],
        ),
        (
            # simple match
            {"example.org/foo/v2": ["example.org/*"]},
            "example.org/foo/v2",
            ["example.org/*"],
        ),
        (
            # simple match beats versionless match
            {"example.org/foo/v2": ["example.org/foo/v2/*"], "example.org/foo": ["example.org/*"]},
            "example.org/foo/v2",
            ["example.org/foo/v2/*"],
        ),
        (
            # no match
            {"example.org/foo": ["example.org/*"]},
            "example.org/foo/bar",
            [],
        ),
        (
            # no match
            {"example.org/foo/v2": ["example.org/*"]},
            "example.org/foo",
            [],
        ),
        (
            # no match
            {"example.org/foo/v2": ["example.org/*"]},
            "example.org/foo/v3",
            [],
        ),
    ],
)
@mock.patch("cachito.workers.pkg_managers.gomod.get_worker_config")
def test_get_allowed_local_deps(mock_worker_config, allowlist, module_name, expect_allowed):
    mock_worker_config.return_value.cachito_gomod_file_deps_allowlist = allowlist
    assert gomod._get_allowed_local_deps(module_name) == expect_allowed


@pytest.mark.parametrize(
    "parent_name, package_name, expect_result",
    [
        ("github.com/foo", "github.com/foo", True),
        ("github.com/foo", "github.com/foo/bar", True),
        ("github.com/foo", "github.com/bar", False),
        ("github.com/foo", "github.com/foobar", False),
        ("github.com/foo/bar", "github.com/foo", False),
    ],
)
def test_contains_package(parent_name, package_name, expect_result):
    assert gomod.contains_package(parent_name, package_name) == expect_result


@pytest.mark.parametrize(
    "parent, subpackage, expect_path",
    [
        ("github.com/foo", "github.com/foo", ""),
        ("github.com/foo", "github.com/foo/bar", "bar"),
        ("github.com/foo", "github.com/foo/bar/baz", "bar/baz"),
        ("github.com/foo/bar", "github.com/foo/bar/baz", "baz"),
        ("github.com/foo", "github.com/foo/github.com/foo", "github.com/foo"),
    ],
)
def test_path_to_subpackage(parent, subpackage, expect_path):
    assert gomod.path_to_subpackage(parent, subpackage) == expect_path


def test_path_to_subpackage_not_a_subpackage():
    with pytest.raises(ValueError, match="Package github.com/b does not belong to github.com/a"):
        gomod.path_to_subpackage("github.com/a", "github.com/b")


@pytest.mark.parametrize(
    "package_name, module_names, expect_parent_module",
    [
        ("github.com/foo/bar", ["github.com/foo/bar"], "github.com/foo/bar"),
        ("github.com/foo/bar", [], None),
        ("github.com/foo/bar", ["github.com/spam/eggs"], None),
        ("github.com/foo/bar/baz", ["github.com/foo/bar"], "github.com/foo/bar"),
        (
            "github.com/foo/bar/baz",
            ["github.com/foo/bar", "github.com/foo/bar/baz"],
            "github.com/foo/bar/baz",
        ),
        ("github.com/foo/bar", {"github.com/foo/bar": 1}, "github.com/foo/bar"),
    ],
)
def test_match_parent_module(package_name, module_names, expect_parent_module):
    assert gomod.match_parent_module(package_name, module_names) == expect_parent_module


@pytest.mark.parametrize(
    "flags, vendor_exists, expect_result",
    [
        # no flags => should not vendor, cannot modify (irrelevant)
        ([], True, (False, False)),
        ([], False, (False, False)),
        # gomod-vendor => should vendor, can modify
        (["gomod-vendor"], True, (True, True)),
        (["gomod-vendor"], False, (True, True)),
        # gomod-vendor-check, vendor exists => should vendor, cannot modify
        (["gomod-vendor-check"], True, (True, False)),
        # gomod-vendor-check, vendor does not exist => should vendor, can modify
        (["gomod-vendor-check"], False, (True, True)),
        # both vendor flags => gomod-vendor-check takes priority
        (["gomod-vendor", "gomod-vendor-check"], True, (True, False)),
    ],
)
def test_should_vendor_deps(flags, vendor_exists, expect_result, tmp_path):
    if vendor_exists:
        tmp_path.joinpath("vendor").mkdir()

    assert gomod._should_vendor_deps(flags, str(tmp_path), False) == expect_result


@pytest.mark.parametrize(
    "flags, vendor_exists, expect_error",
    [
        ([], True, True),
        ([], False, False),
        (["gomod-vendor"], True, False),
        (["gomod-vendor-check"], True, False),
    ],
)
def test_should_vendor_deps_strict(flags, vendor_exists, expect_error, tmp_path):
    if vendor_exists:
        tmp_path.joinpath("vendor").mkdir()

    if expect_error:
        msg = 'The "gomod-vendor" or "gomod-vendor-check" flag must be set'
        with pytest.raises(ValidationError, match=msg):
            gomod._should_vendor_deps(flags, str(tmp_path), True)
    else:
        gomod._should_vendor_deps(flags, str(tmp_path), True)


@pytest.mark.parametrize("can_make_changes", [True, False])
@pytest.mark.parametrize("vendor_changed", [True, False])
@mock.patch("cachito.workers.pkg_managers.gomod.Go._run")
@mock.patch("cachito.workers.pkg_managers.gomod._vendor_changed")
def test_vendor_deps(mock_vendor_changed, mock_run, can_make_changes, vendor_changed):
    git_dir = "/fake/repo"
    app_dir = "/fake/repo/some/app"
    run_params = {"cwd": app_dir}
    mock_vendor_changed.return_value = vendor_changed
    expect_error = vendor_changed and not can_make_changes

    if expect_error:
        msg = "The content of the vendor directory is not consistent with go.mod."
        with pytest.raises(ValidationError, match=msg):
            gomod._vendor_deps(gomod.Go(), run_params, can_make_changes, git_dir)
    else:
        gomod._vendor_deps(gomod.Go(), run_params, can_make_changes, git_dir)

    mock_run.assert_called_once_with(["go", "mod", "vendor"], **run_params)
    if not can_make_changes:
        mock_vendor_changed.assert_called_once_with(git_dir, app_dir)


def test_parse_vendor(tmp_path: Path) -> None:
    modules_txt = tmp_path / "vendor/modules.txt"
    modules_txt.parent.mkdir(parents=True)
    modules_txt.write_text(get_mocked_data("vendored/modules.txt"))
    expect_modules = [
        gomod.GoModule(
            path="github.com/Azure/go-ansiterm", version="v0.0.0-20210617225240-d185dfc1b5a1"
        ),
        gomod.GoModule(path="github.com/Masterminds/semver", version="v1.4.2"),
        gomod.GoModule(path="github.com/Microsoft/go-winio", version="v0.6.0"),
        gomod.GoModule(
            path="github.com/cachito-testing/gomod-pandemonium/terminaltor",
            version="v0.0.0",
            replace=gomod.GoModule(path="./terminaltor"),
        ),
        gomod.GoModule(
            path="github.com/cachito-testing/gomod-pandemonium/weird",
            version="v0.0.0",
            replace=gomod.GoModule(path="./weird"),
        ),
        gomod.GoModule(path="github.com/go-logr/logr", version="v1.2.3"),
        gomod.GoModule(
            path="github.com/go-task/slim-sprig", version="v0.0.0-20230315185526-52ccab3ef572"
        ),
        gomod.GoModule(path="github.com/google/go-cmp", version="v0.5.9"),
        gomod.GoModule(
            path="github.com/google/pprof", version="v0.0.0-20210407192527-94a9f03dee38"
        ),
        gomod.GoModule(path="github.com/moby/term", version="v0.0.0-20221205130635-1aeaba878587"),
        gomod.GoModule(path="github.com/onsi/ginkgo/v2", version="v2.9.2"),
        gomod.GoModule(path="github.com/onsi/gomega", version="v1.27.4"),
        gomod.GoModule(
            path="github.com/op/go-logging", version="v0.0.0-20160315200505-970db520ece7"
        ),
        gomod.GoModule(path="github.com/pkg/errors", version="v0.8.1"),
        gomod.GoModule(
            path="github.com/release-engineering/retrodep/v2",
            version="v2.1.0",
            replace=gomod.GoModule(path="github.com/cachito-testing/retrodep/v2", version="v2.1.1"),
        ),
        gomod.GoModule(path="golang.org/x/mod", version="v0.9.0"),
        gomod.GoModule(path="golang.org/x/net", version="v0.8.0"),
        gomod.GoModule(path="golang.org/x/sys", version="v0.6.0"),
        gomod.GoModule(path="golang.org/x/text", version="v0.8.0"),
        gomod.GoModule(path="golang.org/x/tools", version="v0.7.0"),
        gomod.GoModule(path="gopkg.in/yaml.v2", version="v2.2.2"),
        gomod.GoModule(path="gopkg.in/yaml.v3", version="v3.0.1"),
    ]
    assert gomod._parse_vendor(tmp_path) == expect_modules


@pytest.mark.parametrize(
    "file_content, expect_error_msg",
    [
        ("#invalid-line", "vendor/modules.txt: unexpected format: '#invalid-line'"),
        ("# main-module", "vendor/modules.txt: unexpected module line format: '# main-module'"),
        (
            "github.com/x/package",
            "vendor/modules.txt: package has no parent module: github.com/x/package",
        ),
    ],
)
def test_parse_vendor_unexpected_format(
    file_content: str, expect_error_msg: str, tmp_path: Path
) -> None:
    modules_txt = tmp_path / "vendor/modules.txt"
    modules_txt.parent.mkdir(parents=True)
    modules_txt.write_text(file_content)

    with pytest.raises(InvalidFileFormat, match=expect_error_msg):
        gomod._parse_vendor(tmp_path)


@pytest.mark.parametrize("subpath", ["", "some/app/"])
@pytest.mark.parametrize(
    "vendor_before, vendor_changes, expected_change",
    [
        # no vendor/ dirs
        ({}, {}, None),
        # no changes
        ({"vendor": {"modules.txt": "foo v1.0.0\n"}}, {}, None),
        # vendor/modules.txt was added
        (
            {},
            {"vendor": {"modules.txt": "foo v1.0.0\n"}},
            dedent(
                """
                --- /dev/null
                +++ b/{subpath}vendor/modules.txt
                @@ -0,0 +1 @@
                +foo v1.0.0
                """
            ),
        ),
        # vendor/modules.txt changed
        (
            {"vendor": {"modules.txt": "foo v1.0.0\n"}},
            {"vendor": {"modules.txt": "foo v2.0.0\n"}},
            dedent(
                """
                --- a/{subpath}vendor/modules.txt
                +++ b/{subpath}vendor/modules.txt
                @@ -1 +1 @@
                -foo v1.0.0
                +foo v2.0.0
                """
            ),
        ),
        # vendor/some_file was added
        (
            {},
            {"vendor": {"some_file": "foo"}},
            dedent(
                """
                A\t{subpath}vendor/some_file
                """
            ),
        ),
        # multiple additions and modifications
        (
            {"vendor": {"some_file": "foo"}},
            {"vendor": {"some_file": "bar", "other_file": "baz"}},
            dedent(
                """
                A\t{subpath}vendor/other_file
                M\t{subpath}vendor/some_file
                """
            ),
        ),
        # vendor/ was added but only contains empty dirs => will be ignored
        ({}, {"vendor": {"empty_dir": {}}}, None),
        # change will be tracked even if vendor/ is .gitignore'd
        (
            {".gitignore": "vendor/"},
            {"vendor": {"some_file": "foo"}},
            dedent(
                """
                A\t{subpath}vendor/some_file
                """
            ),
        ),
    ],
)
def test_vendor_changed(subpath, vendor_before, vendor_changes, expected_change, fake_repo, caplog):
    repo_dir, _ = fake_repo
    repo = git.Repo(repo_dir)

    app_dir = os.path.join(repo_dir, subpath)
    os.makedirs(app_dir, exist_ok=True)

    write_file_tree(vendor_before, app_dir)
    repo.index.add(os.path.join(app_dir, path) for path in vendor_before)
    repo.index.commit("before vendoring")

    write_file_tree(vendor_changes, app_dir, exist_ok=True)

    assert gomod._vendor_changed(repo_dir, app_dir) == bool(expected_change)
    if expected_change:
        assert expected_change.format(subpath=subpath) in caplog.text

    # The _vendor_changed function should reset the `git add` => added files should not be tracked
    assert not repo.git.diff("--diff-filter", "A")


@pytest.mark.parametrize(
    "go_mod_file, go_mod_version",
    [("go 1.21", "1.21"), ("    go    1.21.4    ", "1.21.4")],
    indirect=["go_mod_file"],
)
def test_get_gomod_version(tmp_path: Path, go_mod_file: Path, go_mod_version: str) -> None:
    assert gomod._get_gomod_version(tmp_path) == go_mod_version


@pytest.mark.parametrize(
    "go_mod_file",
    [pytest.param(_, id=_) for _ in ["go1.21", "go 1.21.0.100", "1.21", "go 1.21 foo"]],
    indirect=True,
)
def test_get_gomod_version_fail(tmp_path: Path, go_mod_file: Path) -> None:
    assert gomod._get_gomod_version(tmp_path) is None


@pytest.mark.parametrize(
    "base_release,modfile_version,used_version",
    [
        pytest.param("go1.20.7", "1.20", "1.20.7", id="old_base_version_equals_modfile"),
        pytest.param("go1.21.4", "1.21.0", "1.21.4", id="new_base_version_equals_modfile"),
        pytest.param("go1.20.7", "1.21.0", "1.21.0", id="modfile_requires_newer_toolchain"),
        pytest.param("go1.21.4", "1.21.9", "1.21.4", id="modfile_requires_newer_121_toolchain"),
        pytest.param("go1.21.4", "1.20", "1.20", id="modfile_requires_older_toolchain"),
        pytest.param("go1.20", None, "1.20", id="no_modfile_version_use_base_toolchain"),
        pytest.param("go1.21.4", None, "1.20", id="no_modfile_version_use_older_toolchain"),
    ],
)
@mock.patch("cachito.workers.pkg_managers.gomod._get_gomod_version")
@mock.patch("cachito.workers.pkg_managers.gomod.Go._run")
def test_select_go_toolchain(
    mock_go_run: mock.Mock,
    mock_get_gomod_version: mock.Mock,
    tmp_path: Path,
    base_release: str,
    modfile_version: str,
    used_version: str,
) -> None:
    mock_go_run.return_value = base_release
    mock_get_gomod_version.return_value = modfile_version

    go = gomod._select_go_toolchain(tmp_path)
    assert go.version == Version(used_version)


class TestGo:
    @pytest.mark.parametrize(
        "bin_, params",
        [
            pytest.param(None, {}, id="bundled_go_no_params"),
            pytest.param("/usr/bin/go1.21", {}, id="custom_go_no_params"),
            pytest.param(None, {"cwd": "/foo/bar"}, id="bundled_go_params"),
            pytest.param(
                "/usr/bin/go1.21",
                {
                    "env": {"GOCACHE": "/foo", "GOTOOLCHAIN": "local"},
                    "cwd": "/foo/bar",
                    "text": True,
                },
                id="custom_go_params",
            ),
        ],
    )
    @mock.patch("cachito.workers.pkg_managers.gomod.run_cmd")
    def test_run(
        self,
        mock_run: mock.Mock,
        bin_: str,
        params: dict,
    ) -> None:
        if not bin_:
            go = gomod.Go(bin_)
        else:
            go = gomod.Go()

        cmd = [go._bin, "mod", "download"]
        go._run(cmd, **params)
        mock_run.assert_called_once_with(cmd, params)

    @pytest.mark.parametrize(
        "bin_, params, tries_needed",
        [
            pytest.param(None, {}, 1, id="bundled_go_1_try"),
            pytest.param("/usr/bin/go1.21", {}, 2, id="custom_go_2_tries"),
            pytest.param(
                None,
                {
                    "env": {"GOCACHE": "/foo", "GOTOOLCHAIN": "local"},
                    "cwd": "/foo/bar",
                    "text": True,
                },
                5,
                id="bundled_go_params_5_tries",
            ),
        ],
    )
    @mock.patch("cachito.workers.pkg_managers.gomod.get_worker_config")
    @mock.patch("cachito.workers.pkg_managers.gomod.run_cmd")
    @mock.patch("time.sleep")
    def test_retry(
        self,
        mock_sleep: mock.Mock,
        mock_run: mock.Mock,
        mock_config: mock.Mock,
        bin_: str,
        params: dict,
        tries_needed: int,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        mock_config.return_value.gomod_download_max_tries = 5

        # We don't want to mock subprocess.run here, because:
        # 1) the call chain looks like this: Go()._retry->run_go->self._run->run_cmd->subprocess.run
        # 2) we wouldn't be able to check if params are propagated correctly since run_cmd adds
        #    some too
        failure = CachitoCalledProcessError("foo", retcode=1)
        success = 1
        mock_run.side_effect = [failure for _ in range(tries_needed - 1)] + [success]

        if bin_:
            go = gomod.Go(bin_)
        else:
            go = gomod.Go()

        cmd = [go._bin, "mod", "download"]
        go._retry(cmd, **params)
        mock_run.assert_called_with(cmd, params)
        assert mock_run.call_count == tries_needed
        assert mock_sleep.call_count == tries_needed - 1

        for n in range(tries_needed - 1):
            wait = 2**n
            assert f"Backing off run_go(...) for {wait:.1f}s" in caplog.text

    @mock.patch("cachito.workers.pkg_managers.gomod.get_worker_config")
    @mock.patch("cachito.workers.pkg_managers.gomod.run_cmd")
    @mock.patch("time.sleep")
    def test_retry_failure(
        self, mock_sleep: Any, mock_run: Any, mock_config: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_config.return_value.cachito_gomod_download_max_tries = 5

        failure = CachitoCalledProcessError("foo", retcode=1)
        mock_run.side_effect = [failure] * 5
        go = gomod.Go()

        error_msg = (
            f"Go execution failed: Cachito re-tried running `{go._bin} mod download` command "
            "5 times."
        )

        with pytest.raises(GoModError, match=error_msg):
            go._retry([go._bin, "mod", "download"])

        assert mock_run.call_count == 5
        assert mock_sleep.call_count == 4

        assert "Backing off run_go(...) for 1.0s" in caplog.text
        assert "Backing off run_go(...) for 2.0s" in caplog.text
        assert "Backing off run_go(...) for 4.0s" in caplog.text
        assert "Backing off run_go(...) for 8.0s" in caplog.text
        assert "Giving up run_go(...) after 5 tries" in caplog.text

    @pytest.mark.parametrize(
        "release, retry",
        [
            pytest.param(None, False, id="bundled_go"),
            pytest.param("go1.20", True, id="custom_release"),
        ],
    )
    @mock.patch("cachito.workers.pkg_managers.gomod.get_worker_config")
    @mock.patch("cachito.workers.pkg_managers.gomod.Go._retry")
    @mock.patch("cachito.workers.pkg_managers.gomod.Go._run")
    def test_call(
        self,
        mock_run: mock.Mock,
        mock_retry: mock.Mock,
        mock_get_config: mock.Mock,
        tmp_path: Path,
        release: Optional[str],
        retry: bool,
    ) -> None:

        env = {"env": {"GOTOOLCHAIN": "local", "GOCACHE": "foo", "GOPATH": "bar"}}
        opts = ["mod", "download"]
        go = gomod.Go(release=release)
        go(opts, retry=retry, params=env)

        cmd = [go._bin, *opts]

        if release:
            assert go._bin == f"/usr/local/go/{release}/bin/go"
        if not retry:
            mock_run.assert_called_once_with(cmd, **env)
        else:
            mock_get_config.return_value.gomod_download_max_tries = 1
            mock_retry.assert_called_once_with(cmd, **env)

    @pytest.mark.parametrize("retry", [False, True])
    @mock.patch("cachito.workers.pkg_managers.gomod.get_worker_config")
    @mock.patch("subprocess.run")
    def test_call_failure(
        self,
        mock_run: mock.Mock,
        mock_get_config: mock.Mock,
        retry: bool,
    ) -> None:
        ntries = 1
        mock_get_config.return_value.cachito_gomod_download_max_tries = ntries
        failure = subprocess.CompletedProcess(args="", returncode=1, stdout="")
        mock_run.side_effect = [failure]

        opts = ["mod", "download"]
        cmd = ["go", *opts]
        error_msg = "Go execution failed: "
        if retry:
            error_msg += f"Cachito re-tried running `{' '.join(cmd)}` command {ntries} times."
        else:
            error_msg += f"`{' '.join(cmd)}` failed with rc=1"

        with pytest.raises(GoModError, match=error_msg):
            go = gomod.Go()
            go(opts, retry=retry)

        assert mock_run.call_count == 1

    @pytest.mark.parametrize(
        "release, expect, go_output",
        [
            pytest.param("go1.20", "go1.20", None, id="explicit_release"),
            pytest.param(
                None, "go1.21.4", "go version go1.21.4 linux/amd64", id="parse_from_output"
            ),
            pytest.param(
                None,
                "go1.21.4",
                "go   version\tgo1.21.4 \t\t linux/amd64",
                id="parse_from_output_white_spaces",
            ),
        ],
    )
    @mock.patch("cachito.workers.pkg_managers.gomod.Go._run")
    def test_release(
        self,
        mock_run: mock.Mock,
        release: Optional[str],
        expect: str,
        go_output: str,
    ) -> None:
        mock_run.return_value = go_output

        go = gomod.Go(release=release)
        assert go.release == expect

    @mock.patch("cachito.workers.pkg_managers.gomod.Go._run")
    def test_release_failure(self, mock_run: mock.Mock) -> None:
        go_output = "go mangled version 1.21_4"
        mock_run.return_value = go_output

        error_msg = f"Could not extract Go toolchain version from Go's output: '{go_output}'"
        with pytest.raises(GoModError, match=error_msg):
            gomod.Go(release=None).release
