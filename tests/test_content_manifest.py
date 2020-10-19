# SPDX-License-Identifier: GPL-3.0-or-later
from collections import OrderedDict
from unittest import mock

import pytest

from cachito.errors import ContentManifestError
from cachito.web.content_manifest import ContentManifest
from cachito.web.models import Package, Request, RequestPackage

GIT_REPO = "https://github.com/namespace/repo"
GIT_REF = "1798a59f297f5f3886e41bc054e538540581f8ce"


@pytest.fixture
def default_request():
    """Get default request to use in tests."""
    return Request(repo=GIT_REPO, ref=GIT_REF)


@pytest.fixture
def default_toplevel_purl():
    """Get VCS purl for default request."""
    return f"pkg:github/namespace/repo@{GIT_REF}"


def test_process_go(default_request):
    pkg = Package.from_json(
        {"name": "example.com/org/project", "type": "go-package", "version": "1.1.1"}
    )
    pkg.id = 1
    expected_purl = "pkg:golang/example.com%2Forg%2Fproject@1.1.1"

    dep = Package.from_json(
        {"name": "example.com/org/project/lib", "type": "go-package", "version": "2.2.2"}
    )
    dep.id = 2
    expected_dep_purl = "pkg:golang/example.com%2Forg%2Fproject%2Flib@2.2.2"

    src = Package.from_json(
        {"name": "example.com/anotherorg/project", "type": "gomod", "version": "3.3.3"}
    )
    src.id = 3
    expected_src_purl = "pkg:golang/example.com%2Fanotherorg%2Fproject@3.3.3"

    cm = ContentManifest(default_request)

    # emulate to_json behavior to setup internal packages cache
    cm._gomod_data.setdefault(pkg.name, [])
    cm._gopkg_data.setdefault(
        pkg.id, {"name": pkg.name, "purl": expected_purl, "dependencies": [], "sources": []}
    )

    cm.process_go_package(pkg, dep)
    cm.process_gomod(pkg, src)
    cm.set_go_package_sources()

    expected_contents = {
        pkg.id: {
            "purl": expected_purl,
            "dependencies": [{"purl": expected_dep_purl}],
            "sources": [{"purl": expected_src_purl}],
        }
    }

    assert cm._gopkg_data
    assert pkg.id in cm._gopkg_data
    assert cm._gopkg_data == expected_contents


def test_process_npm(default_request, default_toplevel_purl):
    pkg = Package.from_json({"name": "grc-ui", "type": "npm", "version": "1.0.0"})
    pkg.id = 1
    expected_purl = default_toplevel_purl

    dep_commit_id = "7762177aacfb1ddf5ca45cebfe8de1da3b24f0ff"
    dep = Package.from_json(
        {
            "name": "security-middleware",
            "type": "npm",
            "version": f"github:open-cluster-management/security-middleware#{dep_commit_id}",
        }
    )
    dep.id = 2
    expected_dep_purl = f"pkg:github/open-cluster-management/security-middleware@{dep_commit_id}"

    src = Package.from_json({"name": "@types/events", "type": "npm", "version": "3.0.0"})
    src.id = 3
    src.dev = True
    expected_src_purl = "pkg:npm/%40types/events@3.0.0"

    cm = ContentManifest(default_request)

    # emulate to_json behavior to setup internal packages cache
    cm._npm_data.setdefault(pkg.id, {"purl": expected_purl, "dependencies": [], "sources": []})

    cm.process_npm_package(pkg, dep)
    cm.process_npm_package(pkg, src)

    expected_contents = {
        pkg.id: {
            "purl": expected_purl,
            "dependencies": [{"purl": expected_dep_purl}],
            "sources": [{"purl": expected_dep_purl}, {"purl": expected_src_purl}],
        }
    }

    assert cm._npm_data
    assert pkg.id in cm._npm_data
    assert cm._npm_data == expected_contents


