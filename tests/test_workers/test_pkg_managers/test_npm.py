# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import json
import operator
import os
import re
from pathlib import Path
from typing import Any, Callable
from unittest import mock

import pytest

from cachito.errors import FileAccessError, InvalidRepoStructure, ValidationError
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers import general_js, npm
from cachito.workers.pkg_managers.npm import Package, PackageLock, PackageTreeNode


@pytest.fixture()
def lockfile_v1() -> dict[str, Any]:
    return {
        "name": "han_solo",
        "version": "5.0.0",
        "lockfileVersion": 1,
        "requires": True,
        "dependencies": {
            "@angular-devkit/architect": {
                "version": "0.803.26",
                "resolved": (
                    "https://registry.npmjs.org/@angular-devkit/architect/-/architect-0.803.26.tgz"
                ),
                "integrity": (
                    "sha512-mCynDvhGLElmuiaK5I6hVleMuZ1Svn7o5NnMW1ItiDlVZu1v49JWOxPS1A7C/"
                    "ypGmhjl9jMorVtz2IumtLgCXw=="
                ),
                "dev": True,
                "requires": {"rxjs": "6.4.0"},
                "dependencies": {
                    "rxjs": {
                        "version": "6.4.0",
                        "resolved": "https://registry.npmjs.org/rxjs/-/rxjs-6.4.0.tgz",
                        "integrity": (
                            "sha512-Z9Yfa11F6B9Sg/BK9MnqnQ+aQYicPLtilXBp2yUtDt2JRCE0h26d33"
                            "EnfO3ZxoNxG0T92OUucP3Ct7cpfkdFfw=="
                        ),
                        "dev": True,
                        "requires": {"tslib": "^1.9.0"},
                    }
                },
            },
            "@angular/animations": {
                "version": "8.2.14",
                "resolved": (
                    "https://registry.npmjs.org/@angular/animations" "/-/animations-8.2.14.tgz"
                ),
                "integrity": (
                    "sha512-3Vc9TnNpKdtvKIXcWDFINSsnwgEMiDmLzjceWg1iYKwpeZGQahUXPoesLwQazBMmxJzQiA"
                    "4HOMj0TTXKZ+Jzkg=="
                ),
                "requires": {"tslib": "^1.9.0"},
            },
            "rxjs": {
                "version": "6.5.5",
                "resolved": "https://registry.npmjs.org/rxjs/-/rxjs-6.5.5.tgz",
                "integrity": (
                    "sha512-WfQI+1gohdf0Dai/Bbmk5L5ItH5tYqm3ki2c5GdWhKjalzjg93N3avFjVStyZZz+A2Em+Z"
                    "xKH5bNghw9UeylGQ=="
                ),
                "requires": {"tslib": "^1.9.0"},
            },
            "tslib": {
                "version": "1.11.1",
                "resolved": "https://registry.npmjs.org/tslib/-/tslib-1.11.1.tgz",
                "integrity": (
                    "sha512-aZW88SY8kQbU7gpV19lN24LtXh/yD4ZZg6qieAJDDg+YBsJcSmLGK9QpnUjAKVG/"
                    "xefmvJGd1WUmfpT/g6AJGA=="
                ),
            },
        },
    }


@pytest.fixture()
def get_packages_v1() -> Callable[[dict[str, Any]], list[Package]]:
    def _get_packages_v1(lockfile: dict[str, Any]) -> list[Package]:
        deps = lockfile["dependencies"]
        architect_pkg = Package(
            "@angular-devkit/architect", deps["@angular-devkit/architect"], is_top_level=True
        )
        nested_rxjs_pkg = Package(
            "rxjs",
            deps["@angular-devkit/architect"]["dependencies"]["rxjs"],
            dependent_packages=[architect_pkg],
        )
        animations_pkg = Package(
            "@angular/animations", deps["@angular/animations"], is_top_level=True
        )
        rxjs_pkg = Package("rxjs", deps["rxjs"], is_top_level=True)
        tslib_pkg = Package(
            "tslib",
            deps["tslib"],
            is_top_level=True,
            dependent_packages=[nested_rxjs_pkg, animations_pkg, rxjs_pkg],
        )

        return [architect_pkg, nested_rxjs_pkg, animations_pkg, rxjs_pkg, tslib_pkg]

    return _get_packages_v1


@pytest.fixture()
def name_to_deps() -> dict[str, list]:
    return {
        "@angular-devkit/architect": [
            {
                "bundled": False,
                "dev": True,
                "name": "@angular-devkit/architect",
                "type": "npm",
                "version": "0.803.26",
                "version_in_nexus": None,
            }
        ],
        "@angular/animations": [
            {
                "bundled": False,
                "dev": False,
                "name": "@angular/animations",
                "type": "npm",
                "version": "8.2.14",
                "version_in_nexus": None,
            }
        ],
        "rxjs": [
            {
                "bundled": False,
                "dev": True,
                "name": "rxjs",
                "type": "npm",
                "version": "6.4.0",
                "version_in_nexus": None,
            },
            {
                "bundled": False,
                "dev": False,
                "name": "rxjs",
                "type": "npm",
                "version": "6.5.5",
                "version_in_nexus": None,
            },
        ],
        "tslib": [
            {
                "bundled": False,
                "dev": False,
                "name": "tslib",
                "type": "npm",
                "version": "1.11.1",
                "version_in_nexus": None,
            }
        ],
    }


@pytest.fixture()
def lockfile_v1_replacements(lockfile_v1: dict[str, Any]) -> dict[str, Any]:
    lockfile_v1["dependencies"]["@angular-devkit/architect"]["dependencies"]["rxjs"] = {
        "version": "github:ReactiveX/rxjs#dfa239d41b97504312fa95e13f4d593d95b49c4b",
        "from": "github:ReactiveX/rxjs#dfa239d41b97504312fa95e13f4d593d95b49c4b",
        "requires": {"tslib": "^1.9.0"},
        "dev": True,
    }
    lockfile_v1["dependencies"]["rxjs"] = {
        "version": "github:ReactiveX/rxjs#8cc6491771fcbf44984a419b7f26ff442a5d58f5",
        "from": "github:ReactiveX/rxjs#8cc6491771fcbf44984a419b7f26ff442a5d58f5",
        "requires": {"tslib": "^1.9.0"},
    }

    return lockfile_v1


@pytest.fixture()
def name_to_deps_v1_replacements(name_to_deps: dict[str, list]) -> dict[str, list]:
    name_to_deps["rxjs"] = [
        {
            "bundled": False,
            "dev": True,
            "name": "rxjs",
            "type": "npm",
            "version": "github:ReactiveX/rxjs#dfa239d41b97504312fa95e13f4d593d95b49c4b",
            "version_in_nexus": (
                "6.4.0-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b"
            ),
        },
        {
            "bundled": False,
            "dev": False,
            "name": "rxjs",
            "type": "npm",
            "version": "github:ReactiveX/rxjs#8cc6491771fcbf44984a419b7f26ff442a5d58f5",
            "version_in_nexus": (
                "6.5.5-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5"
            ),
        },
    ]

    return name_to_deps


