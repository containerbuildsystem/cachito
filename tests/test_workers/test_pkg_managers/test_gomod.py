# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tarfile
from textwrap import dedent
from unittest import mock

import pytest

from cachito.workers.pkg_managers.gomod import get_golang_version, resolve_gomod
from cachito.errors import CachitoError
from cachito.workers.paths import RequestBundleDir

url = "https://github.com/release-engineering/retrodep.git"
ref = "c50b93a32df1c9d700e3e80996845bc2e13be848"
archive_path = f"/tmp/cachito-archives/release-engineering/retrodep/{ref}.tar.gz"


def _generate_mock_cmd_output(error_pkg="github.com/pkg/errors v1.0.0"):
    return dedent(
        f"""\
        github.com/release-engineering/retrodep/v2
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
        k8s.io/metrics v0.0.0 ./staging/src/k8s.io/metrics
    """
    )


@pytest.mark.parametrize(
    "dep_replacement, go_list_error_pkg, expected_replace",
    (
        (None, "github.com/pkg/errors v1.0.0", None),
        (
            {"name": "github.com/pkg/errors", "type": "gomod", "version": "v1.0.0"},
            "github.com/pkg/errors v0.9.0 github.com/pkg/errors v1.0.0",
            "github.com/pkg/errors=github.com/pkg/errors@v1.0.0",
        ),
        (
            {
                "name": "github.com/pkg/errors",
                "new_name": "github.com/pkg/new_errors",
                "type": "gomod",
                "version": "v1.0.0",
            },
            "github.com/pkg/errors v0.9.0 github.com/pkg/new_errors v1.0.0",
            "github.com/pkg/errors=github.com/pkg/new_errors@v1.0.0",
        ),
    ),
)
@mock.patch("cachito.workers.pkg_managers.gomod.get_golang_version")
@mock.patch("cachito.workers.pkg_managers.gomod.GoCacheTemporaryDirectory")
@mock.patch("subprocess.run")
@mock.patch("shutil.copytree")
def test_resolve_gomod(
    mock_copytree,
    mock_run,
    mock_temp_dir,
    mock_golang_version,
    dep_replacement,
    go_list_error_pkg,
    expected_replace,
    tmpdir,
    sample_deps,
    sample_deps_replace,
    sample_deps_replace_new_name,
    sample_package,
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
    run_side_effects.append(mock.Mock(returncode=0, stdout=mock_cmd_output))  # go list -m all
    mock_run.side_effect = run_side_effects

    mock_golang_version.return_value = "v2.1.1"

    archive_path = "/this/is/path/to/archive.tar.gz"
    request = {"id": 3, "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848"}
    if dep_replacement is None:
        module, resolved_deps = resolve_gomod(archive_path, request)
        expected_deps = sample_deps
    else:
        module, resolved_deps = resolve_gomod(archive_path, request, [dep_replacement])
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

    assert module == sample_package
    assert resolved_deps == expected_deps

    mock_copytree.assert_called_once_with(
        os.path.join(tmpdir, RequestBundleDir.go_mod_cache_download_part),
        str(RequestBundleDir(request["id"]).gomod_download_dir),
    )


@mock.patch("cachito.workers.pkg_managers.gomod.get_golang_version")
@mock.patch("cachito.workers.pkg_managers.gomod.GoCacheTemporaryDirectory")
@mock.patch("subprocess.run")
@mock.patch("os.makedirs")
@mock.patch("os.path.exists")
@mock.patch("shutil.copytree")
def test_resolve_gomod_no_deps(
    mock_copytree,
    mock_exists,
    mock_makedirs,
    mock_run,
    mock_temp_dir,
    mock_golang_version,
    tmpdir,
    sample_package,
):
    # Ensure to create the gomod download cache directory
    mock_exists.return_value = False

    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmpdir)

    # Mock the "subprocess.run" calls
    mock_run.side_effect = [
        # go mod download
        mock.Mock(returncode=0, stdout=None),
        # go list -m all
        mock.Mock(returncode=0, stdout="github.com/release-engineering/retrodep/v2"),
    ]
    mock_golang_version.return_value = "v2.1.1"

    archive_path = "/this/is/path/to/archive.tar.gz"
    request = {"id": 3, "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848"}
    module, resolved_deps = resolve_gomod(archive_path, request)

    assert module == sample_package
    assert not resolved_deps

    # The second one ensures the source cache directory exists
    mock_makedirs.assert_called_once_with(
        os.path.join(tmpdir, RequestBundleDir.go_mod_cache_download_part), exist_ok=True
    )

    bundle_dir = RequestBundleDir(request["id"])
    mock_copytree.assert_called_once_with(
        os.path.join(tmpdir, RequestBundleDir.go_mod_cache_download_part),
        str(bundle_dir.gomod_download_dir),
    )


@mock.patch("cachito.workers.pkg_managers.gomod.GoCacheTemporaryDirectory")
@mock.patch("subprocess.run")
def test_resolve_gomod_unused_dep(mock_run, mock_temp_dir, tmpdir):
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmpdir)

    # Mock the "subprocess.run" calls
    mock_run.side_effect = [
        mock.Mock(returncode=0, stdout=None),  # go mod edit -replace
        mock.Mock(returncode=0, stdout=None),  # go mod download
        mock.Mock(returncode=0, stdout=None),  # go mod tidy
        mock.Mock(returncode=0, stdout=_generate_mock_cmd_output()),  # go list -m all
    ]

    expected_error = "The following gomod dependency replacements don't apply: pizza"
    with pytest.raises(CachitoError, match=expected_error):
        resolve_gomod(
            "/path/archive.tar.gz", 3, [{"name": "pizza", "type": "gomod", "version": "v1.0.0"}]
        )


