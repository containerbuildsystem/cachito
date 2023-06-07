import copy
import json
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from cachito.errors import InvalidRepoStructure, NexusError
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers import yarn
from cachito.workers.pkg_managers.general_js import JSDependency
from tests.helper_utils import Symlink, write_file_tree

REGISTRY_DEP_URL = "https://registry.yarnpkg.com/chai/-/chai-4.2.0.tgz"

HTTP_DEP_URL = "https://example.org/fecha.tar.gz"
HTTP_DEP_URL_WITH_CHECKSUM = f"{HTTP_DEP_URL}#123456"
HTTP_DEP_NEXUS_VERSION = "1.0.0-external"
HTTP_DEP_NEXUS_URL = "http://nexus.example.org/repository/js/fecha.tar.gz"

GIT_DEP_URL = "git+https://github.com/example/leftpad.git"
GIT_DEP_URL_WITH_REF = f"{GIT_DEP_URL}#abcdef"
GIT_DEP_NEXUS_VERSION = "2.0.0-external"
GIT_DEP_NEXUS_URL = "http://nexus.example.org/repository/js/leftpad.tar.gz"

MOCK_INTEGRITY = "sha1-abcdefghijklmnopqrstuvwxyzo="
MOCK_NEXUS_VERSION = "1.0.0-external"

OPTIONAL_DEP_URL = "git+https://github.com/example/pathval.git"
PEER_DEP_URL = "git+https://github.com/example/deep-eql.git"

MINIMAL_PACKAGE_JSON = {"name": "hitchhikers-guide", "version": "42.0.0"}

EXAMPLE_PACKAGE_JSON = {
    **MINIMAL_PACKAGE_JSON,
    "dependencies": {"chai": "^4.2.0", "fecha": HTTP_DEP_URL},
    "devDependencies": {"leftpad": GIT_DEP_URL},
    "optionalDependencies": {"pathval": OPTIONAL_DEP_URL},
    "peerDependencies": {"deep-eql": PEER_DEP_URL},
}


def replaced_example_packjson(replacements):
    """
    Get a copy of the example package.json with replacements applied.

    Replacements are a list of (dep_type, dep_name, new_version) tuples.
    """
    packjson = copy.deepcopy(EXAMPLE_PACKAGE_JSON)
    for dep_type, dep_name, new_version in replacements:
        packjson[dep_type][dep_name] = new_version
    return packjson


def yarn_lock_to_str(yarn_lock_data: dict[str, Any]) -> str:
    lockfile = yarn.pyarn.lockfile.Lockfile("1", yarn_lock_data)
    return lockfile.to_str()


def test_get_npm_proxy_repo_name():
    assert yarn.get_yarn_proxy_repo_name(3) == "cachito-yarn-3"


def test_get_npm_proxy_repo_url():
    assert yarn.get_yarn_proxy_repo_url(3).endswith("/repository/cachito-yarn-3/")


def test_get_npm_proxy_username():
    assert yarn.get_yarn_proxy_repo_username(3) == "cachito-yarn-3"


@pytest.mark.parametrize(
    "integrity_value, expected",
    [
        # only one option
        ("sha1-asdf", "sha1-asdf"),
        ("sha512-asdf", "sha512-asdf"),
        # prioritize strongest algorithm
        ("sha384-asdf sha512-qwer", "sha512-qwer"),
        ("sha256-asdf sha384-qwer", "sha384-qwer"),
        ("sha1-asdf sha256-qwer", "sha256-qwer"),
        ("sha256-asdf sha1-qwer", "sha256-asdf"),
        ("sha256-asdf sha512-qwer sha1-zxcv", "sha512-qwer"),
        # if only weak algorithms are present, just pick the first one
        ("sha1-asdf md5-qwer", "sha1-asdf"),
        ("md5-asdf sha1-qwer", "md5-asdf"),
    ],
)
def test_pick_strongest_crypto_hash(integrity_value, expected):
    assert yarn._pick_strongest_crypto_hash(integrity_value) == expected