@pytest.fixture()
def lockfile_v3() -> dict[str, Any]:
    return {
        "name": "han_solo",
        "version": "5.0.0",
        "lockfileVersion": 3,
        "requires": True,
        "packages": {
            "": {
                "name": "han_solo",
                "version": "5.0.0",
                "dependencies": {
                    "@angular/animations": "8.2.14",
                    "rxjs": "6.5.5",
                },
                "devDependencies": {"@angular-devkit/architect": "0.803.26"},
            },
            "node_modules/@angular-devkit/architect": {
                "version": "0.803.26",
                "resolved": (
                    "https://registry.npmjs.org/@angular-devkit/architect/"
                    "-/architect-0.803.26.tgz"
                ),
                "integrity": (
                    "sha512-mCynDvhGLElmuiaK5I6hVleMuZ1Svn7o5NnMW1ItiDlVZu1v49JWOxPS1A7C"
                    "/ypGmhjl9jMorVtz2IumtLgCXw=="
                ),
                "dev": True,
                "dependencies": {"rxjs": "6.4.0"},
            },
            "node_modules/@angular-devkit/architect/node_modules/rxjs": {
                "version": "6.4.0",
                "resolved": "https://registry.npmjs.org/rxjs/-/rxjs-6.4.0.tgz",
                "integrity": (
                    "sha512-Z9Yfa11F6B9Sg/BK9MnqnQ+aQYicPLtilXBp2yUtDt2JRCE0h26d33EnfO3Z"
                    "xoNxG0T92OUucP3Ct7cpfkdFfw=="
                ),
                "dev": True,
                "dependencies": {"tslib": "^1.9.0"},
            },
            "node_modules/@angular/animations": {
                "version": "8.2.14",
                "resolved": (
                    "https://registry.npmjs.org/@angular/animations/" "-/animations-8.2.14.tgz"
                ),
                "integrity": (
                    "sha512-3Vc9TnNpKdtvKIXcWDFINSsnwgEMiDmLzjceWg1iYKwpeZGQahU"
                    "XPoesLwQazBMmxJzQiA4HOMj0TTXKZ+Jzkg=="
                ),
                "dependencies": {"tslib": "^1.9.0"},
            },
            "node_modules/rxjs": {
                "version": "6.5.5",
                "resolved": "https://registry.npmjs.org/rxjs/-/rxjs-6.5.5.tgz",
                "integrity": (
                    "sha512-WfQI+1gohdf0Dai/Bbmk5L5ItH5tYqm3ki2c5GdWhKjalzjg93N3avFjVSty"
                    "ZZz+A2Em+ZxKH5bNghw9UeylGQ=="
                ),
                "dependencies": {"tslib": "^1.9.0"},
            },
            "node_modules/tslib": {
                "version": "1.11.1",
                "resolved": "https://registry.npmjs.org/tslib/-/tslib-1.11.1.tgz",
                "integrity": (
                    "sha512-aZW88SY8kQbU7gpV19lN24LtXh/yD4ZZg6qieAJDDg+YBsJcSmLGK9QpnUjA"
                    "KVG/xefmvJGd1WUmfpT/g6AJGA=="
                ),
            },
        },
    }


@pytest.fixture()
def get_packages_v3() -> Callable[[dict[str, Any]], list[Package]]:
    def _get_packages_v3(lockfile: dict[str, Any]) -> list[Package]:
        packages = lockfile["packages"]

        root = Package("han_solo", packages[""], is_top_level=True, path="")
        architect = Package(
            "@angular-devkit/architect",
            packages["node_modules/@angular-devkit/architect"],
            is_top_level=True,
            path="node_modules/@angular-devkit/architect",
            dependent_packages=[root],
        )
        architect_rxjs = Package(
            "rxjs",
            packages["node_modules/@angular-devkit/architect/node_modules/rxjs"],
            path="node_modules/@angular-devkit/architect/node_modules/rxjs",
            dependent_packages=[architect],
        )
        animations = Package(
            "@angular/animations",
            packages["node_modules/@angular/animations"],
            is_top_level=True,
            path="node_modules/@angular/animations",
            dependent_packages=[root],
        )
        rxjs = Package(
            "rxjs",
            packages["node_modules/rxjs"],
            is_top_level=True,
            path="node_modules/rxjs",
            dependent_packages=[root],
        )
        tslib = Package(
            "tslib",
            packages["node_modules/tslib"],
            is_top_level=True,
            path="node_modules/tslib",
            dependent_packages=[architect_rxjs, animations, rxjs],
        )

        return [architect, architect_rxjs, animations, rxjs, tslib]

    return _get_packages_v3


@pytest.fixture()
def lockfile_v3_replacements(lockfile_v3: dict[str, Any]) -> dict[str, Any]:
    lockfile_v3["packages"]["node_modules/@angular-devkit/architect/node_modules/rxjs"] = {
        "version": "6.4.0",
        "resolved": (
            "git+ssh://git@github.com/ReactiveX/rxjs.git#"
            "dfa239d41b97504312fa95e13f4d593d95b49c4b"
        ),
        "integrity": (
            "sha512-Z9Yfa11F6B9Sg/BK9MnqnQ+aQYicPLtilXBp2yUtDt2JRCE0h26d33EnfO3Z"
            "xoNxG0T92OUucP3Ct7cpfkdFfw=="
        ),
        "dev": True,
        "dependencies": {"tslib": "^1.9.0"},
    }
    lockfile_v3["packages"]["node_modules/rxjs"] = {
        "version": "6.5.5",
        "resolved": (
            "git+ssh://git@github.com/ReactiveX/rxjs.git#"
            "8cc6491771fcbf44984a419b7f26ff442a5d58f5"
        ),
        "integrity": (
            "sha512-WfQI+1gohdf0Dai/Bbmk5L5ItH5tYqm3ki2c5GdWhKjalzjg93N3avFjVSty"
            "ZZz+A2Em+ZxKH5bNghw9UeylGQ=="
        ),
        "dependencies": {"tslib": "^1.9.0"},
    }

    return lockfile_v3


@pytest.fixture()
def name_to_deps_v3_replacements(name_to_deps: dict[str, list]) -> dict[str, list]:
    name_to_deps["rxjs"] = [
        {
            "bundled": False,
            "dev": True,
            "name": "rxjs",
            "type": "npm",
            "version": (
                "git+ssh://git@github.com/ReactiveX/rxjs.git#"
                "dfa239d41b97504312fa95e13f4d593d95b49c4b"
            ),
            "version_in_nexus": (
                "6.4.0-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b"
            ),
        },
        {
            "bundled": False,
            "dev": False,
            "name": "rxjs",
            "type": "npm",
            "version": (
                "git+ssh://git@github.com/ReactiveX/rxjs.git#"
                "8cc6491771fcbf44984a419b7f26ff442a5d58f5"
            ),
            "version_in_nexus": (
                "6.5.5-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5"
            ),
        },
    ]

    return name_to_deps


@pytest.fixture()
def package_and_deps():
    """Provide sample data for npm.get_package_and_deps."""
    package = {"name": "han_solo", "type": "npm", "version": "5.0.0"}
    deps = [
        {
            "bundled": False,
            "dev": True,
            "name": "@angular-devkit/architect",
            "type": "npm",
            "version": "0.803.26",
            "version_in_nexus": None,
        },
        {
            "bundled": False,
            "dev": False,
            "name": "@angular/animations",
            "type": "npm",
            "version": "8.2.14",
            "version_in_nexus": None,
        },
        {
            "bundled": False,
            "dev": True,
            "name": "rxjs",
            "type": "npm",
            "version": "6.4.0",
            "version_in_nexus": None,
        },
        {
            "bundled": False,
            "dev": False,
            "name": "rxjs",
            "type": "npm",
            "version": "6.5.5",
            "version_in_nexus": None,
        },
        {
            "bundled": False,
            "dev": False,
            "name": "tslib",
            "type": "npm",
            "version": "1.11.1",
            "version_in_nexus": None,
        },
    ]
    return {
        "deps": deps,
        "lock_file": None,
        "package": package,
        "package.json": None,
    }