def test_process_pip(default_request, default_toplevel_purl):
    pkg = Package.from_json({"name": "requests", "type": "pip", "version": "2.24.0"})
    pkg.id = 1
    expected_purl = default_toplevel_purl

    dep_commit_id = "58c88e4952e95935c0dd72d4a24b0c44f2249f5b"
    dep = Package.from_json(
        {
            "name": "cnr-server",
            "type": "pip",
            "version": f"git+https://github.com/quay/appr@{dep_commit_id}",
        }
    )
    dep.id = 2
    expected_dep_purl = f"pkg:github/quay/appr@{dep_commit_id}"

    src = Package.from_json({"name": "setuptools", "type": "pip", "version": "49.1.1"})
    src.id = 3
    src.dev = True
    expected_src_purl = "pkg:pypi/setuptools@49.1.1"

    cm = ContentManifest(default_request)

    # emulate to_json behavior to setup internal packages cache
    cm._pip_data.setdefault(pkg.id, {"purl": expected_purl, "dependencies": [], "sources": []})

    cm.process_pip_package(pkg, dep)
    cm.process_pip_package(pkg, src)

    expected_contents = {
        pkg.id: {
            "purl": expected_purl,
            "dependencies": [{"purl": expected_dep_purl}],
            "sources": [{"purl": expected_dep_purl}, {"purl": expected_src_purl}],
        }
    }

    assert cm._pip_data
    assert pkg.id in cm._pip_data
    assert cm._pip_data == expected_contents


@pytest.mark.parametrize(
    "package",
    [
        None,
        {"name": "example.com/org/project", "type": "go-package", "version": "1.1.1"},
        {"name": "grc-ui", "type": "npm", "version": "1.0.0"},
        {"name": "requests", "type": "pip", "version": "2.24.0"},
    ],
)
@pytest.mark.parametrize("subpath", [None, "some/path"])
@mock.patch("cachito.web.models.Package.to_top_level_purl")
def test_to_json(mock_top_level_purl, app, package, subpath):
    request = Request()
    cm = ContentManifest(request)

    image_contents = []
    if package:
        pkg = Package.from_json(package)
        request_package = RequestPackage(package=pkg, subpath=subpath)
        request.request_packages.append(request_package)
        content = {
            "purl": mock_top_level_purl.return_value,
            "dependencies": [],
            "sources": [],
        }
        image_contents.append(content)

    expected = {
        "metadata": {
            "icm_version": 1,
            "icm_spec": ContentManifest.json_schema_url,
            "image_layer_index": -1,
        },
        "image_contents": image_contents,
    }
    assert cm.to_json() == expected

    if package:
        mock_top_level_purl.assert_called_once_with(request, subpath=subpath)


@pytest.mark.parametrize(
    "packages",
    [
        [
            {"name": "example.com/org/project", "type": "go-package", "version": "1.1.1"},
            {
                "name": "tour",
                "type": "git-submodule",
                "version": (
                    "https://github.com/testrepo/tour.git#58c88e4952e95935c0dd72d4a24b0c44f2249f5b"
                ),
            },
        ]
    ],
)
@mock.patch("cachito.web.content_manifest.ContentManifest.generate_icm")
def test_to_json_with_multiple_packages(mock_generate_icm, app, packages):
    request = Request()
    cm = ContentManifest(request)

    image_contents = []
    for package in packages:
        pkg = Package.from_json(package)
        request_package = RequestPackage(package=pkg)
        request.request_packages.append(request_package)
        content = {"purl": pkg.to_purl(), "dependencies": [], "sources": []}
        image_contents.append(content)
    res = cm.to_json()
    mock_generate_icm.assert_called_once_with(image_contents)
    assert res == mock_generate_icm.return_value


@pytest.mark.parametrize("contents", [None, [], "foobar", 42, OrderedDict({"egg": "bacon"})])
def test_generate_icm(contents, default_request):
    cm = ContentManifest(default_request)
    expected = OrderedDict(
        {
            "image_contents": contents or [],
            "metadata": OrderedDict(
                {
                    "icm_spec": ContentManifest.json_schema_url,
                    "icm_version": 1,
                    "image_layer_index": -1,
                }
            ),
        }
    )
    assert cm.generate_icm(contents) == expected


