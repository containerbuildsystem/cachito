# SPDX-License-Identifier: GPL-3.0-or-later
import os
import re
import tarfile
import textwrap
from contextlib import nullcontext
from tempfile import TemporaryDirectory as tempDir
from textwrap import dedent
from unittest import mock

import git
import pytest

from cachito.common.packages_data import _package_sort_key
from cachito.errors import CachitoError, ValidationError
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers import gomod
from cachito.workers.pkg_managers.gomod import (
    _fail_unless_allowed,
    _get_allowed_local_deps,
    _merge_bundle_dirs,
    _merge_files,
    _set_full_local_dep_relpaths,
    _vet_local_deps,
    contains_package,
    get_golang_version,
    match_parent_module,
    path_to_subpackage,
    resolve_gomod,
)
from tests.helper_utils import assert_directories_equal, write_file_tree


def setup_module():
    """Re-enable logging that was disabled at some point in previous tests."""
    gomod.log.disabled = False
    gomod.log.setLevel("DEBUG")


url = "https://github.com/release-engineering/retrodep.git"
ref = "c50b93a32df1c9d700e3e80996845bc2e13be848"
archive_path = f"/tmp/cachito-archives/release-engineering/retrodep/{ref}.tar.gz"

mock_pkg_list = dedent(
    """\
    github.com/release-engineering/retrodep/v2
    github.com/release-engineering/retrodep/v2/retrodep
    github.com/release-engineering/retrodep/v2/retrodep/glide
    """
)
mock_pkg_deps = dedent(
    # Output of go list -deps -json ./...
    """
    {
        "ImportPath": "github.com/op/go-logging",
        "Module": {
            "Path": "github.com/op/go-logging",
            "Version": "v0.0.0-20160315200505-970db520ece7"
        }
    }
    {
        "ImportPath": "github.com/Masterminds/semver",
        "Module": {
            "Path": "github.com/Masterminds/semver",
            "Version": "v1.4.2"
        }
    }
    {
        "ImportPath": "github.com/pkg/errors",
        "Module": {
            "Path": "github.com/pkg/errors",
            "Version": "v0.8.1"
        }
    }
    {
        "ImportPath": "gopkg.in/yaml.v2",
        "Module": {
            "Path": "gopkg.in/yaml.v2",
            "Version": "v2.2.2"
        }
    }
    {
        "ImportPath": "github.com/release-engineering/retrodep/v2/retrodep/glide",
        "Module": {
            "Path": "github.com/release-engineering/retrodep/v2",
            "Main": true
        }
    }
    {
        "ImportPath": "golang.org/x/tools/go/vcs",
        "Module": {
            "Path": "golang.org/x/tools",
            "Version": "v0.0.0-20190325161752-5a8dccf5b48a"
        }
    }
    {
        "ImportPath": "github.com/release-engineering/retrodep/v2/retrodep",
        "Module": {
            "Path": "github.com/release-engineering/retrodep/v2",
            "Main": true
        }
    }
    {
        "ImportPath": "github.com/release-engineering/retrodep/v2",
        "Module": {
            "Path": "github.com/release-engineering/retrodep/v2",
            "Main": true
        },
        "Deps": [
            "fmt",
            "github.com/op/go-logging",
            "github.com/Masterminds/semver",
            "github.com/pkg/errors",
            "gopkg.in/yaml.v2",
            "github.com/release-engineering/retrodep/v2/retrodep/glide",
            "golang.org/x/tools/go/vcs",
            "github.com/release-engineering/retrodep/v2/retrodep"
        ]
    }
    {
        "ImportPath": "github.com/markbates/inflect",
        "Module": {
            "Path": "github.com/markbates/inflect",
            "Version": "v1.0.0",
            "Replace": {
                "Path": "github.com/markbates/inflect",
                "Version": "v1.0.1"
            }
        }
    }
    {
        "ImportPath": "fmt",
        "Standard": true,
        "Deps": [
          "errors",
          "internal/bytealg",
          "internal/cpu",
          "internal/fmtsort",
          "internal/oserror",
          "internal/poll",
          "internal/race",
          "internal/reflectlite",
          "internal/syscall/execenv",
          "internal/syscall/unix",
          "internal/testlog",
          "internal/unsafeheader",
          "io",
          "io/fs",
          "math",
          "math/bits",
          "os",
          "path",
          "reflect",
          "runtime",
          "runtime/internal/atomic",
          "runtime/internal/math",
          "runtime/internal/sys",
          "sort",
          "strconv",
          "sync",
          "sync/atomic",
          "syscall",
          "time",
          "unicode",
          "unicode/utf8",
          "unsafe"
        ]
    }
    """
)

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