@mock.patch("cachito.workers.pkg_managers.yarn.convert_hex_sha_to_npm")
@mock.patch("cachito.workers.pkg_managers.yarn._pick_strongest_crypto_hash")
@mock.patch("cachito.workers.pkg_managers.yarn.process_non_registry_dependency")
@pytest.mark.parametrize(
    "dep_name, dep_source, dep_info, expected_jsdep, convert_sha_call",
    [
        (
            "subpackage",
            "file:./subpackage",
            {"version": "1.0.0"},
            JSDependency("subpackage", source="file:./subpackage"),
            None,
        ),
        (
            "fecha",
            "https://example.org/fecha.tar.gz#123456",
            {"version": "2.0.0"},
            JSDependency(
                "fecha",
                source="https://example.org/fecha.tar.gz#123456",
                integrity=MOCK_INTEGRITY,
            ),
            ("123456", "sha1"),
        ),
        (
            "chai",
            REGISTRY_DEP_URL,
            {"version": "3.0.0", "integrity": "sha512-asdfgh"},
            JSDependency("chai", source=REGISTRY_DEP_URL, integrity=MOCK_INTEGRITY),
            None,
        ),
    ],
)
def test_convert_to_nexus_hosted(
    mock_process_dep,
    mock_pick_strongest_hash,
    mock_convert_sha,
    dep_name,
    dep_source,
    dep_info,
    expected_jsdep,
    convert_sha_call,
):
    if "integrity" in dep_info:
        mock_pick_strongest_hash.return_value = expected_jsdep.integrity
    elif convert_sha_call:
        mock_convert_sha.return_value = expected_jsdep.integrity

    rv = yarn._convert_to_nexus_hosted(dep_name, dep_source, dep_info)
    assert rv == {
        "version": mock_process_dep.return_value.version,
        "integrity": mock_process_dep.return_value.integrity,
    }

    mock_process_dep.assert_called_once_with(expected_jsdep)
    if "integrity" in dep_info:
        mock_pick_strongest_hash.assert_called_once_with(dep_info["integrity"])
    elif convert_sha_call:
        mock_convert_sha.assert_called_once_with(*convert_sha_call)