@pytest.mark.parametrize(("go_mod_rc", "go_list_rc"), ((0, 1), (1, 0)))
@mock.patch("cachito.workers.pkg_managers.gomod.GoCacheTemporaryDirectory")
@mock.patch("subprocess.run")
def test_go_list_cmd_failure(mock_run, mock_temp_dir, tmpdir, go_mod_rc, go_list_rc):
    archive_path = "/this/is/path/to/archive.tar.gz"
    # Mock the tempfile.TemporaryDirectory context manager
    mock_temp_dir.return_value.__enter__.return_value = str(tmpdir)

    # Mock the "subprocess.run" calls
    mock_run.side_effect = [
        mock.Mock(returncode=go_mod_rc, stdout=None),  # go mod download
        mock.Mock(returncode=go_list_rc, stdout=_generate_mock_cmd_output()),  # go list -m all
    ]

    with pytest.raises(CachitoError) as exc_info:
        resolve_gomod(archive_path, 1)
    assert str(exc_info.value) == "Processing gomod dependencies failed"


@pytest.mark.parametrize(
    "module_suffix, ref, expected",
    (
        # First commit with no tag
        ("", "78510c591e2be635b010a52a7048b562bad855a3", "v0.0.0-20191107200220-78510c591e2b"),
        # No prior tag at all
        ("", "5a6e50a1f0e3ce42959d98b3c3a2619cb2516531", "v0.0.0-20191107202433-5a6e50a1f0e3"),
        # Only a non-semver tag (v1)
        ("", "7911d393ab186f8464884870fcd0213c36ecccaf", "v0.0.0-20191107202444-7911d393ab18"),
        # Directly maps to a semver tag (v1.0.0)
        ("", "d1b74311a7bf590843f3b58bf59ab047a6f771ae", "v1.0.0"),
        # One commit after a semver tag (v1.0.0)
        ("", "e92462c73bbaa21540f7385e90cb08749091b66f", "v1.0.1-0.20191107202936-e92462c73bba"),
        # A semver tag (v2.0.0) without the corresponding go.mod bump, which happens after a v1.0.0
        # semver tag
        ("", "61fe6324077c795fc81b602ee27decdf4a4cf908", "v1.0.1-0.20191107202953-61fe6324077c"),
        # A semver tag (v2.1.0) after the go.mod file was bumped
        ("/v2", "39006a0b5b0654a299cc43f71e0dc1aa50c2bc72", "v2.1.0"),
        # A pre-release semver tag (v2.2.0-alpha)
        ("/v2", "0b3468852566617379215319c0f4dfe7f5948a8f", "v2.2.0-alpha"),
        # Two commits after a pre-release semver tag (v2.2.0-alpha)
        (
            "/v2",
            "863073fae6efd5e04bb972a05db0b0706ec8276e",
            "v2.2.0-alpha.0.20191107204050-863073fae6ef",
        ),
        # Directly maps to a semver non-annotated tag (v2.2.0)
        ("/v2", "709b220511038f443fe1b26ac09c3e6c06c9f7c7", "v2.2.0"),
        # A non-semver tag (random-tag)
        ("/v2", "37cea8ddd9e6b6b81c7cfbc3223ce243c078388a", "v2.2.1-0.20191107204245-37cea8ddd9e6"),
        # The go.mod file is bumped but there is no versioned commit
        ("/v2", "6c7249e8c989852f2a0ee0900378d55d8e1d7fe0", "v2.0.0-20191108212303-6c7249e8c989"),
        # Three semver annotated tags on the same commit
        ("/v2", "a77e08ced4d6ae7d9255a1a2e85bd3a388e61181", "v2.2.5"),
        # A non-annotated semver tag and an annotated semver tag
        ("/v2", "bf2707576336626c8bbe4955dadf1916225a6a60", "v2.3.3"),
        # Two non-annotated semver tags
        ("/v2", "729d0e6d60317bae10a71fcfc81af69a0f6c07be", "v2.4.1"),
        # Two semver tags, with one having the wrong major version and the other with the correct
        # major version
        ("/v2", "3decd63971ed53a5b7ff7b2ca1e75f3915e99cf2", "v2.5.0"),
        # A semver tag that is incorrectly lower then the preceding semver tag
        ("/v2", "0dd249ad59176fee9b5451c2f91cc859e5ddbf45", "v2.0.1"),
        # A commit after the incorrect lower semver tag
        ("/v2", "2883f3ddbbc811b112ff1fe51ba2ee7596ddbf24", "v2.5.1-0.20191118190931-2883f3ddbbc8"),
    ),
)
def test_get_golang_version(tmpdir, module_suffix, ref, expected):
    # Extract the Git repository of a Go module to verify the correct versions are computed
    repo_archive_path = os.path.join(os.path.dirname(__file__), "golang_git_repo.tar.gz")
    with tarfile.open(repo_archive_path, "r:*") as archive:
        archive.extractall(tmpdir)
    repo_path = os.path.join(tmpdir, "golang_git_repo")

    module_name = f"github.com/mprahl/test-golang-pseudo-versions{module_suffix}"
    version = get_golang_version(module_name, repo_path, ref)
    assert version == expected