def _generate_mock_cmd_output(error_pkg="github.com/pkg/errors v1.0.0"):
    """
    Generate mocked output of the following command.

    go list -m -f '{{ if not .Main }}{{ .String }}{{ end }}' all
    """
    return dedent(
        f"""\
        github.com/Masterminds/semver v1.4.2
        github.com/kr/pretty v0.1.0
        github.com/kr/pty v1.1.1
        github.com/kr/text v0.1.0
        github.com/op/go-logging v0.0.0-20160315200505-970db520ece7
        {error_pkg}
        golang.org/x/crypto v0.0.0-20190308221718-c2843e01d9a2
        golang.org/x/net v0.0.0-20190311183353-d8887717615a
        golang.org/x/sys v0.0.0-20190215142949-d0b11bdaac8a
        golang.org/x/text v0.3.0
        golang.org/x/tools v0.0.0-20190325161752-5a8dccf5b48a
        gopkg.in/check.v1 v1.0.0-20180628173108-788fd7840127
        gopkg.in/yaml.v2 v2.2.2
        k8s.io/metrics v0.0.0 => ./staging/src/k8s.io/metrics
    """
    )


@pytest.mark.parametrize(
    "dep_replacement, go_list_error_pkg, expected_replace",
    (
        (None, "github.com/pkg/errors v1.0.0", None),
        (
            {"name": "github.com/pkg/errors", "type": "gomod", "version": "v1.0.0"},
            "github.com/pkg/errors v0.9.0 => github.com/pkg/errors v1.0.0",
            "github.com/pkg/errors=github.com/pkg/errors@v1.0.0",
        ),
        (
            {
                "name": "github.com/pkg/errors",
                "new_name": "github.com/pkg/new_errors",
                "type": "gomod",
                "version": "v1.0.0",
            },
            "github.com/pkg/errors v0.9.0 => github.com/pkg/new_errors v1.0.0",
            "github.com/pkg/errors=github.com/pkg/new_errors@v1.0.0",
        ),
    ),
)
@pytest.mark.parametrize("cgo_disable", [False, True])
@mock.patch("cachito.workers.pkg_managers.gomod.get_golang_version")
@mock.patch("cachito.workers.pkg_managers.gomod.GoCacheTemporaryDirectory")
@mock.patch("cachito.workers.pkg_managers.gomod._merge_bundle_dirs")
@mock.patch("cachito.workers.pkg_managers.gomod._get_allowed_local_deps")
@mock.patch("cachito.workers.pkg_managers.gomod._vet_local_deps")
@mock.patch("cachito.workers.pkg_managers.gomod._set_full_local_dep_relpaths")
@mock.patch("cachito.workers.pkg_managers.gomod.get_worker_config")
@mock.patch("subprocess.run")
def test_resolve_gomod(
    mock_run,
    mock_get_worker_config,
    mock_set_full_relpaths,
    mock_vet_local_deps,
    mock_get_allowed_local_deps,
    mock_merge_tree,
    mock_temp_dir,
    mock_golang_version,
    dep_replacement,
    go_list_error_pkg,
    expected_replace,
    cgo_disable,
    tmpdir,
    sample_deps,
    sample_deps_replace,
    sample_deps_replace_new_name,
    sample_package,
    sample_pkg_deps_without_replace,
):
    mock_cmd_output = _generate_mock_cmd_output(go_list_error_pkg)
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmpdir)

    # Mock the "subprocess.run" calls
    run_side_effects = []
    if dep_replacement:
        run_side_effects.append(mock.Mock(returncode=0, stdout=None))  # go mod edit -replace
    run_side_effects.append(mock.Mock(returncode=0, stdout=None))  # go mod download
    if dep_replacement:
        run_side_effects.append(mock.Mock(returncode=0, stdout=None))  # go mod tidy
    run_side_effects.append(
        mock.Mock(returncode=0, stdout="github.com/release-engineering/retrodep/v2")  # go list -m
    )
    run_side_effects.append(mock.Mock(returncode=0, stdout=mock_cmd_output))  # go list -m all
    run_side_effects.append(mock.Mock(returncode=0, stdout=mock_pkg_list))  # go list -find ./...
    run_side_effects.append(mock.Mock(returncode=0, stdout=mock_pkg_deps))  # go list -deps -json
    mock_run.side_effect = run_side_effects

    mock_golang_version.return_value = "v2.1.1"

    mock_get_allowed_local_deps.return_value = ["*"]

    archive_path = "/this/is/path/to/archive.tar.gz"
    request = {"id": 3, "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848"}
    if cgo_disable:
        request["flags"] = ["cgo-disable"]

    if dep_replacement is None:
        gomod = resolve_gomod(archive_path, request)
        expected_deps = sample_deps
    else:
        gomod = resolve_gomod(archive_path, request, [dep_replacement])
        if dep_replacement.get("new_name"):
            expected_deps = sample_deps_replace_new_name
        else:
            expected_deps = sample_deps_replace

    if expected_replace:
        assert mock_run.call_args_list[0][0][0] == (
            "go",
            "mod",
            "edit",
            "-replace",
            expected_replace,
        )
        if dep_replacement:
            assert mock_run.call_args_list[2][0][0] == ("go", "mod", "tidy")

    # when not vendoring, go list should be called with -mod readonly
    assert mock_run.call_args_list[-2][0][0] == ["go", "list", "-mod", "readonly", "-find", "./..."]

    for call in mock_run.call_args_list:
        env = call.kwargs["env"]
        if cgo_disable:
            assert env["CGO_ENABLED"] == "0"
        else:
            assert "CGO_ENABLED" not in env

    assert gomod["module"] == sample_package
    assert gomod["module_deps"] == expected_deps
    assert len(gomod["packages"]) == 1
    if dep_replacement is None:
        assert (
            sorted(gomod["packages"][0]["pkg_deps"], key=_package_sort_key)
            == sample_pkg_deps_without_replace
        )

    mock_merge_tree.assert_called_once_with(
        os.path.join(tmpdir, RequestBundleDir.go_mod_cache_download_part),
        str(RequestBundleDir(request["id"]).gomod_download_dir),
    )
    expect_module_name = sample_package["name"]
    mock_get_allowed_local_deps.assert_called_once_with(expect_module_name)
    mock_vet_local_deps.assert_has_calls(
        [
            mock.call(expected_deps, expect_module_name, ["*"]),
            mock.call(gomod["packages"][0]["pkg_deps"], expect_module_name, ["*"]),
        ],
    )
    mock_set_full_relpaths.assert_called_once_with(gomod["packages"][0]["pkg_deps"], expected_deps)