class TestPackage:
    @pytest.mark.parametrize(
        "package, expected_resolved_url",
        [
            pytest.param(
                Package(
                    "foo",
                    {
                        "version": "1.0.0",
                        "resolved": "https://some.registry.org/foo/-/foo-1.0.0.tgz",
                    },
                ),
                "https://some.registry.org/foo/-/foo-1.0.0.tgz",
                id="registry_dependency",
            ),
            pytest.param(
                Package(
                    "foo",
                    {
                        "version": "https://foohub.org/foo/-/foo-1.0.0.tgz",
                    },
                ),
                "https://foohub.org/foo/-/foo-1.0.0.tgz",
                id="non_registry_dependency",
            ),
            pytest.param(
                Package(
                    "foo",
                    {
                        "version": "1.0.0",
                        "resolved": "https://some.registry.org/foo/-/foo-1.0.0.tgz",
                    },
                    path="node_modules/foo",
                ),
                "https://some.registry.org/foo/-/foo-1.0.0.tgz",
                id="package",
            ),
            pytest.param(
                Package(
                    "foo",
                    {
                        "version": "1.0.0",
                    },
                    path="foo",
                ),
                "file:foo",
                id="workspace_package",
            ),
        ],
    )
    def test_get_resolved_url(self, package: Package, expected_resolved_url: str) -> None:
        assert package.resolved_url == expected_resolved_url

    @pytest.mark.parametrize(
        "package, is_dev",
        [
            (
                Package("foo", {"dev": True}),
                True,
            ),
            (
                Package("foo", {"dev": False}),
                False,
            ),
            (
                Package("foo", {"devOptional": True}),
                False,
            ),
            (
                Package("foo", {"devOptional": False}),
                False,
            ),
            (
                Package("foo", {"dev": True, "optional": True}),
                True,
            ),
        ],
    )
    def test_dev(self, package: Package, is_dev: bool) -> None:
        assert package.dev == is_dev

    @pytest.mark.parametrize(
        "package, expected_names",
        [
            pytest.param(
                Package(
                    "foo",
                    {"requires": {"foo": "1", "bar": "2"}},
                ),
                ["foo", "bar"],
                id="v1_package",
            ),
            pytest.param(
                Package(
                    "foo",
                    {
                        "dependencies": {"bar": "1"},
                        "devDependencies": {"baz": "1"},
                        "optionalDependencies": {"spam": "1"},
                        "peerDependencies": {"eggs": "1"},
                    },
                    path="node_modules/foo",
                ),
                ["bar", "baz", "spam", "eggs"],
                id="v2_package",
            ),
        ],
    )
    def test_get_dependency_names(self, package: Package, expected_names: dict[str, str]) -> None:
        assert sorted(package.get_dependency_names()) == sorted(expected_names)

    @pytest.mark.parametrize(
        "package, expected_package_data",
        [
            pytest.param(
                Package(
                    "foo",
                    {"requires": {"bar": "1"}},
                ),
                {"requires": {"bar": "2"}},
                id="v1_package",
            ),
            pytest.param(
                Package(
                    "foo",
                    {
                        "dependencies": {"bar": "1"},
                        "devDependencies": {"baz": "1"},
                        "optionalDependencies": {"spam": "1"},
                        "peerDependencies": {"eggs": "1"},
                    },
                    path="node_modules/foo",
                ),
                {
                    "dependencies": {"bar": "2"},
                    "devDependencies": {"baz": "3"},
                    "optionalDependencies": {"spam": "4"},
                    "peerDependencies": {"eggs": "5"},
                },
                id="v2_package",
            ),
        ],
    )
    def test_replace_dependency_version(
        self, package: Package, expected_package_data: dict[str, str]
    ) -> None:
        for version, deps in enumerate(package._package_dict.values(), start=2):
            for dep_name, _ in deps.items():
                package.replace_dependency_version(dep_name, str(version))
        assert package._package_dict == expected_package_data

    def test_eq(self):
        assert Package("foo", "", {}) == Package("foo", "", {})
        assert Package("foo", "", {}) != Package("bar", "", {})
        assert 1 != Package("foo", "", {})


class TestPackageLock:
    def test_get_dependencies(
        self, tmp_path: Path, lockfile_v1: dict[str, Any], get_packages_v1: list[Package]
    ) -> None:
        package_lock = PackageLock(tmp_path, lockfile_v1)
        assert package_lock.packages == get_packages_v1(lockfile_v1)

    def test_get_packages(
        self,
        tmp_path: Path,
        lockfile_v3: dict[str, Any],
        get_packages_v3: list[Package],
    ) -> None:
        package_lock = PackageLock(tmp_path, lockfile_v3)
        assert package_lock.packages == get_packages_v3(lockfile_v3)


@pytest.mark.parametrize("pkg_version", (1, 2))
def test_resolve_dependent_packages(pkg_version: int) -> None:
    if pkg_version > 1:
        dep_key = "dependencies"
        path = "irrelevant/path"
    else:
        dep_key = "requires"
        path = None

    root = PackageTreeNode()
    foo = PackageTreeNode(
        Package("foo", {dep_key: {"bar": "2", "baz": "1"}}, path=path),
        root,
        {},
    )
    bar_nested = PackageTreeNode(
        Package("bar", {dep_key: {"baz": "2"}}, path=path),
        foo,
        {},
    )
    baz_nested = PackageTreeNode(
        Package("baz", {}, path=path),
        bar_nested,
        {},
    )
    bar = PackageTreeNode(
        Package("bar", {}, path=path),
        root,
        {},
    )
    baz = PackageTreeNode(
        Package("baz", {}, path=path),
        root,
        {},
    )
    root.children = {"foo": foo, "bar": bar, "baz": baz}
    foo.children = {"bar": bar_nested, "baz": baz}
    bar_nested.children = {"baz": baz_nested}

    npm._resolve_dependent_packages(root)
    assert foo.package.dependent_packages == []
    assert bar_nested.package.dependent_packages == [foo.package]
    assert baz_nested.package.dependent_packages == [bar_nested.package]
    assert bar.package.dependent_packages == []
    assert baz.package.dependent_packages == [foo.package]


def test_get_v2_package_tree() -> None:
    paths_to_packages = {
        "": Package("root", {}, path=""),
        "foo_workspace": Package("foo_workspace", {}, path="foo_workspace"),
        "node_modules/bar": Package("bar", {}, path="node_modules/bar"),
        "node_modules/foo_workspace": Package(
            "foo_workspace",
            {"resolved": "foo_workspace", "link": True},
            path="node_modules/foo_workspace",
        ),
        "node_modules/foo_workspace/node_modules/bar": Package(
            "bar", {}, path="node_modules/foo_workspace/node_modules/bar"
        ),
    }

    root_node = npm._get_v2_package_tree(paths_to_packages)

    assert root_node.parent is None
    assert root_node.package is None
    assert {name: node.package for name, node in root_node.children.items()} == {
        "root": paths_to_packages[""],
        "foo_workspace": paths_to_packages["foo_workspace"],
        "bar": paths_to_packages["node_modules/bar"],
    }

    root_pkg_node = root_node.children["root"]
    assert root_pkg_node.parent == root_node
    assert root_pkg_node.package == paths_to_packages[""]
    assert root_pkg_node.children == {}

    foo_pkg_node = root_node.children["foo_workspace"]
    assert foo_pkg_node.parent == root_node
    assert foo_pkg_node.package == paths_to_packages["foo_workspace"]
    assert {name: node.package for name, node in foo_pkg_node.children.items()} == {
        "bar": paths_to_packages["node_modules/foo_workspace/node_modules/bar"]
    }

    bar_pkg_node = root_node.children["bar"]
    assert bar_pkg_node.parent == root_node
    assert bar_pkg_node.package == paths_to_packages["node_modules/bar"]
    assert bar_pkg_node.children == {}

    foo_bar_pkg_node = foo_pkg_node.children["bar"]
    assert foo_bar_pkg_node.parent == foo_pkg_node
    assert (
        foo_bar_pkg_node.package == paths_to_packages["node_modules/foo_workspace/node_modules/bar"]
    )
    assert foo_bar_pkg_node.children == {}


@pytest.mark.parametrize(
    "pkg_path, node_paths, expected_path",
    [
        (
            Path(""),
            {},
            "ROOTPATH",
        ),
        (
            Path("foo"),
            {"foo"},
            "ROOTPATH",
        ),
        (
            Path("@foo/bar"),
            {"@foo/bar"},
            "ROOTPATH",
        ),
        (
            Path("packages/foo"),
            {"packages/foo"},
            "ROOTPATH",
        ),
        (
            Path("packages/@foo/bar"),
            {"packages/@foo/bar"},
            "ROOTPATH",
        ),
        (
            Path("spam/packages/foo"),
            {"spam", "spam/packages/foo"},
            "spam",
        ),
        (
            Path("spam/packages/@foo/bar"),
            {"spam", "spam/packages/@foo/bar"},
            "spam",
        ),
    ],
)
def test_get_fs_parent_node(pkg_path: str, node_paths: set[str], expected_path: str) -> None:
    root_node = mock.Mock()
    root_node.path = "ROOTPATH"

    paths_to_nodes = {}
    for path in node_paths:
        mock_package_node = mock.Mock()
        mock_package_node.path = path
        paths_to_nodes[Path(path)] = mock_package_node

    assert npm._get_fsparent_node(pkg_path, root_node, paths_to_nodes).path == expected_path


