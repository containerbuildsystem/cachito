# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import json
import operator
import os
from unittest import mock

import pytest

from cachito.errors import CachitoError
from cachito.workers.pkg_managers import npm


@pytest.fixture()
def package_lock_deps():
    return {
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
            "requires": {"@angular-devkit/core": "8.3.26", "rxjs": "6.4.0"},
            "dependencies": {
                "rxjs": {
                    "version": "6.4.0",
                    "resolved": "https://registry.npmjs.org/rxjs/-/rxjs-6.4.0.tgz",
                    "integrity": (
                        "sha512-Z9Yfa11F6B9Sg/BK9MnqnQ+aQYicPLtilXBp2yUtDt2JRCE0h26d33EnfO3ZxoNx"
                        "G0T92OUucP3Ct7cpfkdFfw=="
                    ),
                    "dev": True,
                    "requires": {"tslib": "^1.9.0"},
                }
            },
        },
        "@angular/animations": {
            "version": "8.2.14",
            "resolved": "https://registry.npmjs.org/@angular/animations/-/animations-8.2.14.tgz",
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
    }


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
        },
        {
            "bundled": False,
            "dev": False,
            "name": "@angular/animations",
            "type": "npm",
            "version": "8.2.14",
        },
        {"bundled": False, "dev": True, "name": "rxjs", "type": "npm", "version": "6.4.0"},
        {"bundled": False, "dev": False, "name": "rxjs", "type": "npm", "version": "6.5.5"},
        {"bundled": False, "dev": False, "name": "tslib", "type": "npm", "version": "1.11.1"},
    ]
    return (package, deps)


def test_get_deps(package_lock_deps):
    name_to_deps = npm._get_deps(package_lock_deps)

    assert name_to_deps == {
        "@angular-devkit/architect": [
            {
                "bundled": False,
                "dev": True,
                "name": "@angular-devkit/architect",
                "type": "npm",
                "version": "0.803.26",
            }
        ],
        "@angular/animations": [
            {
                "bundled": False,
                "dev": False,
                "name": "@angular/animations",
                "type": "npm",
                "version": "8.2.14",
            }
        ],
        "rxjs": [
            {"bundled": False, "dev": True, "name": "rxjs", "type": "npm", "version": "6.4.0"},
            {"bundled": False, "dev": False, "name": "rxjs", "type": "npm", "version": "6.5.5"},
        ],
        "tslib": [
            {"bundled": False, "dev": False, "name": "tslib", "type": "npm", "version": "1.11.1"}
        ],
    }


def test_get_deps_non_registry_dep():
    package_lock_deps = {
        "@angular/animations": {
            "version": "8.2.14",
            "resolved": "https://registry.npmjs.org/@angular/animations/-/animations-8.2.14.tgz",
            "integrity": (
                "sha512-3Vc9TnNpKdtvKIXcWDFINSsnwgEMiDmLzjceWg1iYKwpeZGQahUXPoesLwQazBMmxJzQiA"
                "4HOMj0TTXKZ+Jzkg=="
            ),
            "requires": {"tslib": "^1.9.0"},
        },
        "tslib": {
            "version": "github:microsoft/tslib#c1f87f79190d61e1e4ca24af03894771cdf1aef9",
            "from": "github:microsoft/tslib",
        },
    }
    expected = (
        "The lock file contains a dependency not from the npm registry. This is not yet supported."
    )
    with pytest.raises(CachitoError, match=expected):
        npm._get_deps(package_lock_deps, {})


def test_get_package_and_deps(package_lock_deps, package_and_deps):
    package_lock = {"name": "han_solo", "version": "5.0.0", "dependencies": package_lock_deps}
    mock_open = mock.mock_open(read_data=json.dumps(package_lock))
    with mock.patch("cachito.workers.pkg_managers.npm.open", mock_open):
        package, deps = npm.get_package_and_deps(
            "/tmp/cachito-bundles/1/temp/app/package-lock.json"
        )

    deps = sorted(deps, key=operator.itemgetter("name", "version"))

    expected_package, expected_deps = package_and_deps
    assert package == expected_package
    assert deps == expected_deps


@pytest.mark.parametrize("shrink_wrap, package_lock", ((True, False), (True, True), (False, True)))
@mock.patch("cachito.workers.pkg_managers.npm.os.path.exists")
@mock.patch("cachito.workers.pkg_managers.npm.get_package_and_deps")
@mock.patch("cachito.workers.pkg_managers.npm.download_dependencies")
def test_resolve_npm(mock_dd, mock_gpad, mock_exists, shrink_wrap, package_lock, package_and_deps):
    mock_exists.side_effect = [shrink_wrap, package_lock]
    mock_gpad.return_value = package_and_deps
    expected_package, expected_deps = package_and_deps
    # Note that the deps returned by the get_package_and_deps function are modified as part of the
    # resolve_npm function. This is why a deep copy is necessary.
    expected_deps = copy.deepcopy(expected_deps)
    # Remove the "bundled" key as does the resolve_npm function to get expected returned
    # dependencies
    for dep in expected_deps:
        dep.pop("bundled")

    src_path = "/tmp/cachito-bundles/temp/1/app"
    package, deps = npm.resolve_npm(src_path, {"id": 1})

    assert package == expected_package
    assert deps == expected_deps
    if shrink_wrap:
        lock_file_path = os.path.join(src_path, "npm-shrinkwrap.json")
    elif package_lock:
        lock_file_path = os.path.join(src_path, "package-lock.json")
    mock_gpad.assert_called_once_with(lock_file_path)
    # We can't verify the actual correct deps value was passed in since the deps that were passed
    # in were mutated and mock does not keep a deepcopy of the function arguments.
    mock_dd.assert_called_once_with(1, mock.ANY)


@mock.patch("cachito.workers.pkg_managers.npm.os.path.exists")
@mock.patch("cachito.workers.pkg_managers.npm.download_dependencies")
def test_resolve_npm_no_lock(mock_dd, mock_exists):
    mock_exists.return_value = False

    expected = (
        "The npm-shrinkwrap.json or package-lock.json file must be present for the npm "
        "package manager"
    )
    with pytest.raises(CachitoError, match=expected):
        npm.resolve_npm("/tmp/cachito-bundles/temp/1/app", {"id": 1})


@mock.patch("cachito.workers.pkg_managers.npm.os.path.exists")
@mock.patch("cachito.workers.pkg_managers.npm.get_package_and_deps")
@mock.patch("cachito.workers.pkg_managers.npm.download_dependencies")
def test_resolve_npm_invalid_lock(mock_dd, mock_gpad, mock_exists):
    mock_exists.return_value = True
    mock_gpad.side_effect = KeyError()

    expected = "The lock file npm-shrinkwrap.json has an unexpected format"
    with pytest.raises(CachitoError, match=expected):
        npm.resolve_npm("/tmp/cachito-bundles/temp/1/app", {"id": 1})
