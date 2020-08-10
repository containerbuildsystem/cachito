# SPDX-License-Identifier: GPL-3.0-or-later
from unittest import mock

import pytest

from cachito.errors import ContentManifestError
from cachito.web.content_manifest import ContentManifest
from cachito.web.models import Package, Request


def test_process_go():
    pkg = Package.from_json(
        {"name": "example.com/org/project", "type": "go-package", "version": "1.1.1"}
    )
    expected_purl = "pkg:golang/example.com%2Forg%2Fproject@1.1.1"

    dep = Package.from_json(
        {"name": "example.com/org/project/lib", "type": "go-package", "version": "2.2.2"}
    )
    expected_dep_purl = "pkg:golang/example.com%2Forg%2Fproject%2Flib@2.2.2"

    src = Package.from_json(
        {"name": "example.com/anotherorg/project", "type": "gomod", "version": "3.3.3"}
    )
    expected_src_purl = "pkg:golang/example.com%2Fanotherorg%2Fproject@3.3.3"
    cm = ContentManifest()

    # emulate to_json behavior to setup internal packages cache
    cm._gomod_data.setdefault(pkg.name, [])
    cm._gopkg_data.setdefault(
        expected_purl, {"name": pkg.name, "purl": expected_purl, "dependencies": [], "sources": []}
    )

    cm.process_go_package(pkg, dep)
    cm.process_gomod(pkg, src)
    cm.set_go_package_sources()

    expected_contents = {
        expected_purl: {
            "purl": expected_purl,
            "dependencies": [{"purl": expected_dep_purl}],
            "sources": [{"purl": expected_src_purl}],
        }
    }

    assert cm._gopkg_data
    assert expected_purl in cm._gopkg_data
    assert cm._gopkg_data == expected_contents


def test_process_npm():
    pkg = Package.from_json({"name": "grc-ui", "type": "npm", "version": "1.0.0"})
    expected_purl = "pkg:npm/grc-ui@1.0.0"

    dep_commit_id = "7762177aacfb1ddf5ca45cebfe8de1da3b24f0ff"
    dep = Package.from_json(
        {
            "name": "security-middleware",
            "type": "npm",
            "version": f"github:open-cluster-management/security-middleware#{dep_commit_id}",
        }
    )
    expected_dep_purl = f"pkg:github/open-cluster-management/security-middleware@{dep_commit_id}"

    src = Package.from_json({"name": "@types/events", "type": "npm", "version": "3.0.0"})
    expected_src_purl = "pkg:npm/%40types/events@3.0.0"

    src.dev = True
    cm = ContentManifest()

    # emulate to_json behavior to setup internal packages cache
    cm._npm_data.setdefault(
        expected_purl, {"purl": expected_purl, "dependencies": [], "sources": []}
    )

    cm.process_npm_package(pkg, dep)
    cm.process_npm_package(pkg, src)

    expected_contents = {
        expected_purl: {
            "purl": expected_purl,
            "dependencies": [{"purl": expected_dep_purl}],
            "sources": [{"purl": expected_dep_purl}, {"purl": expected_src_purl}],
        }
    }

    assert cm._npm_data
    assert expected_purl in cm._npm_data
    assert cm._npm_data == expected_contents


@pytest.mark.parametrize(
    "package", [None, {"name": "example.com/org/project", "type": "go-package", "version": "1.1.1"}]
)
def test_to_json(app, package):
    request = Request()
    cm = ContentManifest(request)

    image_contents = []
    if package:
        pkg = Package.from_json(package)
        request.packages.append(pkg)
        content = {"purl": pkg.to_purl(), "dependencies": [], "sources": []}
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


@pytest.mark.parametrize("contents", [None, [], "foobar", 42, {"egg": "bacon"}])
def test_generate_icm(contents):
    cm = ContentManifest()
    expected = {
        "metadata": {
            "icm_version": 1,
            "icm_spec": ContentManifest.json_schema_url,
            "image_layer_index": -1,
        },
        "image_contents": contents or [],
    }
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
def test_set_go_package_sources(mock_warning, app, pkg_name, gomod_data, warn):
    cm = ContentManifest()

    main_purl = "pkg:golang/a-package"
    cm._gopkg_data = {
        main_purl: {"name": pkg_name, "purl": main_purl, "sources": [], "dependencies": []}
    }
    cm._gomod_data = gomod_data

    cm.set_go_package_sources()

    sources = []
    for v in gomod_data.values():
        if any(k in pkg_name for k in gomod_data.keys()):
            sources += v

    expected = {main_purl: {"purl": main_purl, "dependencies": [], "sources": sources}}

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