@pytest.mark.parametrize(
    "pkg_path, node_paths, expected_path",
    [
        (
            Path(""),
            {},
            "ROOTPATH",
        ),
        (
            Path("node_modules/foo"),
            {"node_modules/foo"},
            "ROOTPATH",
        ),
        (
            Path("node_modules/@foo/bar"),
            {"node_modules/@foo/bar"},
            "ROOTPATH",
        ),
        (
            Path("node_modules/foo/node_modules/bar"),
            {"node_modules/foo", "node_modules/foo/node_modules/bar"},
            "node_modules/foo",
        ),
        (
            Path("node_modules/foo/node_modules/@foo/bar"),
            {"node_modules/foo", "node_modules/foo/node_modules/@foo/bar"},
            "node_modules/foo",
        ),
        (
            Path("node_modules/@foo/bar/node_modules/baz"),
            {"node_modules/@foo/bar", "node_modules/@foo/bar/node_modules/baz"},
            "node_modules/@foo/bar",
        ),
        (
            Path("node_modules/@foo/bar/node_modules/@baz/qux"),
            {"node_modules/@foo/bar", "node_modules/@foo/bar/node_modules/@baz/qux"},
            "node_modules/@foo/bar",
        ),
    ],
)
def test_get_parent_node(pkg_path: str, node_paths: set[str], expected_path: str) -> None:
    root_node = mock.Mock()
    root_node.path = "ROOTPATH"

    paths_to_nodes = {}
    for path in node_paths:
        mock_package_node = mock.Mock()
        mock_package_node.path = path
        mock_package_node.package.path = path
        mock_package_node.package.is_link = False
        paths_to_nodes[Path(path)] = mock_package_node

    assert npm._get_parent_node(pkg_path, root_node, paths_to_nodes).path == expected_path


@pytest.mark.parametrize(
    "pkg_path, node_paths, link_pkg, expected_path",
    [
        (
            Path("node_modules/foo/node_modules/bar"),
            {"foo", "node_modules/foo", "node_modules/foo/node_modules/bar"},
            "node_modules/foo",
            "foo",
        ),
        (
            Path("node_modules/foo/node_modules/bar"),
            {"packages/foo", "node_modules/foo", "node_modules/foo/node_modules/bar"},
            "node_modules/foo",
            "packages/foo",
        ),
        (
            Path("node_modules/foo/node_modules/@foo/bar"),
            {"foo", "node_modules/foo", "node_modules/foo/node_modules/@foo/bar"},
            "node_modules/foo",
            "foo",
        ),
        (
            Path("node_modules/@foo/bar/node_modules/baz"),
            {"@foo/bar", "node_modules/@foo/bar", "node_modules/@foo/bar/node_modules/baz"},
            "node_modules/@foo/bar",
            "@foo/bar",
        ),
        (
            Path("node_modules/@foo/bar/node_modules/@baz/qux"),
            {"@foo/bar", "node_modules/@foo/bar", "node_modules/@foo/bar/node_modules/@baz/qux"},
            "node_modules/@foo/bar",
            "@foo/bar",
        ),
    ],
)
def test_get_parent_node_is_link(
    pkg_path: str, node_paths: set[str], link_pkg: str, expected_path: str
) -> None:
    root_node = mock.Mock()
    root_node.path = "ROOTPATH"

    paths_to_nodes = {}
    for path in node_paths:
        mock_package_node = mock.Mock()
        mock_package_node.path = path
        mock_package_node.package.path = path
        if path == link_pkg:
            mock_package_node.package.is_link = True
            mock_package_node.package.resolved_url = expected_path
        else:
            mock_package_node.package.is_link = False
        paths_to_nodes[Path(path)] = mock_package_node

    assert npm._get_parent_node(pkg_path, root_node, paths_to_nodes).path == expected_path


@pytest.mark.parametrize(
    "lockfile_fixture, packages_fixture, name_to_deps_fixture",
    [
        ("lockfile_v1", "get_packages_v1", "name_to_deps"),
        ("lockfile_v3", "get_packages_v3", "name_to_deps"),
    ],
)
def test_get_deps(
    lockfile_fixture: str,
    packages_fixture: str,
    name_to_deps_fixture: str,
    request: pytest.FixtureRequest,
) -> None:
    lockfile = request.getfixturevalue(lockfile_fixture)
    packages = request.getfixturevalue(packages_fixture)(lockfile)
    expected_name_to_deps = request.getfixturevalue(name_to_deps_fixture)

    package_lock = mock.Mock()
    package_lock.packages = packages
    name_to_deps, replacements = npm._get_deps(package_lock, set())

    assert name_to_deps == expected_name_to_deps
    assert replacements == []


@pytest.mark.parametrize(
    "lockfile_fixture, packages_fixture, name_to_deps_fixture",
    [
        ("lockfile_v1_replacements", "get_packages_v1", "name_to_deps_v1_replacements"),
        ("lockfile_v3_replacements", "get_packages_v3", "name_to_deps_v3_replacements"),
    ],
)
def test_get_deps_non_registry_dep(
    lockfile_fixture: str,
    packages_fixture: str,
    name_to_deps_fixture: str,
    request: pytest.FixtureRequest,
) -> None:
    lockfile = request.getfixturevalue(lockfile_fixture)
    packages = request.getfixturevalue(packages_fixture)(lockfile)
    expected_name_to_deps = request.getfixturevalue(name_to_deps_fixture)

    package_lock = mock.Mock()
    package_lock.packages = packages

    replacement_versions = {
        "github:ReactiveX/rxjs#dfa239d41b97504312fa95e13f4d593d95b49c4b": (
            "6.4.0-external-gitcommit-" "dfa239d41b97504312fa95e13f4d593d95b49c4b"
        ),
        "git+ssh://git@github.com/ReactiveX/rxjs.git#dfa239d41b97504312fa95e13f4d593d95b49c4b": (
            "6.4.0-external-gitcommit-" "dfa239d41b97504312fa95e13f4d593d95b49c4b"
        ),
        "github:ReactiveX/rxjs#8cc6491771fcbf44984a419b7f26ff442a5d58f5": (
            "6.5.5-external-gitcommit-" "8cc6491771fcbf44984a419b7f26ff442a5d58f5"
        ),
        "git+ssh://git@github.com/ReactiveX/rxjs.git#8cc6491771fcbf44984a419b7f26ff442a5d58f5": (
            "6.5.5-external-gitcommit-" "8cc6491771fcbf44984a419b7f26ff442a5d58f5"
        ),
    }

    def _mock_convert_to_nexus_hosted(package: Package) -> None:
        package.version = replacement_versions[package.resolved_url]

    with mock.patch(
        "cachito.workers.pkg_managers.npm._convert_to_nexus_hosted",
        new=_mock_convert_to_nexus_hosted,
    ):
        name_to_deps, replacements = npm._get_deps(package_lock, set())

    assert name_to_deps == expected_name_to_deps
    # Verify that only the top level replacements are returned
    assert replacements == [
        ("rxjs", "6.5.5-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5")
    ]