@mock.patch("cachito.workers.pkg_managers.gomod.get_golang_version")
@mock.patch("cachito.workers.pkg_managers.gomod.GoCacheTemporaryDirectory")
@mock.patch("subprocess.run")
@mock.patch("cachito.workers.pkg_managers.gomod.RequestBundleDir")
@mock.patch("cachito.workers.pkg_managers.gomod._module_lines_from_modules_txt")
def test_resolve_gomod_vendor_dependencies(
    mock_module_lines,
    mock_bundle_dir,
    mock_run,
    mock_temp_dir,
    mock_golang_version,
    tmpdir,
    sample_package,
):
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmpdir)

    # Mock the "subprocess.run" calls
    mock_run.side_effect = [
        # go mod vendor
        mock.Mock(returncode=0, stdout=None),
        # go list -m
        mock.Mock(returncode=0, stdout="github.com/release-engineering/retrodep/v2"),
        # go list -find ./...
        mock.Mock(returncode=0, stdout="github.com/release-engineering/retrodep/v2"),
        # go list -deps -json
        mock.Mock(returncode=0, stdout=mock_pkg_deps_no_deps),
    ]
    mock_module_lines.return_value = []
    mock_golang_version.return_value = "v2.1.1"

    archive_path = "/this/is/path/to/archive.tar.gz"
    request = {
        "id": 3,
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "flags": ["gomod-vendor"],
    }
    gomod = resolve_gomod(archive_path, request)

    assert mock_run.call_args_list[0][0][0] == ("go", "mod", "vendor")
    # when vendoring, go list should be called without -mod readonly
    assert mock_run.call_args_list[-2][0][0] == ["go", "list", "-find", "./..."]
    assert gomod["module"] == sample_package
    assert not gomod["module_deps"]

    # Ensure an empty directory is created at bundle_dir.gomod_download_dir
    mock_bundle_dir.return_value.gomod_download_dir.mkdir.assert_called_once_with(
        exist_ok=True, parents=True
    )
    mock_module_lines.assert_called_once_with(archive_path)