@mock.patch("cachito.workers.pkg_managers.yarn._convert_to_nexus_hosted")
@mock.patch("cachito.workers.pkg_managers.yarn.get_worker_config")
@pytest.mark.parametrize(
    "package_json, yarn_lock, allowlist, expected_deps, expected_replaced, expected_convert_calls",
    [
        # registry dependency
        (
            # package.json
            {**MINIMAL_PACKAGE_JSON, "dependencies": {"chai": "^1.0.0"}},
            # yarn.lock
            {
                "chai@^1.0.0": {
                    "version": "1.0.1",
                    "resolved": REGISTRY_DEP_URL,
                    "integrity": MOCK_INTEGRITY,
                },
            },
            # allowlist
            set(),
            # expected_deps
            [
                {
                    "dev": False,
                    "name": "chai",
                    "version": "1.0.1",
                    "version_in_nexus": None,
                    "bundled": False,
                    "type": "yarn",
                },
            ],
            # expected_replaced
            [],
            # expected_convert_calls
            [],
        ),
        # http dependency
        (
            # package.json
            {
                **MINIMAL_PACKAGE_JSON,
                "peerDependencies": {"fecha": HTTP_DEP_URL},
                "devDependencies": {"chai": "^1.0.0"},
            },
            # yarn.lock
            {
                f"fecha@{HTTP_DEP_URL}": {
                    "version": "2.0.0",
                    "resolved": HTTP_DEP_URL_WITH_CHECKSUM,
                },
                "chai@^1.0.0": {
                    "version": "1.0.1",
                    "resolved": REGISTRY_DEP_URL,
                    "integrity": MOCK_INTEGRITY,
                },
            },
            # allowlist
            set(),
            # expected_deps
            [
                {
                    "dev": False,
                    "name": "fecha",
                    "version": HTTP_DEP_URL_WITH_CHECKSUM,
                    "version_in_nexus": MOCK_NEXUS_VERSION,
                    "bundled": False,
                    "type": "yarn",
                },
                {
                    "dev": True,
                    "name": "chai",
                    "version": "1.0.1",
                    "version_in_nexus": None,
                    "bundled": False,
                    "type": "yarn",
                },
            ],
            # expected_replaced
            [f"fecha@{HTTP_DEP_URL}"],
            # expected_convert_calls
            [
                (
                    "fecha",
                    HTTP_DEP_URL_WITH_CHECKSUM,
                    {"version": "2.0.0", "resolved": HTTP_DEP_URL_WITH_CHECKSUM},
                ),
            ],
        ),
        # git dependency
        (
            # package.json
            {**MINIMAL_PACKAGE_JSON, "devDependencies": {"chai": "^1.0.0"}},
            # yarn.lock
            {
                "chai@^1.0.0": {
                    "version": "1.0.1",
                    "resolved": REGISTRY_DEP_URL,
                    "integrity": MOCK_INTEGRITY,
                    "dependencies": {"leftpad": GIT_DEP_URL},
                },
                f"leftpad@{GIT_DEP_URL}": {
                    "version": "3.0.0",
                    "resolved": GIT_DEP_URL_WITH_REF,
                },
            },
            # allowlist
            set(),
            # expected_deps
            [
                {
                    "dev": True,
                    "name": "chai",
                    "version": "1.0.1",
                    "version_in_nexus": None,
                    "bundled": False,
                    "type": "yarn",
                },
                {
                    "dev": True,
                    "name": "leftpad",
                    "version": GIT_DEP_URL_WITH_REF,
                    "version_in_nexus": MOCK_NEXUS_VERSION,
                    "bundled": False,
                    "type": "yarn",
                },
            ],
            # expected_replaced
            [f"leftpad@{GIT_DEP_URL}"],
            # expected_convert_calls
            [
                (
                    "leftpad",
                    GIT_DEP_URL_WITH_REF,
                    {"version": "3.0.0", "resolved": GIT_DEP_URL_WITH_REF},
                ),
            ],
        ),
        # allowlisted file dependency
        (
            # package.json
            {**MINIMAL_PACKAGE_JSON, "devDependencies": {"chai": "^1.0.0"}},
            # yarn.lock
            {
                "chai@^1.0.0": {
                    "version": "1.0.1",
                    "resolved": REGISTRY_DEP_URL,
                    "integrity": MOCK_INTEGRITY,
                    "dependencies": {"subpackage": "file:./subpath"},
                },
                "subpackage@file:./subpath": {"version": "4.0.0"},
            },
            # allowlist
            {"subpackage"},
            # expected_deps
            [
                {
                    "dev": True,
                    "name": "chai",
                    "version": "1.0.1",
                    "version_in_nexus": None,
                    "bundled": False,
                    "type": "yarn",
                },
                {
                    "dev": True,
                    "name": "subpackage",
                    "version": "file:subpath",
                    "version_in_nexus": None,
                    "bundled": False,
                    "type": "yarn",
                },
            ],
            # expected_replaced
            [],
            # expected_convert_calls
            [],
        ),
        # one http and one git dependency
        (
            # package.json
            {
                **MINIMAL_PACKAGE_JSON,
                "optionalDependencies": {"chai": "^1.0.0"},
                "devDependencies": {"fecha": HTTP_DEP_URL, "leftpad": GIT_DEP_URL},
            },
            # yarn.lock
            {
                "chai@^1.0.0": {
                    "version": "1.0.1",
                    "resolved": REGISTRY_DEP_URL,
                    "integrity": MOCK_INTEGRITY,
                    "dependencies": {"leftpad": GIT_DEP_URL},
                },
                f"fecha@{HTTP_DEP_URL}": {
                    "version": "2.0.0",
                    "resolved": HTTP_DEP_URL_WITH_CHECKSUM,
                },
                f"leftpad@{GIT_DEP_URL}": {
                    "version": "3.0.0",
                    "resolved": GIT_DEP_URL_WITH_REF,
                },
            },
            # allowlist
            set(),
            # expected_deps
            [
                {
                    "dev": False,
                    "name": "chai",
                    "version": "1.0.1",
                    "version_in_nexus": None,
                    "bundled": False,
                    "type": "yarn",
                },
                {
                    "dev": True,
                    "name": "fecha",
                    "version": HTTP_DEP_URL_WITH_CHECKSUM,
                    "version_in_nexus": MOCK_NEXUS_VERSION,
                    "bundled": False,
                    "type": "yarn",
                },
                {
                    "dev": False,
                    "name": "leftpad",
                    "version": GIT_DEP_URL_WITH_REF,
                    "version_in_nexus": MOCK_NEXUS_VERSION,
                    "bundled": False,
                    "type": "yarn",
                },
            ],
            # expected_replaced
            [f"fecha@{HTTP_DEP_URL}", f"leftpad@{GIT_DEP_URL}"],
            # expected_convert_calls
            [
                (
                    "fecha",
                    HTTP_DEP_URL_WITH_CHECKSUM,
                    {"version": "2.0.0", "resolved": HTTP_DEP_URL_WITH_CHECKSUM},
                ),
                (
                    "leftpad",
                    GIT_DEP_URL_WITH_REF,
                    {"version": "3.0.0", "resolved": GIT_DEP_URL_WITH_REF},
                ),
            ],
        ),
    ],
)
def test_get_package_and_deps(
    mock_get_config: mock.Mock,
    mock_convert_hosted: mock.Mock,
    package_json: dict[str, Any],
    yarn_lock: dict[str, Any],
    allowlist: set[str],
    expected_deps: list[dict[str, Any]],
    expected_replaced: list[str],
    expected_convert_calls: list[tuple[Any, ...]],
    tmp_path: Path,
) -> None:
    tmp_path.joinpath("package.json").write_text(json.dumps(package_json))
    tmp_path.joinpath("yarn.lock").write_text(yarn_lock_to_str(yarn_lock))

    def mock_nexus_replacement_getitem(key):
        assert key == "version"
        return MOCK_NEXUS_VERSION

    mock_convert_hosted.return_value.__getitem__.side_effect = mock_nexus_replacement_getitem
    mock_get_config.return_value.cachito_yarn_file_deps_allowlist = {
        "hitchhikers-guide": list(allowlist)
    }

    info = yarn._get_package_and_deps(tmp_path)

    assert info["package"] == {"name": "hitchhikers-guide", "version": "42.0.0", "type": "yarn"}
    assert info["deps"] == expected_deps
    assert info["package.json"] == package_json
    assert info["lock_file"] == yarn_lock

    for dep_identifier in expected_replaced:
        assert dep_identifier in info["nexus_replacements"]
        assert info["nexus_replacements"][dep_identifier] == mock_convert_hosted.return_value
    assert len(info["nexus_replacements"]) == len(expected_replaced)

    mock_convert_hosted.assert_has_calls(
        [mock.call(*call) for call in expected_convert_calls],
        # we are also mocking out a __getitem__ call which messes with the order
        any_order=True,
    )