@pytest.mark.parametrize(
    "packages, expected_name_to_deps",
    [
        pytest.param(
            [
                Package(
                    "foo",
                    {
                        "version": "1.0.0",
                        "bundled": True,
                    },
                ),
                Package(
                    "foo",
                    {
                        "version": "1.0.0",
                        "resolved": "https://registry.npmjs.org/foo/-/foo-1.0.0.tgz",
                    },
                ),
            ],
            {
                "foo": [
                    {
                        "bundled": False,
                        "dev": False,
                        "name": "foo",
                        "type": "npm",
                        "version": "1.0.0",
                        "version_in_nexus": None,
                    }
                ],
            },
            id="v1_packages_bundle_duplicate",
        ),
        pytest.param(
            [
                Package(
                    "foo",
                    {
                        "version": "1.0.0",
                        "bundled": True,
                    },
                ),
                Package(
                    "foo",
                    {
                        "version": "2.0.0",
                        "resolved": "https://registry.npmjs.org/foo/-/foo-1.0.0.tgz",
                    },
                ),
            ],
            {
                "foo": [
                    {
                        "bundled": True,
                        "dev": False,
                        "name": "foo",
                        "type": "npm",
                        "version": "1.0.0",
                        "version_in_nexus": None,
                    },
                    {
                        "bundled": False,
                        "dev": False,
                        "name": "foo",
                        "type": "npm",
                        "version": "2.0.0",
                        "version_in_nexus": None,
                    },
                ],
            },
            id="v1_packages",
        ),
        pytest.param(
            [
                Package(
                    "foo",
                    {
                        "version": "1.0.0",
                        "inBundle": True,
                    },
                    path="node_modules/spam/node_modules/foo",
                ),
                Package(
                    "foo",
                    {
                        "version": "1.0.0",
                        "resolved": "https://registry.npmjs.org/foo/-/foo-1.0.0.tgz",
                    },
                    path="node_modules/foo",
                ),
            ],
            {
                "foo": [
                    {
                        "bundled": False,
                        "dev": False,
                        "name": "foo",
                        "type": "npm",
                        "version": "1.0.0",
                        "version_in_nexus": None,
                    }
                ],
            },
            id="v2_packages_bundle_duplicate",
        ),
        pytest.param(
            [
                Package(
                    "foo",
                    {
                        "version": "1.0.0",
                        "inBundle": True,
                    },
                    path="node_modules/spam/node_modules/foo",
                ),
                Package(
                    "foo",
                    {
                        "version": "2.0.0",
                        "resolved": "https://registry.npmjs.org/foo/-/foo-1.0.0.tgz",
                    },
                    path="node_modules/foo",
                ),
            ],
            {
                "foo": [
                    {
                        "bundled": True,
                        "dev": False,
                        "name": "foo",
                        "type": "npm",
                        "version": "1.0.0",
                        "version_in_nexus": None,
                    },
                    {
                        "bundled": False,
                        "dev": False,
                        "name": "foo",
                        "type": "npm",
                        "version": "2.0.0",
                        "version_in_nexus": None,
                    },
                ],
            },
            id="v2_packages",
        ),
        pytest.param(
            [
                # direct bundled dep - should be downloaded, shouldn't be considered bundled
                # npm init --yes
                # npm add fecha --save-bundle
                Package(
                    "fecha",
                    {
                        "version": "4.2.3",
                        "resolved": "https://registry.npmjs.org/fecha/-/fecha-4.2.3.tgz",
                        "inBundle": True,
                    },
                    path="node_modules/fecha",
                ),
                # indirect bundled dep, duplicate of the direct one
                Package(
                    "fecha",
                    {
                        "version": "4.2.3",
                        # the result should be non-bundled and non-dev
                        "inBundle": True,
                        "dev": True,
                    },
                    path="node_modules/foo/node_modules/fecha",
                ),
            ],
            {
                "fecha": [
                    {
                        "bundled": False,
                        "dev": False,
                        "name": "fecha",
                        "type": "npm",
                        "version": "4.2.3",
                        "version_in_nexus": None,
                    },
                ],
            },
            id="v2_packages_direct_bundled_dep",
        ),
    ],
)
def test_get_deps_bundled_dep(
    packages: list[Package],
    expected_name_to_deps: dict[str, dict],
) -> None:
    package_lock = mock.Mock()
    package_lock.packages = packages
    name_to_deps, replacements = npm._get_deps(package_lock, set())

    assert name_to_deps == expected_name_to_deps
    assert replacements == []


@pytest.mark.parametrize(
    "packages",
    [
        pytest.param(
            [
                Package(
                    "jsplumb",
                    {
                        "version": "file:jsplumb-2.10.2.tgz",
                        "integrity": (
                            "sha512-I6R70uG8HTBl4bDae8Tj4WpwRRS0RPLPDw/cZOqNFkk+qhQ241rL"
                            "q8ynuC7dN4CKtihxybAvqvk+FrsLau3fOA=="
                        ),
                    },
                ),
                Package(
                    "rxjs",
                    {
                        "version": "6.5.5",
                        "resolved": "https://registry.npmjs.org/rxjs/-/rxjs-6.5.5.tgz",
                        "integrity": (
                            "sha512-WfQI+1gohdf0Dai/Bbmk5L5ItH5tYqm3ki2c5GdWhKjalzjg93N3"
                            "avFjVStyZZz+A2Em+ZxKH5bNghw9UeylGQ=="
                        ),
                        "requires": {"tslib": "^1.9.0"},
                    },
                ),
            ],
            id="v1_package",
        ),
        pytest.param(
            [
                Package(
                    "jsplumb",
                    {
                        "version": "2.10.2",
                        "resolved": "file:jsplumb-2.10.2.tgz",
                        "integrity": (
                            "sha512-I6R70uG8HTBl4bDae8Tj4WpwRRS0RPLPDw/cZOqNFkk+qhQ241rL"
                            "q8ynuC7dN4CKtihxybAvqvk+FrsLau3fOA=="
                        ),
                    },
                    path="node_modules/jsplumb",
                ),
                Package(
                    "rxjs",
                    {
                        "version": "6.5.5",
                        "resolved": "https://registry.npmjs.org/rxjs/-/rxjs-6.5.5.tgz",
                        "integrity": (
                            "sha512-WfQI+1gohdf0Dai/Bbmk5L5ItH5tYqm3ki2c5GdWhKjalzjg93N3"
                            "avFjVStyZZz+A2Em+ZxKH5bNghw9UeylGQ=="
                        ),
                        "dependencies": {"tslib": "^1.9.0"},
                    },
                ),
            ],
            id="v2_package",
        ),
    ],
)
def test_get_deps_allowlisted_file_dep(packages):
    package_lock = mock.Mock()
    package_lock.packages = packages
    package_lock.workspaces = []
    name_to_deps, replacements = npm._get_deps(package_lock, {"jsplumb"})

    assert name_to_deps == {
        "jsplumb": [
            {
                "bundled": False,
                "dev": False,
                "name": "jsplumb",
                "type": "npm",
                "version": "file:jsplumb-2.10.2.tgz",
                "version_in_nexus": None,
            }
        ],
        "rxjs": [
            {
                "bundled": False,
                "dev": False,
                "name": "rxjs",
                "type": "npm",
                "version": "6.5.5",
                "version_in_nexus": None,
            },
        ],
    }
    assert replacements == []