@pytest.mark.parametrize(
    "pkg_name, gomod_data, warn",
    [
        ["example.com/foo/bar", {}, True],
        ["example.com/foo/bar", {"example.com/foo/bar": []}, False],
        ["example.com/foo/bar", {"example.com/foo/bar": [{"purl": "foo"}]}, False],
        ["example.com/foo/bar", {"example.com/foo": [{"purl": "foo"}]}, False],
        ["example.com/foo", {"example.com/foo/bar": [{"purl": "foo"}]}, True],
    ],
)
@mock.patch("flask.current_app.logger.warning")
def test_set_go_package_sources(mock_warning, app, pkg_name, gomod_data, warn, default_request):
    cm = ContentManifest(default_request)

    main_purl = "pkg:golang/a-package"
    main_package_id = 1

    cm._gopkg_data = {
        main_package_id: {"name": pkg_name, "purl": main_purl, "sources": [], "dependencies": []}
    }
    cm._gomod_data = gomod_data

    cm.set_go_package_sources()

    sources = []
    for v in gomod_data.values():
        if any(k in pkg_name for k in gomod_data.keys()):
            sources += v

    expected = {main_package_id: {"purl": main_purl, "dependencies": [], "sources": sources}}

    assert cm._gopkg_data == expected

    if warn:
        mock_warning.assert_called_once_with("Could not find a Go module for %s", main_purl)
    else:
        mock_warning.assert_not_called()


@pytest.mark.parametrize(
    "package, expected_purl, defined, known_protocol",
    [
        [{"name": "bacon", "type": "invalid", "version": "1.0.0"}, None, False, False],
        [
            {"name": "example.com/org/project", "type": "go-package", "version": "1.1.1"},
            "pkg:golang/example.com%2Forg%2Fproject@1.1.1",
            True,
            True,
        ],
        [
            {"name": "example.com/org/project", "type": "gomod", "version": "1.1.1"},
            "pkg:golang/example.com%2Forg%2Fproject@1.1.1",
            True,
            True,
        ],
        [{"name": "grc-ui", "type": "npm", "version": "1.0.0"}, "pkg:npm/grc-ui@1.0.0", True, True],
        [
            {
                "name": "security-middleware",
                "type": "npm",
                "version": "github:open-cluster-management/security-middleware#i0am0a0commit0hash",
            },
            "pkg:github/open-cluster-management/security-middleware@i0am0a0commit0hash",
            True,
            True,
        ],
        [
            {
                "name": "security-middleware",
                "type": "npm",
                "version": "gitlab:deep/nested/repo/security-middleware#i0am0a0commit0hash",
            },
            "pkg:gitlab/deep/nested/repo/security-middleware@i0am0a0commit0hash",
            True,
            True,
        ],
        [
            {
                "name": "fromgit",
                "type": "npm",
                "version": "git://some.domain/my/project/repo.git#i0am0a0commit0hash",
            },
            (
                "pkg:generic/fromgit?vcs_url=git%3A%2F%2Fsome.domain%2Fmy%2Fproject%2Frepo.git"
                "%23i0am0a0commit0hash"
            ),
            True,
            True,
        ],
        [
            {
                "name": "fromweb",
                "type": "npm",
                "version": "https://some.domain/my/project/package.tar.gz",
            },
            (
                "pkg:generic/fromweb?download_url=https%3A%2F%2Fsome.domain%2Fmy%2Fproject"
                "%2Fpackage.tar.gz"
            ),
            True,
            True,
        ],
        [
            {"name": "fromfile", "type": "npm", "version": "file:client-default"},
            "generic/fromfile?file%3Aclient-default",
            True,
            True,
        ],
        [
            {
                "name": "fromunknown",
                "type": "npm",
                "version": "unknown://some.domain/my/project/package.tar.gz",
            },
            None,
            True,
            False,
        ],
        [
            {"name": "requests", "type": "pip", "version": "2.24.0"},
            "pkg:pypi/requests@2.24.0",
            True,
            True,
        ],
        [
            {"name": "requests_FOO bar", "type": "pip", "version": "2.24.0"},
            "pkg:pypi/requests-foo-bar@2.24.0",
            True,
            True,
        ],
        [
            {
                "name": "cnr-server",
                "type": "pip",
                "version": "git+https://github.com/quay/appr@abcdef",
            },
            "pkg:github/quay/appr@abcdef",
            True,
            True,
        ],
        [
            {
                "name": "operator-manifest",
                "type": "pip",
                "version": (
                    "https://github.com/containerbuildsystem/operator-manifest/archive/"
                    "1234.tar.gz#egg=operator-manifest&cachito_hash=sha256:abcd"
                ),
            },
            (
                "pkg:generic/operator-manifest"
                "?download_url=https%3A%2F%2Fgithub.com%2Fcontainerbuildsystem%2Foperator-manifest"
                "%2Farchive%2F1234.tar.gz%23egg%3Doperator-manifest%26cachito_hash%3Dsha256%3Aabcd"
                "&checksum=sha256:abcd"
            ),
            True,
            True,
        ],
        [
            {
                "name": "tour",
                "type": "git-submodule",
                "version": (
                    "https://github.com/testrepo/tour.git#58c88e4952e95935c0dd72d4a24b0c44f2249f5b"
                ),
            },
            "pkg:github/testrepo/tour@58c88e4952e95935c0dd72d4a24b0c44f2249f5b",
            True,
            True,
        ],
    ],
)
def test_purl_conversion(package, expected_purl, defined, known_protocol):
    pkg = Package.from_json(package)
    if defined and known_protocol:
        purl = pkg.to_purl()
        assert purl == expected_purl
    else:
        msg = f"The PURL spec is not defined for {pkg.type} packages"
        if defined:
            msg = f"Unknown protocol in npm package version: {pkg.version}"
        with pytest.raises(ContentManifestError, match=msg):
            pkg.to_purl()