@pytest.mark.parametrize(
    "file_tree, expected_deps",
    [
        # workspace not included in package.json dependencies
        (
            {
                "package.json": json.dumps(
                    {
                        **MINIMAL_PACKAGE_JSON,
                        "workspaces": ["subpath"],
                    }
                ),
                # yarn.lock does not include workspaces if they're not declared as dependencies
                "yarn.lock": yarn_lock_to_str({}),
                "subpath": {
                    "package.json": json.dumps({"name": "subpackage", "version": "4.0.0"}),
                },
            },
            [
                {
                    "dev": False,
                    "name": "subpackage",
                    "version": "file:subpath",
                    "version_in_nexus": None,
                    "bundled": False,
                    "type": "yarn",
                },
            ],
        ),
        # workspace included in package.json dependencies, but as a version
        (
            {
                "package.json": json.dumps(
                    {
                        **MINIMAL_PACKAGE_JSON,
                        "workspaces": ["subpath"],
                        "dependencies": {"subpackage": "^4.0.0"},
                    }
                ),
                # yarn.lock does not include workspaces if they're specified as versions
                "yarn.lock": yarn_lock_to_str({}),
                "subpath": {
                    "package.json": json.dumps({"name": "subpackage", "version": "4.0.0"}),
                },
            },
            [
                {
                    "dev": False,
                    "name": "subpackage",
                    "version": "file:subpath",
                    "version_in_nexus": None,
                    "bundled": False,
                    "type": "yarn",
                },
            ],
        ),
        # workspace included in package.json as a file: dependency
        (
            {
                "package.json": json.dumps(
                    {
                        **MINIMAL_PACKAGE_JSON,
                        "workspaces": ["subpath"],
                        "dependencies": {"subpackage": "file:./subpath"},
                    }
                ),
                "yarn.lock": yarn_lock_to_str(
                    {
                        "subpackage@file:./subpath": {"version": "4.0.0"},
                    },
                ),
                "subpath": {
                    "package.json": json.dumps({"name": "subpackage", "version": "4.0.0"}),
                },
            },
            [
                {
                    "dev": False,
                    "name": "subpackage",
                    "version": "file:subpath",
                    "version_in_nexus": None,
                    "bundled": False,
                    "type": "yarn",
                },
            ],
        ),
        # workspace included in package.json as a file: dependency (alt. workspaces format)
        (
            {
                "package.json": json.dumps(
                    {
                        **MINIMAL_PACKAGE_JSON,
                        "workspaces": {
                            "packages": ["subpath"],
                        },
                        "dependencies": {"subpackage": "file:./subpath"},
                    }
                ),
                "yarn.lock": yarn_lock_to_str(
                    {
                        "subpackage@file:./subpath": {"version": "4.0.0"},
                    },
                ),
                "subpath": {
                    "package.json": json.dumps({"name": "subpackage", "version": "4.0.0"}),
                },
            },
            [
                {
                    "dev": False,
                    "name": "subpackage",
                    "version": "file:subpath",
                    "version_in_nexus": None,
                    "bundled": False,
                    "type": "yarn",
                },
            ],
        ),
        # multiple workspaces specified via glob pattern
        (
            {
                "package.json": json.dumps(
                    {
                        **MINIMAL_PACKAGE_JSON,
                        "workspaces": ["packages/*"],
                    }
                ),
                "yarn.lock": yarn_lock_to_str({}),
                "packages": {
                    "eggs": {
                        "package.json": json.dumps({"name": "eggs", "version": "1.2.3"}),
                    },
                    "spam": {
                        "package.json": json.dumps({"name": "spam", "version": "4.5.6"}),
                    },
                    "not_a_workspace": {},
                },
            },
            [
                {
                    "dev": False,
                    "name": "eggs",
                    "version": "file:packages/eggs",
                    "version_in_nexus": None,
                    "bundled": False,
                    "type": "yarn",
                },
                {
                    "dev": False,
                    "name": "spam",
                    "version": "file:packages/spam",
                    "version_in_nexus": None,
                    "bundled": False,
                    "type": "yarn",
                },
            ],
        ),
        # dev-dependency identification when workspaces are involved (and not declared as deps)
        (
            {
                "package.json": json.dumps(
                    {
                        **MINIMAL_PACKAGE_JSON,
                        "workspaces": ["packages/*"],
                    }
                ),
                "yarn.lock": yarn_lock_to_str(
                    {
                        "chai@^1.0.0": {
                            "version": "1.0.1",
                            "resolved": REGISTRY_DEP_URL,
                            "integrity": MOCK_INTEGRITY,
                            "dependencies": {"leftpad": "^3.0.0"},
                        },
                        "fecha@^2.0.0": {
                            "version": "2.0.2",
                            "resolved": "https://registry.yarnpkg.com/fecha/-/fecha-2.0.2.tgz",
                            "integrity": MOCK_INTEGRITY,
                        },
                        "leftpad@^3.0.0": {
                            "version": "3.0.3",
                            "resolved": "https://registry.yarnpkg.com/leftpad/-/leftpad-3.0.3.tgz",
                            "integrity": MOCK_INTEGRITY,
                        },
                    },
                ),
                "packages": {
                    "eggs": {
                        "package.json": json.dumps(
                            {
                                "name": "eggs",
                                "version": "1.2.3",
                                "dependencies": {
                                    "chai": "^1.0.0",
                                    "spam": "^4.0.0",  # this is the other workspace
                                },
                            }
                        ),
                    },
                    "spam": {
                        "package.json": json.dumps(
                            {
                                "name": "spam",
                                "version": "4.5.6",
                                "devDependencies": {
                                    "chai": "^1.0.0",
                                    "fecha": "^2.0.0",
                                },
                            }
                        ),
                    },
                },
            },
            [
                {
                    # is non-dev in at least one workspace
                    "dev": False,
                    "name": "chai",
                    "version": "1.0.1",
                    "version_in_nexus": None,
                    "bundled": False,
                    "type": "yarn",
                },
                {
                    # is not non-dev in any workspace
                    "dev": True,
                    "name": "fecha",
                    "version": "2.0.2",
                    "version_in_nexus": None,
                    "bundled": False,
                    "type": "yarn",
                },
                {
                    # is a dependency of chai (which is non-dev)
                    "dev": False,
                    "name": "leftpad",
                    "version": "3.0.3",
                    "version_in_nexus": None,
                    "bundled": False,
                    "type": "yarn",
                },
                {
                    "dev": False,
                    "name": "eggs",
                    "version": "file:packages/eggs",
                    "version_in_nexus": None,
                    "bundled": False,
                    "type": "yarn",
                },
                {
                    "dev": False,
                    "name": "spam",
                    "version": "file:packages/spam",
                    "version_in_nexus": None,
                    "bundled": False,
                    "type": "yarn",
                },
            ],
        ),
    ],
)
def test_get_package_and_deps_with_workspaces(
    file_tree: dict[str, Any], expected_deps: list[dict[str, Any]], tmp_path: Path
) -> None:
    write_file_tree(file_tree, tmp_path)
    info = yarn._get_package_and_deps(tmp_path)
    assert info["deps"] == expected_deps