@mock.patch("cachito.workers.pkg_managers.gomod.GoCacheTemporaryDirectory")
@mock.patch("subprocess.run")
@mock.patch("cachito.workers.pkg_managers.gomod.get_golang_version")
@mock.patch("cachito.workers.pkg_managers.gomod.get_worker_config")
@mock.patch("pathlib.Path.is_dir")
@pytest.mark.parametrize("strict_vendor", [True, False])
def test_resolve_gomod_strict_mode_raise_error(
    mock_isdir, mock_gwc, mock_golang_version, mock_run, mock_temp_dir, tmpdir, strict_vendor,
):
    mock_isdir.return_value = True
    # Mock the get_worker_config
    mock_config = mock.Mock()
    if strict_vendor != "default":
        mock_config.cachito_gomod_strict_vendor = strict_vendor
    mock_config.cachito_athens_url = "http://athens:3000"
    mock_gwc.return_value = mock_config
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmpdir)
    mock_golang_version.return_value = "v2.1.1"

    # Mock the "subprocess.run" call
    mock_run.side_effect = [
        mock.Mock(returncode=0, stdout=""),  # go mod edit -replace
        mock.Mock(returncode=0, stdout=""),  # go mod download
        mock.Mock(returncode=0, stdout=""),  # go mod tidy
        mock.Mock(returncode=0, stdout="pizza"),  # go list -m
        mock.Mock(returncode=0, stdout="pizza v1.0.0 => pizza v1.0.1\n"),  # go list -mod readonly
        mock.Mock(returncode=0, stdout=""),  # go list -find
        mock.Mock(returncode=0, stdout=""),  # go list -deps -json
    ]

    archive_path = "/this/is/path/to/archive.tar.gz"
    request = {"id": 3, "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848"}
    expected_error = (
        'The "gomod-vendor" or "gomod-vendor-check" flag must be set when your repository has '
        "vendored dependencies."
    )
    with strict_vendor and pytest.raises(CachitoError, match=expected_error) or nullcontext():
        resolve_gomod(
            archive_path, request, [{"name": "pizza", "type": "gomod", "version": "v1.0.0"}]
        )


@mock.patch("cachito.workers.pkg_managers.gomod.get_golang_version")
@mock.patch("cachito.workers.pkg_managers.gomod.GoCacheTemporaryDirectory")
@mock.patch("cachito.workers.pkg_managers.gomod._merge_bundle_dirs")
@mock.patch("subprocess.run")
@mock.patch("os.makedirs")
@mock.patch("os.path.exists")
def test_resolve_gomod_no_deps(
    mock_exists,
    mock_makedirs,
    mock_run,
    mock_merge_tree,
    mock_temp_dir,
    mock_golang_version,
    tmpdir,
    sample_package,
    sample_pkg_lvl_pkg,
):
    # Ensure to create the gomod download cache directory
    mock_exists.return_value = False

    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmpdir)

    # Mock the "subprocess.run" calls
    mock_run.side_effect = [
        # go mod download
        mock.Mock(returncode=0, stdout=None),
        # go list -m
        mock.Mock(returncode=0, stdout="github.com/release-engineering/retrodep/v2"),
        # go list -m all
        mock.Mock(returncode=0, stdout=""),
        # go list -find ./...
        mock.Mock(returncode=0, stdout="github.com/release-engineering/retrodep/v2"),
        # go list -deps -json
        mock.Mock(returncode=0, stdout=mock_pkg_deps_no_deps),
    ]
    mock_golang_version.return_value = "v2.1.1"

    archive_path = "/this/is/path/to/archive.tar.gz"
    request = {"id": 3, "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848"}
    gomod = resolve_gomod(archive_path, request)

    assert gomod["module"] == sample_package
    assert not gomod["module_deps"]
    assert len(gomod["packages"]) == 1
    assert gomod["packages"][0]["pkg"] == sample_pkg_lvl_pkg
    assert not gomod["packages"][0]["pkg_deps"]

    # The second one ensures the source cache directory exists
    mock_makedirs.assert_called_once_with(
        os.path.join(tmpdir, RequestBundleDir.go_mod_cache_download_part), exist_ok=True
    )

    bundle_dir = RequestBundleDir(request["id"])
    mock_merge_tree.assert_called_once_with(
        os.path.join(tmpdir, RequestBundleDir.go_mod_cache_download_part),
        str(bundle_dir.gomod_download_dir),
    )