@pytest.mark.parametrize(
    "packages,workspaces,allowlist,result",
    [
        ([], [], set(), {}),
        (
            [Package("a", {"version": "file:a"})],
            ["a"],
            set(),
            {
                "a": [
                    {
                        "bundled": False,
                        "dev": False,
                        "type": "npm",
                        "name": "a",
                        "version": "file:a",
                        "version_in_nexus": None,
                    },
                ],
            },
        ),
        (
            [
                Package("a", {"version": "file:a"}),
                Package(
                    "tslib",
                    {
                        "version": "1.11.1",
                        "resolved": "https://registry.npmjs.org/tslib/-/tslib-1.11.1.tgz",
                    },
                ),
            ],
            ["a"],
            set(),
            {
                "tslib": [
                    {
                        "bundled": False,
                        "dev": False,
                        "type": "npm",
                        "name": "tslib",
                        "version": "1.11.1",
                        "version_in_nexus": None,
                    },
                ],
                "a": [
                    {
                        "bundled": False,
                        "dev": False,
                        "type": "npm",
                        "name": "a",
                        "version": "file:a",
                        "version_in_nexus": None,
                    },
                ],
            },
        ),
        (
            [Package("a", {"version": "file:a"}), Package("b", {"version": "file:b"})],
            ["a", "b"],
            set(),
            {
                "a": [
                    {
                        "bundled": False,
                        "dev": False,
                        "type": "npm",
                        "name": "a",
                        "version": "file:a",
                        "version_in_nexus": None,
                    },
                ],
                "b": [
                    {
                        "bundled": False,
                        "dev": False,
                        "type": "npm",
                        "name": "b",
                        "version": "file:b",
                        "version_in_nexus": None,
                    },
                ],
            },
        ),
        (
            [
                Package(
                    "tslib",
                    {
                        "version": "1.11.1",
                        "resolved": "https://registry.npmjs.org/tslib/-/tslib-1.11.1.tgz",
                    },
                ),
                Package("a", {"version": "file:a"}),
                Package("b", {"version": "file:b"}),
            ],
            ["b", "a"],
            set(),
            {
                "tslib": [
                    {
                        "bundled": False,
                        "dev": False,
                        "type": "npm",
                        "name": "tslib",
                        "version": "1.11.1",
                        "version_in_nexus": None,
                    },
                ],
                "a": [
                    {
                        "bundled": False,
                        "dev": False,
                        "type": "npm",
                        "name": "a",
                        "version": "file:a",
                        "version_in_nexus": None,
                    },
                ],
                "b": [
                    {
                        "bundled": False,
                        "dev": False,
                        "type": "npm",
                        "name": "b",
                        "version": "file:b",
                        "version_in_nexus": None,
                    },
                ],
            },
        ),
        (
            [Package("a", {"version": "file:a"}), Package("b", {"version": "file:b"})],
            ["a"],
            {"b"},
            {
                "a": [
                    {
                        "bundled": False,
                        "dev": False,
                        "type": "npm",
                        "name": "a",
                        "version": "file:a",
                        "version_in_nexus": None,
                    },
                ],
                "b": [
                    {
                        "bundled": False,
                        "dev": False,
                        "type": "npm",
                        "name": "b",
                        "version": "file:b",
                        "version_in_nexus": None,
                    },
                ],
            },
        ),
    ],
)
def test_get_deps_workspaces(packages, workspaces, allowlist, result):
    package_lock = mock.Mock()
    package_lock.packages = packages
    package_lock.workspaces = workspaces
    name_to_deps, replacements = npm._get_deps(package_lock, allowlist)
    assert name_to_deps == result
    assert replacements == []


@pytest.mark.parametrize(
    "package, dep_key",
    [
        pytest.param(
            Package(
                "foo",
                {"version": "https://foo-1.0.tgz", "integrity": "abc"},
            ),
            "requires",
            id="package_without_deps",
        ),
        pytest.param(
            Package(
                "foo",
                {"version": "https://foo-1.0.tgz", "integrity": "abc"},
                dependent_packages=[Package("bar", {"requires": {"foo": "https://foo-1.0.tgz"}})],
            ),
            "requires",
            id="v1_package_with_deps",
        ),
        pytest.param(
            Package(
                "foo",
                {"version": "1.0", "resolved": "https://foo-1.0.tgz", "integrity": "abc"},
                path="node_modules/foo",
                dependent_packages=[
                    Package(
                        "bar",
                        {"dependencies": {"foo": "https://foo-1.0.tgz"}},
                        path="node_modules/bar",
                    )
                ],
            ),
            "dependencies",
            id="v2_package_with_deps",
        ),
    ],
)
@mock.patch("cachito.workers.pkg_managers.npm.process_non_registry_dependency")
def test_convert_to_nexus_hosted(
    mock_process_non_registry_dep, package: Package, dep_key: str
) -> None:
    js_dependency = general_js.JSDependency(
        name=package.name, source=package.resolved_url, integrity=package.integrity
    )
    with mock.patch(
        "cachito.workers.pkg_managers.npm.Package.replace_dependency_version"
    ) as mock_replace:
        npm._convert_to_nexus_hosted(package)
        if package.dependent_packages:
            replace_call = mock.call(
                package.alias or package.name, mock_process_non_registry_dep.return_value.version
            )
            mock_replace.assert_has_calls([replace_call for _ in package.dependent_packages])
        else:
            mock_replace.assert_not_called()

    assert package.version == mock_process_non_registry_dep.return_value.version
    assert package.integrity == mock_process_non_registry_dep.return_value.integrity
    assert package._package_dict["resolved"] == mock_process_non_registry_dep.return_value.source

    mock_process_non_registry_dep.assert_called_once_with(js_dependency)


@pytest.mark.parametrize(
    "packages",
    [
        pytest.param(
            [
                Package(
                    "@angular/animations",
                    {
                        "version": "8.2.14",
                        "resolved": (
                            "https://registry.npmjs.org/@angular/animations/"
                            "-/animations-8.2.14.tgz"
                        ),
                        "integrity": (
                            "sha512-3Vc9TnNpKdtvKIXcWDFINSsnwgEMiDmLzjceWg1iYKwpeZGQahUX"
                            "PoesLwQazBMmxJzQiA4HOMj0TTXKZ+Jzkg=="
                        ),
                        "requires": {"tslib": "^1.9.0"},
                    },
                ),
                Package(
                    "tslib",
                    {
                        "version": "file:tslib.tar.gz",
                        "integrity": (
                            "sha512-ZETBuz/jo9ivHHolRRfYZgK5Zd2F5KZ/Yk7iygP8y8YEFLe5ZHCV"
                            "Y5zJMHiP3WeA8M/yvPKN7XJpM03KH7FtPw=="
                        ),
                    },
                ),
            ],
            id="v1_package",
        ),
        pytest.param(
            [
                Package(
                    "@angular/animations",
                    {
                        "version": "8.2.14",
                        "resolved": (
                            "https://registry.npmjs.org/@angular/animations/"
                            "-/animations-8.2.14.tgz"
                        ),
                        "integrity": (
                            "sha512-3Vc9TnNpKdtvKIXcWDFINSsnwgEMiDmLzjceWg1iYKwpeZGQahUX"
                            "PoesLwQazBMmxJzQiA4HOMj0TTXKZ+Jzkg=="
                        ),
                        "dependencies": {"tslib": "^1.9.0"},
                    },
                    path="node_modules/@angular/animations",
                ),
                Package(
                    "tslib",
                    {
                        "version": "1.9.0",
                        "resolved": "file:tslib.tar.gz",
                        "integrity": (
                            "sha512-ZETBuz/jo9ivHHolRRfYZgK5Zd2F5KZ/Yk7iygP8y8YEFLe5ZHCV"
                            "Y5zJMHiP3WeA8M/yvPKN7XJpM03KH7FtPw=="
                        ),
                        "dependencies": {"tslib": "^1.9.0"},
                    },
                    path="tslib.tar.gz",
                ),
            ],
            id="v2_package",
        ),
    ],
)
def test_get_deps_unsupported_non_registry_dep(packages):
    package_lock = mock.Mock()
    package_lock.packages = packages
    package_lock.workspaces = []
    expected = re.escape(
        "tslib@file:tslib.tar.gz is a 'file:' dependency. File dependencies are allowed if: "
        "a) the dependency is declared as a workspace in package.json or "
        "b) the dependency is present in the server-side allowlist."
    )
    with pytest.raises(InvalidRepoStructure, match=expected):
        npm._get_deps(package_lock, set())


def test_get_npm_proxy_repo_name():
    assert npm.get_npm_proxy_repo_name(3) == "cachito-npm-3"


def test_get_npm_proxy_repo_url():
    assert npm.get_npm_proxy_repo_url(3).endswith("/repository/cachito-npm-3/")


def test_get_npm_proxy_username():
    assert npm.get_npm_proxy_username(3) == "cachito-npm-3"