def test_get_package_and_deps_disallowed_file_dep(tmp_path: Path) -> None:
    tmp_path.joinpath("package.json").write_text(json.dumps(MINIMAL_PACKAGE_JSON))
    tmp_path.joinpath("yarn.lock").write_text(
        yarn_lock_to_str({"subpackage@file:./subpath": {"version": "1.0.0"}})
    )

    err_msg = "subpackage@file:subpath is a 'file:' dependency."

    with pytest.raises(InvalidRepoStructure, match=err_msg):
        yarn._get_package_and_deps(tmp_path)


@pytest.mark.parametrize(
    "file_tree, expected_err_msg",
    [
        (
            {
                "package.json": json.dumps({**MINIMAL_PACKAGE_JSON, "workspaces": ["../sibling"]}),
                "yarn.lock": yarn_lock_to_str({}),
                "..": {
                    "sibling": {
                        "package.json": json.dumps({"name": "sibling", "version": "1.0.0"}),
                    },
                },
            },
            "Workspace path leads outside the package root: ../sibling/package.json",
        ),
        (
            {
                "package.json": json.dumps({**MINIMAL_PACKAGE_JSON, "workspaces": ["child"]}),
                "yarn.lock": yarn_lock_to_str({}),
                "child": {"package.json": Symlink("../..")},
            },
            "Workspace path leads outside the package root: child/package.json",
        ),
    ],
)
def test_get_package_and_deps_nonlocal_workspace(
    file_tree: dict[str, Any], expected_err_msg: str, tmp_path: Path
) -> None:
    package_path = tmp_path / "package"
    package_path.mkdir()
    write_file_tree(file_tree, package_path, exist_ok=True)

    with pytest.raises(InvalidRepoStructure, match=expected_err_msg):
        yarn._get_package_and_deps(package_path)