def test_purl_conversion_bogus_forge():
    package = {"name": "odd", "type": "npm", "version": f"github:something/odd"}
    pkg = Package.from_json(package)

    msg = f"Could not convert version {pkg.version} to purl"
    with pytest.raises(ContentManifestError, match=msg):
        pkg.to_purl()


@pytest.mark.parametrize(
    "repo_url, expected_purl",
    [
        ("http://github.com/org/repo-name", f"pkg:github/org/repo-name@{GIT_REF}"),
        ("http://github.com/org/repo-name/", f"pkg:github/org/repo-name@{GIT_REF}"),
        ("http://github.com:443/org/repo-name", f"pkg:github/org/repo-name@{GIT_REF}"),
        ("http://user:pass@github.com/org/repo-name", f"pkg:github/org/repo-name@{GIT_REF}"),
        ("http://github.com/org/repo-name.git", f"pkg:github/org/repo-name@{GIT_REF}"),
        ("http://bitbucket.org/org/repo-name", f"pkg:bitbucket/org/repo-name@{GIT_REF}"),
        (
            # pkg:gitlab is not defined in the purl spec yet
            "http://gitlab.com/org/repo-name",
            f"pkg:generic/foo?vcs_url=http%3A%2F%2Fgitlab.com%2Forg%2Frepo-name%40{GIT_REF}",
        ),
        (
            "http://gitlab.com/org/repo-name/",
            f"pkg:generic/foo?vcs_url=http%3A%2F%2Fgitlab.com%2Forg%2Frepo-name%40{GIT_REF}",
        ),
        (
            "http://gitlab.com/org/repo-name.git",
            f"pkg:generic/foo?vcs_url=http%3A%2F%2Fgitlab.com%2Forg%2Frepo-name.git%40{GIT_REF}",
        ),
    ],
)
def test_vcs_purl_conversion(repo_url, expected_purl):
    pkg = Package(name="foo")
    assert pkg.to_vcs_purl(repo_url, GIT_REF) == expected_purl


@pytest.mark.parametrize(
    "pkg_type, purl_method, method_args",
    [
        ("gomod", "to_purl", []),
        ("go-package", "to_purl", []),
        ("npm", "to_vcs_purl", [GIT_REPO, GIT_REF]),
        ("pip", "to_vcs_purl", [GIT_REPO, GIT_REF]),
        ("git-submodule", "to_purl", []),
        ("bogus", None, None),
    ],
)
@pytest.mark.parametrize("has_subpath", [False, True])
def test_top_level_purl_conversion(
    pkg_type, purl_method, method_args, default_request, has_subpath
):
    pkg = Package(type=pkg_type)

    if purl_method is None:
        msg = f"{pkg_type!r} is not a valid top level package"
        with pytest.raises(ContentManifestError, match=msg):
            pkg.to_top_level_purl(default_request)
    else:
        with mock.patch.object(pkg, purl_method) as mock_purl_method:
            mock_purl_method.return_value = "pkg:generic/foo"
            purl = pkg.to_top_level_purl(
                default_request, subpath="some/path" if has_subpath else None
            )

        assert mock_purl_method.called_once_with(*method_args)
        if has_subpath and pkg_type != "git-submodule":
            assert purl == "pkg:generic/foo#some/path"
        else:
            assert purl == "pkg:generic/foo"