@mock.patch("cachito.workers.pkg_managers.gomod.GoCacheTemporaryDirectory")
@mock.patch("subprocess.run")
def test_resolve_gomod_unused_dep(mock_run, mock_temp_dir, tmpdir):
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmpdir)
    request = {"id": 3, "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848"}

    # Mock the "subprocess.run" calls
    mock_run.side_effect = [
        mock.Mock(returncode=0, stdout=None),  # go mod edit -replace
        mock.Mock(returncode=0, stdout=None),  # go mod download
        mock.Mock(returncode=0, stdout=None),  # go mod tidy
        mock.Mock(returncode=0, stdout="github.com/release-engineering/retrodep/v2"),  # go list -m
        mock.Mock(returncode=0, stdout=_generate_mock_cmd_output()),  # go list -m all
    ]

    expected_error = "The following gomod dependency replacements don't apply: pizza"
    with pytest.raises(CachitoError, match=expected_error):
        resolve_gomod(
            "/path/archive.tar.gz",
            request,
            [{"name": "pizza", "type": "gomod", "version": "v1.0.0"}],
        )


@pytest.mark.parametrize(("go_mod_rc", "go_list_rc"), ((0, 1), (1, 0)))
@mock.patch("cachito.workers.pkg_managers.gomod.GoCacheTemporaryDirectory")
@mock.patch("cachito.workers.pkg_managers.gomod.get_worker_config")
@mock.patch("subprocess.run")
def test_go_list_cmd_failure(
    mock_run, mock_worker_config, mock_temp_dir, tmpdir, go_mod_rc, go_list_rc
):
    archive_path = "/this/is/path/to/archive.tar.gz"
    request = {"id": 3, "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848"}

    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmpdir)
    mock_worker_config.return_value.cachito_gomod_download_max_tries = 1

    # Mock the "subprocess.run" calls
    mock_run.side_effect = [
        mock.Mock(returncode=go_mod_rc, stdout=None),  # go mod download
        mock.Mock(returncode=go_list_rc, stdout=_generate_mock_cmd_output()),  # go list -m all
    ]

    with pytest.raises(CachitoError, match="Processing gomod dependencies failed"):
        resolve_gomod(archive_path, request)


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
        archive.extractall(tmpdir)
    repo_path = os.path.join(tmpdir, "golang_git_repo")

    module_name = f"github.com/mprahl/test-golang-pseudo-versions{module_suffix}"
    version = get_golang_version(module_name, repo_path, ref, subpath=subpath)
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
        _merge_bundle_dirs(dir_1, dir_2)
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
        _merge_files("{}/list".format(dir_1), "{}/list".format(dir_2))
        with open("{}/list".format(dir_2), "r") as f:
            print(f.read())
        with open("{}/list".format(dir_3), "r") as f:
            print(f.read())
        assert_directories_equal(dir_2, dir_3)