@pytest.mark.parametrize("components_exist", [True, False])
@mock.patch("cachito.workers.pkg_managers.yarn.get_yarn_component_info_from_non_hosted_nexus")
def test_set_proxy_resolved_urls(mock_get_component, components_exist):
    yarn_lock = {
        f"fecha@{HTTP_DEP_URL}": {
            "version": HTTP_DEP_NEXUS_VERSION,
            "resolved": HTTP_DEP_NEXUS_URL,  # hosted Nexus url
        },
        f"leftpad@{GIT_DEP_URL}": {
            "version": GIT_DEP_NEXUS_VERSION,
            "resolved": GIT_DEP_NEXUS_URL,  # hosted Nexus url
        },
        "chai@^4.2.0": {
            "version": "4.2.0",
            "resolved": REGISTRY_DEP_URL,  # url in official registry
        },
        "subpackage@file:./subpackage": {"version": "1.0.0"},
    }

    proxy_url_1 = "http://nexus.example.org/repository/cachito-yarn-42/fecha.tar.gz"
    proxy_url_2 = "http://nexus.example.org/repository/cachito-yarn-42/leftpad.tar.gz"
    proxy_url_3 = "http://nexus.example.org/repository/cachito-yarn-42/chai.tar.gz"

    component_1 = {"assets": [{"downloadUrl": proxy_url_1}]}
    component_2 = {"assets": [{"downloadUrl": proxy_url_2}]}
    component_3 = {"assets": [{"downloadUrl": proxy_url_3}]}

    if components_exist:
        mock_get_component.side_effect = [component_1, component_2, component_3]
        expected_calls = [
            mock.call("fecha", HTTP_DEP_NEXUS_VERSION, "cachito-yarn-42", max_attempts=5),
            mock.call("leftpad", GIT_DEP_NEXUS_VERSION, "cachito-yarn-42", max_attempts=5),
            mock.call("chai", "4.2.0", "cachito-yarn-42", max_attempts=5),
        ]
    else:
        mock_get_component.side_effect = [None, component_2]
        expected_calls = [
            mock.call("fecha", HTTP_DEP_NEXUS_VERSION, "cachito-yarn-42", max_attempts=5)
        ]

    if components_exist:
        assert yarn._set_proxy_resolved_urls(yarn_lock, "cachito-yarn-42") is True
        assert yarn_lock[f"fecha@{HTTP_DEP_URL}"]["resolved"] == proxy_url_1
        assert yarn_lock[f"leftpad@{GIT_DEP_URL}"]["resolved"] == proxy_url_2
        assert yarn_lock["chai@^4.2.0"]["resolved"] == proxy_url_3
    else:
        err_msg = (
            f"The dependency fecha@{HTTP_DEP_NEXUS_VERSION} was uploaded to the Nexus hosted "
            "repository but is not available in cachito-yarn-42"
        )
        with pytest.raises(NexusError, match=err_msg):
            yarn._set_proxy_resolved_urls(yarn_lock, "cachito-yarn-42")

    mock_get_component.assert_has_calls(expected_calls)
    assert mock_get_component.call_count == len(expected_calls)


def test_set_proxy_resolved_urls_no_urls():
    yarn_lock = {
        "foo@file:./foo": {"version": "1.0.0"},
        "bar@file:./bar": {"version": "2.0.0"},
        "baz@file:./baz": {"version": "3.0.0"},
    }
    assert yarn._set_proxy_resolved_urls(yarn_lock, "cachito-yarn-1") is False