@pytest.mark.parametrize(
    "lockfile_fixture, file_dep, dep_key",
    [
        (
            "lockfile_v1",
            {
                "millennium-falcon": {
                    "version": "file:millennium-falcon-1.0.0.tgz",
                    "integrity": (
                        "sha512-I6R70uG8HTBl4bDae8Tj4WpwRRS0RPLPDw/cZOqNFkk+qhQ241rLq8yn"
                        "uC7dN4CKtihxybAvqvk+FrsLau3fOA=="
                    ),
                },
            },
            "dependencies",
        ),
        (
            "lockfile_v3",
            {
                "node_modules/millennium-falcon": {
                    "version": "1.0.0",
                    "resolved": "file:millennium-falcon-1.0.0.tgz",
                    "integrity": (
                        "sha512-I6R70uG8HTBl4bDae8Tj4WpwRRS0RPLPDw/cZOqNFkk+qhQ241rLq8yn"
                        "uC7dN4CKtihxybAvqvk+FrsLau3fOA=="
                    ),
                },
            },
            "packages",
        ),
    ],
)
def test_get_package_and_deps(
    tmp_path: Path,
    package_and_deps: dict[str, Any],
    lockfile_fixture: str,
    file_dep: dict[str, dict],
    dep_key: str,
    request: pytest.FixtureRequest,
) -> None:
    lockfile_data = request.getfixturevalue(lockfile_fixture)
    lockfile_data[dep_key].update(file_dep)
    package_and_deps["deps"].insert(
        3,
        {
            "bundled": False,
            "dev": False,
            "name": "millennium-falcon",
            "type": "npm",
            "version": "file:millennium-falcon-1.0.0.tgz",
            "version_in_nexus": None,
        },
    )
    package_and_deps["deps"].sort(key=operator.itemgetter("name", "version"))
    mock_open = mock.mock_open(read_data=json.dumps(lockfile_data))
    with mock.patch("pathlib.Path.open", mock_open):
        deps_info = npm.get_package_and_deps(
            tmp_path / "package.json",
            tmp_path / "package-lock.json",
        )

    deps_info["deps"].sort(key=operator.itemgetter("name", "version"))
    mock_open.assert_called_once()
    assert deps_info == package_and_deps


@pytest.mark.parametrize(
    "type", ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
)
def test_get_package_and_deps_dep_replacements_v1(
    tmp_path: Path, lockfile_v1_replacements, name_to_deps_v1_replacements, type
) -> None:
    lockfile_data = lockfile_v1_replacements
    name_to_deps_data = name_to_deps_v1_replacements

    replaced_top_level_rxjs = {
        "version": "6.5.5-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5",
        "resolved": (
            "https://nexus.domain.local/repository/cachito-js-hosted/rxjs/-/"
            "rxjs-6.5.5-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5.tgz"
        ),
        "integrity": (
            "sha512-vvAdzoVTdbr5Lq7BI2+l4R3dM4Mw7305wNKLgij8ru7sx3Fuo1W2XrsoTXWfPtIk+kxiBXxCoc8"
            "UX1Vb45kbRQ=="
        ),
        "requires": {"tslib": "^1.9.0"},
    }
    replaced_second_level_rxjs = {
        "version": "6.4.0-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b",
        "resolved": (
            "https://nexus.domain.local/repository/cachito-js-hosted/rxjs/-/"
            "rxjs-6.4.0-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b.tgz"
        ),
        "integrity": (
            "sha512-l4R3dM4Mw7305wNKLg+l4R3dM4Mw7305wNKLgij8ru7sx3Fuo1W2XrsoTXWfPtIk+kxiBXxCoc8"
            "UX1Vb45kbRQ=="
        ),
        "dev": True,
        "requires": {"tslib": "^1.9.0"},
    }
    post_replacement_lockfile_data = copy.deepcopy(lockfile_data)
    post_replacement_lockfile_data["dependencies"]["rxjs"] = replaced_top_level_rxjs
    post_replacement_lockfile_data["dependencies"]["@angular-devkit/architect"]["dependencies"][
        "rxjs"
    ] = replaced_second_level_rxjs

    package_json = {
        type: {
            "rxjs": {"version": "github:ReactiveX/rxjs#8cc6491771fcbf44984a419b7f26ff442a5d58f5"},
            "tslib": {"version": "1.11.1"},
        },
    }

    def _mock_get_deps(package_lock: PackageLock, file_deps_allowlist: set[str]):
        package_lock._lockfile_data["dependencies"]["rxjs"] = replaced_top_level_rxjs
        package_lock._lockfile_data["dependencies"]["@angular-devkit/architect"]["dependencies"][
            "rxjs"
        ] = replaced_second_level_rxjs
        name_to_deps = name_to_deps_data
        replacements = [
            ("rxjs", "6.5.5-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5"),
        ]

        return name_to_deps, replacements

    with mock.patch("cachito.workers.pkg_managers.npm._get_deps", new=_mock_get_deps):
        with mock.patch("pathlib.Path.open") as mock_open:
            mock_open.side_effect = [
                mock.mock_open(read_data=json.dumps(lockfile_data)).return_value,
                mock.mock_open(read_data=json.dumps(package_json)).return_value,
            ]
            deps_info = npm.get_package_and_deps(
                tmp_path / "package.json",
                tmp_path / "package-lock.json",
            )

    assert deps_info == {
        "deps": [
            {
                "bundled": False,
                "dev": True,
                "name": "@angular-devkit/architect",
                "type": "npm",
                "version": "0.803.26",
                "version_in_nexus": None,
            },
            {
                "bundled": False,
                "dev": False,
                "name": "@angular/animations",
                "type": "npm",
                "version": "8.2.14",
                "version_in_nexus": None,
            },
            {
                "bundled": False,
                "dev": True,
                "name": "rxjs",
                "type": "npm",
                "version": "github:ReactiveX/rxjs#dfa239d41b97504312fa95e13f4d593d95b49c4b",
                "version_in_nexus": (
                    "6.4.0-external-gitcommit-" "dfa239d41b97504312fa95e13f4d593d95b49c4b"
                ),
            },
            {
                "bundled": False,
                "dev": False,
                "name": "rxjs",
                "type": "npm",
                "version": "github:ReactiveX/rxjs#8cc6491771fcbf44984a419b7f26ff442a5d58f5",
                "version_in_nexus": (
                    "6.5.5-external-gitcommit-" "8cc6491771fcbf44984a419b7f26ff442a5d58f5"
                ),
            },
            {
                "bundled": False,
                "dev": False,
                "name": "tslib",
                "type": "npm",
                "version": "1.11.1",
                "version_in_nexus": None,
            },
        ],
        # Verify that the lock file was detected as having been modified
        "lock_file": post_replacement_lockfile_data,
        "package": {"name": "han_solo", "type": "npm", "version": "5.0.0"},
        "package.json": {
            type: {
                # Verify that package.json was updated with the hosted version of rxjs
                "rxjs": "6.5.5-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5",
                "tslib": {"version": "1.11.1"},
            },
        },
    }