@mock.patch("cachito.workers.pkg_managers.gomod._fail_unless_allowed")
def test_vet_local_deps(mock_fail_allowlist):
    dependencies = [
        {"name": "foo", "version": "./local/foo"},
        {"name": "bar", "version": "v1.0.0"},
        {"name": "baz", "version": "./local/baz"},
    ]
    module_name = "some-module"

    _vet_local_deps(dependencies, module_name, ["foo", "baz"])

    mock_fail_allowlist.assert_has_calls(
        [
            mock.call("some-module", "foo", ["foo", "baz"]),
            mock.call("some-module", "baz", ["foo", "baz"]),
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

    expect_error = re.escape(
        f"Absolute paths to gomod dependencies are not supported: {platform_specific_path}"
    )
    with pytest.raises(CachitoError, match=expect_error):
        _vet_local_deps(dependencies, "some-module", [])


@pytest.mark.parametrize("path", ["../local/path", "./local/../path"])
def test_vet_local_deps_parent_dir(path):
    dependencies = [{"name": "foo", "version": path}]

    expect_error = re.escape(
        f"Path to gomod dependency contains '..': {path}. Cachito does not support this case."
    )
    with pytest.raises(CachitoError, match=expect_error):
        _vet_local_deps(dependencies, "some-module", [])


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
        with pytest.raises(CachitoError, match=re.escape(expect_error)):
            _fail_unless_allowed(module_name, package_name, allowed_patterns)
    else:
        _fail_unless_allowed(module_name, package_name, allowed_patterns)


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
    _set_full_local_dep_relpaths(pkg_deps_pre, main_module_deps)
    # pkg_deps_pre should be modified in place
    assert pkg_deps_pre == pkg_deps_post


def test_set_full_local_dep_relpaths_no_match():
    pkg_deps = [{"name": "example.org/foo", "version": "./src/foo"}]
    err_msg = "Could not find parent Go module for local dependency: example.org/foo"

    with pytest.raises(RuntimeError, match=err_msg):
        _set_full_local_dep_relpaths(pkg_deps, [])


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
    assert _get_allowed_local_deps(module_name) == expect_allowed


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
    assert contains_package(parent_name, package_name) == expect_result


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
    assert path_to_subpackage(parent, subpackage) == expect_path


def test_path_to_subpackage_not_a_subpackage():
    with pytest.raises(ValueError, match="Package github.com/b does not belong to github.com/a"):
        path_to_subpackage("github.com/a", "github.com/b")


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
    assert match_parent_module(package_name, module_names) == expect_parent_module


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
@mock.patch("cachito.workers.pkg_managers.gomod.run_download_cmd")
@mock.patch("cachito.workers.pkg_managers.gomod._vendor_changed")
def test_vendor_deps(mock_vendor_changed, mock_run_cmd, can_make_changes, vendor_changed):
    git_dir = "/fake/repo"
    app_dir = "/fake/repo/some/app"
    run_params = {"cwd": app_dir}
    mock_vendor_changed.return_value = vendor_changed
    expect_error = vendor_changed and not can_make_changes

    if expect_error:
        msg = "The content of the vendor directory is not consistent with go.mod."
        with pytest.raises(ValidationError, match=msg):
            gomod._vendor_deps(run_params, can_make_changes, git_dir)
    else:
        gomod._vendor_deps(run_params, can_make_changes, git_dir)

    mock_run_cmd.assert_called_once_with(("go", "mod", "vendor"), run_params)
    if not can_make_changes:
        mock_vendor_changed.assert_called_once_with(git_dir, app_dir)


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
            textwrap.dedent(
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
            textwrap.dedent(
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
            textwrap.dedent(
                """
                A\t{subpath}vendor/some_file
                """
            ),
        ),
        # multiple additions and modifications
        (
            {"vendor": {"some_file": "foo"}},
            {"vendor": {"some_file": "bar", "other_file": "baz"}},
            textwrap.dedent(
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
            textwrap.dedent(
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


def test_module_lines_from_modules_txt(tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    vendor.joinpath("modules.txt").write_text(
        dedent(
            """\
            # github.com/org/some-module v0.0.0 => ./example/src/some-module
            ## explicit
            github.com/org/some-module
            # golang.org/x/text v0.0.0-20170915032832-14c0d48ead0c
            golang.org/x/text/internal/tag
            golang.org/x/text/language
            # rsc.io/quote v1.5.2 => rsc.io/quote v1.5.1
            ## explicit
            rsc.io/quote
            # rsc.io/sampler v1.3.0
            rsc.io/sampler
            # github.com/org/some-module => ./example/src/some-module
            # rsc.io/quote => rsc.io/quote v1.5.1
            """
        )
    )
    assert gomod._module_lines_from_modules_txt(str(tmp_path)) == [
        "github.com/org/some-module v0.0.0 => ./example/src/some-module",
        "golang.org/x/text v0.0.0-20170915032832-14c0d48ead0c",
        "rsc.io/quote v1.5.2 => rsc.io/quote v1.5.1",
        "rsc.io/sampler v1.3.0",
    ]


@pytest.mark.parametrize(
    "file_content, expect_error_msg",
    [
        ("#invalid-line", "vendor/modules.txt: unexpected format: '#invalid-line'"),
        (
            "github.com/x/package",
            "vendor/modules.txt: package has no parent module: github.com/x/package",
        ),
    ],
)
def test_module_lines_from_modules_txt_invalid_format(file_content, expect_error_msg, tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    vendor.joinpath("modules.txt").write_text(file_content)

    with pytest.raises(CachitoError, match=expect_error_msg):
        gomod._module_lines_from_modules_txt(str(tmp_path))


def test_load_list_deps():
    list_deps_output = dedent(
        """
        {
            "ImportPath": "unsafe",
            "Name": "unsafe",
            "Standard": true
        }
        {
            "ImportPath": "github.com/some-org/some-module",
            "Name": "some-module",
            "Module": {
                "Path": "github.com/some-org/some-module",
                "Version": "v1.0.0"
            }
        }
        {
            "ImportPath": "github.com/some-org/other-module",
            "Name": "other-module",
            "Module": {
                "Path": "github.com/some-org/other-module",
                "Version": "v1.0.0"
            },
            "Deps": [
                "unsafe",
                "github.com/some-org/some-module",
                "github.com/some-org/other-module/generated/foo"
            ]
        }
        {
            "ImportPath": "github.com/some-org/other-module/generated/foo",
            "Incomplete": true,
            "Error": {
                "Err": "cannot find module providing package"
            }
        }
        """
    )
    assert gomod._load_list_deps(list_deps_output) == {
        "github.com/some-org/some-module": {
            "Module": {"Path": "github.com/some-org/some-module", "Version": "v1.0.0"},
        },
        "github.com/some-org/other-module": {
            "Module": {"Path": "github.com/some-org/other-module", "Version": "v1.0.0"},
            "Deps": [
                "unsafe",
                "github.com/some-org/some-module",
                "github.com/some-org/other-module/generated/foo",
            ],
        },
        "github.com/some-org/other-module/generated/foo": {},
        "unsafe": {"Standard": True},
    }


@pytest.mark.parametrize(
    "dep_info, expect_version",
    [
        ({}, None),
        ({"Module": {"Path": "github.com/foo/bar", "Version": "v1.0.0"}}, "v1.0.0"),
        ({"Module": {"Path": "github.com/foo/bar", "Main": True}}, None),
        (
            {
                "Module": {
                    "Path": "github.com/foo/bar",
                    "Version": "v1.0.0",
                    "Replace": {"Path": "github.com/xyz/bar", "Version": "v2.0.0"},
                }
            },
            "v2.0.0",
        ),
        (
            {
                "Module": {
                    "Path": "github.com/foo/bar",
                    "Version": "v1.0.0",
                    "Replace": {"Path": "./local/src/bar"},
                }
            },
            "./local/src/bar",
        ),
    ],
)
def test_get_dep_version(dep_info, expect_version):
    assert gomod._get_dep_version(dep_info) == expect_version


@pytest.mark.parametrize("tries_needed", [1, 2, 3, 4, 5])
@mock.patch("cachito.workers.pkg_managers.gomod.get_worker_config")
@mock.patch("subprocess.run")
@mock.patch("time.sleep")
def test_run_download_cmd_success(mock_sleep, mock_run, mock_worker_config, tries_needed, caplog):
    mock_worker_config.return_value.cachito_gomod_download_max_tries = 5

    failure = mock.Mock(returncode=1, stdout="")
    success = mock.Mock(returncode=0, stdout="")
    mock_run.side_effect = [failure for _ in range(tries_needed - 1)] + [success]

    gomod.run_download_cmd(["go", "mod", "download"], {})
    assert mock_run.call_count == tries_needed
    assert mock_sleep.call_count == tries_needed - 1

    for n in range(tries_needed - 1):
        wait = 2 ** n
        assert f"Backing off run_go(...) for {wait:.1f}s" in caplog.text


@mock.patch("cachito.workers.pkg_managers.gomod.get_worker_config")
@mock.patch("subprocess.run")
@mock.patch("time.sleep")
def test_run_download_cmd_failure(mock_sleep, mock_run, mock_worker_config, caplog):
    mock_worker_config.return_value.cachito_gomod_download_max_tries = 5

    failure = mock.Mock(returncode=1, stdout="")
    mock_run.side_effect = [failure] * 5

    expect_msg = (
        "Processing gomod dependencies failed. Cachito tried the go mod download command 5 times. "
        "This may indicate a problem with your repository or Cachito itself."
    )

    with pytest.raises(CachitoError, match=expect_msg):
        gomod.run_download_cmd(["go", "mod", "download"], {})

    assert mock_run.call_count == 5
    assert mock_sleep.call_count == 4

    assert "Backing off run_go(...) for 1.0s" in caplog.text
    assert "Backing off run_go(...) for 2.0s" in caplog.text
    assert "Backing off run_go(...) for 4.0s" in caplog.text
    assert "Backing off run_go(...) for 8.0s" in caplog.text
    assert "Giving up run_go(...) after 5 tries" in caplog.text
