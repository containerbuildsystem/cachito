# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import json
import operator
import os
import re
from unittest import mock

import pytest

from cachito.errors import CachitoError
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers import general_js, npm


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


def test_get_deps(package_lock_deps):
    name_to_deps, replacements = npm._get_deps(package_lock_deps, set())

    assert name_to_deps == {
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
    assert replacements == []


@mock.patch("cachito.workers.pkg_managers.npm.convert_to_nexus_hosted")
def test_get_deps_non_registry_dep(mock_ctnh, package_lock_deps):
    # Set the rxjs dependencies to be directly from GitHub
    package_lock_deps["@angular-devkit/architect"]["dependencies"]["rxjs"] = {
        "version": "github:ReactiveX/rxjs#dfa239d41b97504312fa95e13f4d593d95b49c4b",
        "from": "github:ReactiveX/rxjs#dfa239d41b97504312fa95e13f4d593d95b49c4b",
        "requires": {"tslib": "^1.9.0"},
    }
    nexus_hosted_info = {
        "version": "6.5.5-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b",
        "resolved": (
            "https://nexus.domain.local/repository/cachito-js-hosted/rxjs/-/"
            "rxjs-6.5.5-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b.tgz"
        ),
        "integrity": (
            "sha512-vvAdzoVTdbr5Lq7BI2+l4R3dM4Mw7305wNKLgij8ru7sx3Fuo1W2XrsoTXWfPtIk+kxiBXxCoc8UX"
            "1Vb45kbRQ=="
        ),
        "requires": {"tslib": "^1.9.0"},
    }

    package_lock_deps["rxjs"] = {
        "version": "github:ReactiveX/rxjs#8cc6491771fcbf44984a419b7f26ff442a5d58f5",
        "from": "github:ReactiveX/rxjs#8cc6491771fcbf44984a419b7f26ff442a5d58f5",
        "requires": {"tslib": "^1.9.0"},
    }
    nexus_hosted_info_two = {
        "version": "6.5.2-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5",
        "resolved": (
            "https://nexus.domain.local/repository/cachito-js-hosted/rxjs/-/"
            "rxjs-6.5.2-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5.tgz"
        ),
        "integrity": (
            "sha512-AvAdzoVTdVT5Lq7BI2+l5R3dM4Mw7305wNKLgij8rh7sx3Fuo1W2XrsoTXWfPtIk+kxiBXxCoc8UX"
            "1Vb45kbRP=="
        ),
        "requires": {"tslib": "^1.9.0"},
    }

    # Python 3 iterates through a dictionary in alphabetical order, so this order will always be
    # correct
    mock_ctnh.side_effect = [copy.deepcopy(nexus_hosted_info), copy.deepcopy(nexus_hosted_info_two)]

    name_to_deps, replacements = npm._get_deps(package_lock_deps, set())

    assert name_to_deps == {
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
                "dev": False,
                "name": "rxjs",
                "type": "npm",
                "version": "github:ReactiveX/rxjs#dfa239d41b97504312fa95e13f4d593d95b49c4b",
                "version_in_nexus": (
                    "6.5.5-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b"
                ),
            },
            {
                "bundled": False,
                "dev": False,
                "name": "rxjs",
                "type": "npm",
                "version": "github:ReactiveX/rxjs#8cc6491771fcbf44984a419b7f26ff442a5d58f5",
                "version_in_nexus": (
                    "6.5.2-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5"
                ),
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
    # Verify that only the top level replacements are returned
    assert replacements == [
        ("rxjs", "6.5.2-external-gitcommit-8cc6491771fcbf44984a419b7f26ff442a5d58f5")
    ]
    # Ensure the lock file was updated with the Nexus hosted dependency
    assert (
        package_lock_deps["@angular-devkit/architect"]["dependencies"]["rxjs"] == nexus_hosted_info
    )
    assert package_lock_deps["rxjs"] == nexus_hosted_info_two
    assert package_lock_deps["@angular-devkit/architect"]["requires"] == {
        "@angular-devkit/core": "8.3.26",
        "rxjs": "6.5.5-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b",
    }

    assert mock_ctnh.call_count == 2


def test_get_deps_allowlisted_file_dep():
    package_lock_deps = {
        "jsplumb": {
            "version": "file:jsplumb-2.10.2.tgz",
            "integrity": (
                "sha512-I6R70uG8HTBl4bDae8Tj4WpwRRS0RPLPDw/cZOqNFkk+qhQ241rLq8ynuC7dN4CKtihxybAvqv"
                "k+FrsLau3fOA=="
            ),
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
    }
    name_to_deps, replacements = npm._get_deps(package_lock_deps, {"jsplumb"})

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


@mock.patch("cachito.workers.pkg_managers.npm.process_non_registry_dependency")
def test_convert_to_nexus_hosted(mock_process_non_registry_dep):
    dep_name = "rxjs"
    # The information from the lock file
    dep_info = {
        "version": "github:ReactiveX/rxjs#8cc6491771fcbf44984a419b7f26ff442a5d58f5",
        "from": "github:ReactiveX/rxjs#8cc6491771fcbf44984a419b7f26ff442a5d58f5",
        "requires": {"tslib": "^1.9.0"},
    }

    assert npm.convert_to_nexus_hosted(dep_name, dep_info) == {
        "integrity": mock_process_non_registry_dep.return_value.integrity,
        "resolved": mock_process_non_registry_dep.return_value.source,
        "version": mock_process_non_registry_dep.return_value.version,
        "requires": dep_info["requires"],
    }
    mock_process_non_registry_dep.assert_called_once_with(
        general_js.JSDependency(name=dep_name, source=dep_info["version"], integrity=None)
    )


def test_get_deps_unsupported_non_registry_dep():
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
            "version": "file:tslib.tar.gz",
            "integrity": (
                "sha512-ZETBuz/jo9ivHHolRRfYZgK5Zd2F5KZ/Yk7iygP8y8YEFLe5ZHCVY5zJMHiP3WeA8M/yvPKN7"
                "XJpM03KH7FtPw=="
            ),
        },
    }
    expected = "The dependency tslib@file:tslib.tar.gz is hosted in an unsupported location"
    with pytest.raises(CachitoError, match=expected):
        npm._get_deps(package_lock_deps, set(), {})


def test_get_npm_proxy_repo_name():
    assert npm.get_npm_proxy_repo_name(3) == "cachito-npm-3"


def test_get_npm_proxy_repo_url():
    assert npm.get_npm_proxy_repo_url(3).endswith("/repository/cachito-npm-3/")


def test_get_npm_proxy_username():
    assert npm.get_npm_proxy_username(3) == "cachito-npm-3"


def test_get_package_and_deps(package_lock_deps, package_and_deps):
    package_lock_deps["millennium-falcon"] = {
        "version": "file:millennium-falcon-1.0.0.tgz",
        "integrity": (
            "sha512-I6R70uG8HTBl4bDae8Tj4WpwRRS0RPLPDw/cZOqNFkk+qhQ241rLq8ynuC7dN4CKtihxybAvqvk+Fr"
            "sLau3fOA=="
        ),
    }
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

    package_lock = {"name": "han_solo", "version": "5.0.0", "dependencies": package_lock_deps}
    mock_open = mock.mock_open(read_data=json.dumps(package_lock))
    with mock.patch("cachito.workers.pkg_managers.npm.open", mock_open):
        deps_info = npm.get_package_and_deps(
            "/tmp/cachito-bundles/1/temp/app/package.json",
            "/tmp/cachito-bundles/1/temp/app/package-lock.json",
        )

    deps_info["deps"].sort(key=operator.itemgetter("name", "version"))
    mock_open.assert_called_once()
    assert deps_info == package_and_deps


@pytest.mark.parametrize(
    "type", ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
)
def test_get_package_and_deps_dep_replacements(package_lock_deps, package_and_deps, type):
    package_lock = {
        "name": "star-wars",
        "version": "5.0.0",
        "dependencies": {
            "rxjs": {
                "version": "github:ReactiveX/rxjs#dfa239d41b97504312fa95e13f4d593d95b49c4b",
                "from": "github:ReactiveX/rxjs#dfa239d41b97504312fa95e13f4d593d95b49c4b",
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
    package_json = {
        type: {
            "rxjs": {"version": "github:ReactiveX/rxjs#dfa239d41b97504312fa95e13f4d593d95b49c4b"},
            "tslib": {"version": "1.11.1"},
        },
    }

    def _mock_get_deps(_deps, file_deps_allowlist):
        _deps["rxjs"] = {
            "version": "6.5.5-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b",
            "resolved": (
                "https://nexus.domain.local/repository/cachito-js-hosted/rxjs/-/"
                "rxjs-6.5.5-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b.tgz"
            ),
            "integrity": (
                "sha512-vvAdzoVTdbr5Lq7BI2+l4R3dM4Mw7305wNKLgij8ru7sx3Fuo1W2XrsoTXWfPtIk+kxiBXxCoc8"
                "UX1Vb45kbRQ=="
            ),
            "requires": {"tslib": "^1.9.0"},
        }
        name_to_deps = {
            "rxjs": [
                {
                    "bundled": False,
                    "dev": False,
                    "name": "rxjs",
                    "version": "6.5.5",
                    "version_in_nexus": (
                        "6.5.5-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b"
                    ),
                },
            ],
            "tslib": [
                {
                    "bundled": False,
                    "dev": False,
                    "name": "rxjs",
                    "version": "1.11.1",
                    "version_in_nexus": None,
                },
            ],
        }
        replacements = [
            ("rxjs", "6.5.5-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b"),
        ]

        return name_to_deps, replacements

    with mock.patch("cachito.workers.pkg_managers.npm._get_deps", new=_mock_get_deps):
        with mock.patch("cachito.workers.pkg_managers.npm.open") as mock_open:
            mock_open.side_effect = [
                mock.mock_open(read_data=json.dumps(package_lock)).return_value,
                mock.mock_open(read_data=json.dumps(package_json)).return_value,
            ]
            deps_info = npm.get_package_and_deps(
                "/tmp/cachito-bundles/1/temp/app/package.json",
                "/tmp/cachito-bundles/1/temp/app/package-lock.json",
            )

    assert deps_info == {
        "deps": [
            {
                "bundled": False,
                "dev": False,
                "name": "rxjs",
                "version": "6.5.5",
                "version_in_nexus": (
                    "6.5.5-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b"
                ),
            },
            {
                "bundled": False,
                "dev": False,
                "name": "rxjs",
                "version": "1.11.1",
                "version_in_nexus": None,
            },
        ],
        # Verify that the lock file was detected as having been modified
        "lock_file": {
            "dependencies": {
                "rxjs": {
                    "integrity": (
                        "sha512-vvAdzoVTdbr5Lq7BI2+l4R3dM4Mw7305wNKLgij8ru7sx3Fuo1W2XrsoTXWfPtIk+k"
                        "xiBXxCoc8UX1Vb45kbRQ=="
                    ),
                    "requires": {"tslib": "^1.9.0"},
                    "resolved": (
                        "https://nexus.domain.local/repository/cachito-js-hosted/rxjs/-/"
                        "rxjs-6.5.5-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b.tgz"
                    ),
                    "version": "6.5.5-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b",
                },
                "tslib": {
                    "integrity": (
                        "sha512-aZW88SY8kQbU7gpV19lN24LtXh/yD4ZZg6qieAJDDg+YBsJcSmLGK9Qp"
                        "nUjAKVG/xefmvJGd1WUmfpT/g6AJGA=="
                    ),
                    "resolved": "https://registry.npmjs.org/tslib/-/tslib-1.11.1.tgz",
                    "version": "1.11.1",
                },
            },
            "name": "star-wars",
            "version": "5.0.0",
        },
        "package": {"name": "star-wars", "type": "npm", "version": "5.0.0"},
        "package.json": {
            type: {
                # Verify that package.json was updated with the hosted version of rxjs
                "rxjs": "6.5.5-external-gitcommit-dfa239d41b97504312fa95e13f4d593d95b49c4b",
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
    with pytest.raises(CachitoError, match=expected):
        npm.resolve_npm("/tmp/cachito-bundles/temp/1/app", {"id": 1})


@mock.patch("cachito.workers.pkg_managers.npm.os.path.exists")
@mock.patch("cachito.workers.pkg_managers.npm.get_package_and_deps")
@mock.patch("cachito.workers.pkg_managers.npm.download_dependencies")
def test_resolve_npm_invalid_lock(mock_dd, mock_gpad, mock_exists):
    mock_exists.return_value = True
    mock_gpad.side_effect = KeyError("name")

    expected = "The lock file npm-shrinkwrap.json has an unexpected format (missing key: 'name')"
    with pytest.raises(CachitoError, match=re.escape(expected)):
        npm.resolve_npm("/tmp/cachito-bundles/temp/1/app", {"id": 1})