@pytest.mark.parametrize(
    "replacements, original, replaced",
    [
        (
            # no replacements
            {},
            EXAMPLE_PACKAGE_JSON,
            None,
        ),
        (
            # http dependency is replaced
            {f"fecha@{HTTP_DEP_URL}": {"version": MOCK_NEXUS_VERSION}},
            EXAMPLE_PACKAGE_JSON,
            replaced_example_packjson([("dependencies", "fecha", MOCK_NEXUS_VERSION)]),
        ),
        (
            # git devDependency is replaced
            {f"leftpad@{GIT_DEP_URL}": {"version": MOCK_NEXUS_VERSION}},
            EXAMPLE_PACKAGE_JSON,
            replaced_example_packjson([("devDependencies", "leftpad", MOCK_NEXUS_VERSION)]),
        ),
        (
            # replacement does not apply (url is different)
            {f"fecha@{HTTP_DEP_URL}#foobar": {"version": MOCK_NEXUS_VERSION}},
            EXAMPLE_PACKAGE_JSON,
            None,
        ),
        (
            # replacement is a list, there is a match
            {f"fecha@^1.0.0, fecha@{HTTP_DEP_URL}": {"version": MOCK_NEXUS_VERSION}},
            EXAMPLE_PACKAGE_JSON,
            replaced_example_packjson([("dependencies", "fecha", MOCK_NEXUS_VERSION)]),
        ),
        (
            # replacement is a list, there is no match
            {f"fecha@^1.0.0, fecha@{HTTP_DEP_URL}#foobar": {"version": MOCK_NEXUS_VERSION}},
            EXAMPLE_PACKAGE_JSON,
            None,
        ),
        (
            # all external dependencies are replaced
            {
                f"fecha@{HTTP_DEP_URL}": {"version": "1.0.0-external"},
                f"leftpad@{GIT_DEP_URL}": {"version": "2.0.0-external"},
                f"pathval@{OPTIONAL_DEP_URL}": {"version": "3.0.0-external"},
                f"deep-eql@{PEER_DEP_URL}": {"version": "4.0.0-external"},
            },
            EXAMPLE_PACKAGE_JSON,
            replaced_example_packjson(
                [
                    ("dependencies", "fecha", "1.0.0-external"),
                    ("devDependencies", "leftpad", "2.0.0-external"),
                    ("optionalDependencies", "pathval", "3.0.0-external"),
                    ("peerDependencies", "deep-eql", "4.0.0-external"),
                ]
            ),
        ),
    ],
)
def test_replace_deps_in_package_json(replacements, original, replaced):
    assert yarn._replace_deps_in_package_json(original, replacements) == replaced


def test_replace_deps_in_yarn_lock():
    original = {
        "chai@^4.2.0": {
            "version": "4.2.0",
            "resolved": REGISTRY_DEP_URL,
            "integrity": MOCK_INTEGRITY,
        },
        f"fecha@{HTTP_DEP_URL}": {"version": "1.0.0", "resolved": HTTP_DEP_URL_WITH_CHECKSUM},
        f"leftpad@{GIT_DEP_URL}": {"version": "2.0.0", "resolved": GIT_DEP_URL_WITH_REF},
    }

    http_dep_nexus_integrity = "sha512-placeholder-1"
    git_dep_nexus_integrity = "sha512-placeholder-2"

    replacements = {
        f"fecha@{HTTP_DEP_URL}": {
            "version": HTTP_DEP_NEXUS_VERSION,
            "resolved": HTTP_DEP_NEXUS_URL,
            "integrity": http_dep_nexus_integrity,
        },
        f"leftpad@{GIT_DEP_URL}": {
            "version": GIT_DEP_NEXUS_VERSION,
            "resolved": GIT_DEP_NEXUS_URL,
            "integrity": git_dep_nexus_integrity,
        },
    }

    replaced = yarn._replace_deps_in_yarn_lock(original, replacements)
    assert replaced == {
        "chai@^4.2.0": {
            "version": "4.2.0",
            "resolved": REGISTRY_DEP_URL,
            "integrity": MOCK_INTEGRITY,
        },
        f"fecha@{HTTP_DEP_NEXUS_VERSION}": {
            "version": HTTP_DEP_NEXUS_VERSION,
            "resolved": HTTP_DEP_NEXUS_URL,
            "integrity": http_dep_nexus_integrity,
        },
        f"leftpad@{GIT_DEP_NEXUS_VERSION}": {
            "version": GIT_DEP_NEXUS_VERSION,
            "resolved": GIT_DEP_NEXUS_URL,
            "integrity": git_dep_nexus_integrity,
        },
    }