@pytest.mark.parametrize(
    "type", ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
)
def test_get_package_and_deps_dep_replacements_v3(
    tmp_path: Path, lockfile_v3_replacements, name_to_deps_v3_replacements, type
) -> None:
    lockfile_data = lockfile_v3_replacements
    name_to_deps_data = name_to_deps_v3_replacements

    replaced_top_level_rxjs = {
        "version": "6.5.5-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5",
        "resolved": (
            "https://nexus.domain.local/repository/cachito-js-hosted/rxjs/-/"
            "rxjs-6.5.5-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5.tgz"
        ),
        "integrity": (
            "sha512-vvAdzoVTdbr5Lq7BI2+l4R3dM4Mw7305wNKLgij8ru7sx3Fuo1W2XrsoTXWfPtIk+kxiBXxCoc8"
            "UX1Vb45kbRQ=="
        ),
        "dependencies": {"tslib": "^1.9.0"},
    }
    replaced_second_level_rxjs = {
        "version": "6.4.0-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b",
        "resolved": (
            "https://nexus.domain.local/repository/cachito-js-hosted/rxjs/-/"
            "rxjs-6.4.0-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b.tgz"
        ),
        "integrity": (
            "sha512-l4R3dM4Mw7305wNKLg+l4R3dM4Mw7305wNKLgij8ru7sx3Fuo1W2XrsoTXWfPtIk+kxiBXxCoc8"
            "UX1Vb45kbRQ=="
        ),
        "dev": True,
        "dependencies": {"tslib": "^1.9.0"},
    }
    post_replacement_lockfile_data = copy.deepcopy(lockfile_data)
    post_replacement_lockfile_data["packages"]["node_modules/rxjs"] = replaced_top_level_rxjs
    post_replacement_lockfile_data["packages"][
        "node_modules/@angular-devkit/architect/node_modules/rxjs"
    ] = replaced_second_level_rxjs

    package_json = {
        type: {
            "rxjs": {"version": "github:ReactiveX/rxjs#8cc6491771fcbf44984a419b7f26ff442a5d58f5"},
            "tslib": {"version": "1.11.1"},
        },
    }

    def _mock_get_deps(package_lock: PackageLock, file_deps_allowlist: set[str]):
        package_lock._lockfile_data["packages"]["node_modules/rxjs"] = replaced_top_level_rxjs
        package_lock._lockfile_data["packages"][
            "node_modules/@angular-devkit/architect/node_modules/rxjs"
        ] = replaced_second_level_rxjs
        name_to_deps = name_to_deps_data
        replacements = [
            ("rxjs", "6.5.5-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5"),
        ]

        return name_to_deps, replacements

    with mock.patch("cachito.workers.pkg_managers.npm._get_deps", new=_mock_get_deps):
        with mock.patch("pathlib.Path.open") as mock_open:
            mock_open.side_effect = [
                mock.mock_open(read_data=json.dumps(lockfile_data)).return_value,
                mock.mock_open(read_data=json.dumps(package_json)).return_value,
            ]
            deps_info = npm.get_package_and_deps(
                tmp_path / "package.json",
                tmp_path / "package-lock.json",
            )

    assert deps_info == {
        "deps": [
            {
                "bundled": False,
                "dev": True,
                "name": "@angular-devkit/architect",
                "type": "npm",
                "version": "0.803.26",
                "version_in_nexus": None,
            },
            {
                "bundled": False,
                "dev": False,
                "name": "@angular/animations",
                "type": "npm",
                "version": "8.2.14",
                "version_in_nexus": None,
            },
            {
                "bundled": False,
                "dev": True,
                "name": "rxjs",
                "type": "npm",
                "version": (
                    "git+ssh://git@github.com/ReactiveX/rxjs.git#"
                    "dfa239d41b97504312fa95e13f4d593d95b49c4b"
                ),
                "version_in_nexus": (
                    "6.4.0-external-gitcommit-" "dfa239d41b97504312fa95e13f4d593d95b49c4b"
                ),
            },
            {
                "bundled": False,
                "dev": False,
                "name": "rxjs",
                "type": "npm",
                "version": (
                    "git+ssh://git@github.com/ReactiveX/rxjs.git#"
                    "8cc6491771fcbf44984a419b7f26ff442a5d58f5"
                ),
                "version_in_nexus": (
                    "6.5.5-external-gitcommit-" "8cc6491771fcbf44984a419b7f26ff442a5d58f5"
                ),
            },
            {
                "bundled": False,
                "dev": False,
                "name": "tslib",
                "type": "npm",
                "version": "1.11.1",
                "version_in_nexus": None,
            },
        ],
        # Verify that the lock file was detected as having been modified
        "lock_file": post_replacement_lockfile_data,
        "package": {"name": "han_solo", "type": "npm", "version": "5.0.0"},
        "package.json": {
            type: {
                # Verify that package.json was updated with the hosted version of rxjs
                "rxjs": "6.5.5-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5",
                "tslib": {"version": "1.11.1"},
            },
        },
    }


@pytest.mark.parametrize("shrink_wrap, package_lock", ((True, False), (True, True), (False, True)))
@mock.patch("cachito.workers.pkg_managers.npm.os.path.exists")
@mock.patch("cachito.workers.pkg_managers.npm.get_package_and_deps")
@mock.patch("cachito.workers.pkg_managers.npm.download_dependencies")
@mock.patch("cachito.workers.config.get_worker_config")
def test_resolve_npm(
    get_worker_config,
    mock_dd,
    mock_gpad,
    mock_exists,
    shrink_wrap,
    package_lock,
    package_and_deps,
    tmpdir,
):
    get_worker_config.return_value = mock.Mock(cachito_bundles_dir=str(tmpdir))
    package_json = True
    mock_dd.return_value = {
        "@angular-devkit/architect@0.803.26",
        "@angular/animations@8.2.14",
        "rxjs@6.4.0",
        "rxjs@6.5.5",
        "tslib@1.11.1",
    }
    # Note that the dictionary returned by the get_package_and_deps function is modified as part of
    # the resolve_npm function. This is why a deep copy is necessary.
    expected_deps_info = copy.deepcopy(package_and_deps)
    expected_deps_info["downloaded_deps"] = {
        "@angular-devkit/architect@0.803.26",
        "@angular/animations@8.2.14",
        "rxjs@6.4.0",
        "rxjs@6.5.5",
        "tslib@1.11.1",
    }
    if shrink_wrap:
        expected_deps_info["lock_file_name"] = "npm-shrinkwrap.json"
        mock_exists.side_effect = [shrink_wrap, package_json]
    else:
        expected_deps_info["lock_file_name"] = "package-lock.json"
        mock_exists.side_effect = [shrink_wrap, package_lock, package_json]
    mock_gpad.return_value = package_and_deps
    # Remove the "bundled" key as does the resolve_npm function to get expected returned
    # dependencies
    for dep in expected_deps_info["deps"]:
        dep.pop("bundled")
        dep.pop("version_in_nexus")

    src_path = "/tmp/cachito-bundles/temp/1/app"
    deps_info = npm.resolve_npm(src_path, {"id": 1})

    assert deps_info == expected_deps_info
    package_json_path = os.path.join(src_path, "package.json")
    if shrink_wrap:
        lock_file_path = os.path.join(src_path, "npm-shrinkwrap.json")
    elif package_lock:
        lock_file_path = os.path.join(src_path, "package-lock.json")
    mock_gpad.assert_called_once_with(package_json_path, lock_file_path)
    # We can't verify the actual correct deps value was passed in since the deps that were passed
    # in were mutated and mock does not keep a deepcopy of the function arguments.
    mock_dd.assert_called_once_with(RequestBundleDir(1).npm_deps_dir, mock.ANY, mock.ANY, mock.ANY)


@mock.patch("cachito.workers.pkg_managers.npm.os.path.exists")
@mock.patch("cachito.workers.pkg_managers.npm.download_dependencies")
def test_resolve_npm_no_lock(mock_dd, mock_exists):
    mock_exists.return_value = False

    expected = (
        "The npm-shrinkwrap.json or package-lock.json file must be present for the npm "
        "package manager"
    )
    with pytest.raises(FileAccessError, match=expected):
        npm.resolve_npm("/tmp/cachito-bundles/temp/1/app", {"id": 1})


@mock.patch("cachito.workers.pkg_managers.npm.os.path.exists")
@mock.patch("cachito.workers.pkg_managers.npm.get_package_and_deps")
@mock.patch("cachito.workers.pkg_managers.npm.download_dependencies")
def test_resolve_npm_invalid_lock(mock_dd, mock_gpad, mock_exists):
    mock_exists.return_value = True
    mock_gpad.side_effect = KeyError("name")

    expected = "The lock file npm-shrinkwrap.json has an unexpected format (missing key: 'name')"
    with pytest.raises(ValidationError, match=re.escape(expected)):
        npm.resolve_npm("/tmp/cachito-bundles/temp/1/app", {"id": 1})


@pytest.mark.parametrize("lockfileversion", [0, 4])
def test_invalid_lockfileversion(tmp_path: Path, lockfileversion: int) -> None:
    lockfile = {
        "lockfileVersion": lockfileversion,
    }
    with mock.patch("pathlib.Path.open") as mock_open:
        mock_open.return_value = mock.mock_open(read_data=json.dumps(lockfile)).return_value
        with pytest.raises(ValidationError):
            PackageLock.from_file(tmp_path / "package-lock.json")