def test_replace_deps_in_yarn_lock_dependencies():
    original = {
        "foo@not-external-1": {"version": "not-external-1", "dependencies": {"bar": "external-2"}},
        "bar@external-1, bar@external-2": {
            "version": "external-1",
            "dependencies": {"baz": "external-3"},
        },
        "baz@external-3": {"version": "external-3"},
    }

    nexus_replacements = {
        "bar@external-1, bar@external-2": {"version": "external-in-nexus-1"},
        "baz@external-3": {"version": "external-in-nexus-2"},
    }

    replaced = yarn._replace_deps_in_yarn_lock(original, nexus_replacements)
    assert replaced == {
        "foo@not-external-1": {
            "version": "not-external-1",
            "dependencies": {"bar": "external-in-nexus-1"},
        },
        "bar@external-in-nexus-1": {
            "version": "external-in-nexus-1",
            "dependencies": {"baz": "external-in-nexus-2"},
        },
        "baz@external-in-nexus-2": {"version": "external-in-nexus-2"},
    }


@pytest.mark.parametrize("have_nexus_replacements", [True, False])
@pytest.mark.parametrize("any_urls_in_yarn_lock", [True, False])
@mock.patch("cachito.workers.pkg_managers.yarn._get_package_and_deps")
@mock.patch("cachito.workers.pkg_managers.yarn.get_yarn_proxy_repo_url")
@mock.patch("cachito.workers.pkg_managers.yarn.download_dependencies")
@mock.patch("cachito.workers.pkg_managers.yarn.get_yarn_proxy_repo_name")
@mock.patch("cachito.workers.pkg_managers.yarn._set_proxy_resolved_urls")
@mock.patch("cachito.workers.pkg_managers.yarn._replace_deps_in_package_json")
@mock.patch("cachito.workers.pkg_managers.yarn._replace_deps_in_yarn_lock")
@mock.patch("cachito.workers.config.get_worker_config")
def test_resolve_yarn(
    get_worker_config,
    mock_replace_yarnlock,
    mock_replace_packjson,
    mock_set_proxy_urls,
    mock_get_repo_name,
    mock_download_deps,
    mock_get_repo_url,
    mock_get_package_and_deps,
    have_nexus_replacements,
    any_urls_in_yarn_lock,
    tmpdir,
):
    get_worker_config.return_value = mock.Mock(cachito_bundles_dir=str(tmpdir))
    n_pop_calls = 0

    def dict_pop_mocker():
        expected_keys = ["version_in_nexus", "bundled"]

        def mock_pop(key):
            nonlocal n_pop_calls
            n_pop_calls += 1

            if expected_keys:
                popped_key = expected_keys.pop()
                assert key == popped_key
            else:
                assert False

        return mock_pop

    mock_package = mock.Mock()
    mock_deps = [mock.Mock()]
    for mock_dep in mock_deps:
        mock_dep.pop.side_effect = dict_pop_mocker()
    mock_package_json = mock.Mock()
    mock_yarn_lock = mock.Mock()
    mock_nexus_replacements = {"foo": {}} if have_nexus_replacements else {}

    mock_get_package_and_deps.return_value = {
        "package": mock_package,
        "deps": mock_deps,
        "package.json": mock_package_json,
        "lock_file": mock_yarn_lock,
        "nexus_replacements": mock_nexus_replacements,
    }

    if any_urls_in_yarn_lock:
        mock_set_proxy_urls.return_value = True
        expect_yarn_lock = mock_replace_yarnlock.return_value
    else:
        mock_set_proxy_urls.return_value = False
        expect_yarn_lock = None

    rv = yarn.resolve_yarn("/some/path", {"id": 1}, skip_deps={"foobar"})
    assert rv == {
        "package": mock_package,
        "deps": mock_deps,
        "downloaded_deps": mock_download_deps.return_value,
        "package.json": mock_replace_packjson.return_value,
        "lock_file": expect_yarn_lock,
    }

    mock_get_package_and_deps.assert_called_once_with(Path("/some/path"))
    mock_get_repo_url.assert_called_once_with(1)
    mock_download_deps.assert_called_once_with(
        RequestBundleDir(1).yarn_deps_dir,
        mock_deps,
        mock_get_repo_url.return_value,
        skip_deps={"foobar"},
        pkg_manager="yarn",
    )
    assert n_pop_calls == len(mock_deps) * 2

    if have_nexus_replacements:
        mock_get_repo_name.assert_called_once_with(1)
        mock_replace_packjson.assert_called_once_with(mock_package_json, mock_nexus_replacements)
        mock_replace_yarnlock.assert_called_once_with(mock_yarn_lock, mock_nexus_replacements)

    mock_set_proxy_urls.assert_called_once_with(
        mock_replace_yarnlock.return_value, mock_get_repo_name.return_value
    )
