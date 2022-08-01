import copy
from pathlib import Path
from unittest import mock

import pytest

from cachito.errors import NexusError, UnsupportedFeature
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers import yarn
from cachito.workers.pkg_managers.general_js import JSDependency

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

EXAMPLE_PACKAGE_JSON = {
    "name": "foo",
    "version": "1.0.0",
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


def test_get_npm_proxy_repo_name():
    assert yarn.get_yarn_proxy_repo_name(3) == "cachito-yarn-3"


def test_get_npm_proxy_repo_url():
    assert yarn.get_yarn_proxy_repo_url(3).endswith("/repository/cachito-yarn-3/")


def test_get_npm_proxy_username():
    assert yarn.get_yarn_proxy_repo_username(3) == "cachito-yarn-3"


@pytest.mark.parametrize(
    "url, expected",
    [
        (REGISTRY_DEP_URL, True),
        (HTTP_DEP_URL, False),
        ("https://registry.npmjs.org/chai/-/chai-4.2.0.tgz", True),
    ],
)
def test_is_from_npm_registry(url, expected):
    assert yarn._is_from_npm_registry(url) == expected


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
@pytest.mark.parametrize(
    "package_json, yarn_lock, allowlist, expected_deps, expected_replaced, expected_convert_calls",
    [
        # registry dependency
        (
            # package_json
            {"dependencies": {"chai": "^1.0.0"}},
            # yarn_lock
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
            # package_json
            {"peerDependencies": {"fecha": HTTP_DEP_URL}, "devDependencies": {"chai": "^1.0.0"}},
            # yarn_lock
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
            # package_json
            {"devDependencies": {"chai": "^1.0.0"}},
            # yarn_lock
            {
                "chai@^1.0.0": {
                    "version": "1.0.1",
                    "resolved": REGISTRY_DEP_URL,
                    "integrity": MOCK_INTEGRITY,
                    "dependencies": {"leftpad": GIT_DEP_URL},
                },
                f"leftpad@{GIT_DEP_URL}": {"version": "3.0.0", "resolved": GIT_DEP_URL_WITH_REF},
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
            # package_json
            {"devDependencies": {"chai": "^1.0.0"}},
            # yarn_lock
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
            {"subpath"},
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
                    "version": "file:./subpath",
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
            # package_json
            {
                "optionalDependencies": {"chai": "^1.0.0"},
                "devDependencies": {"fecha": HTTP_DEP_URL, "leftpad": GIT_DEP_URL},
            },
            # yarn_lock
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
                f"leftpad@{GIT_DEP_URL}": {"version": "3.0.0", "resolved": GIT_DEP_URL_WITH_REF},
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
def test_get_deps(
    mock_convert_hosted,
    package_json,
    yarn_lock,
    allowlist,
    expected_deps,
    expected_replaced,
    expected_convert_calls,
):
    def mock_nexus_replacement_getitem(key):
        assert key == "version"
        return MOCK_NEXUS_VERSION

    mock_convert_hosted.return_value.__getitem__.side_effect = mock_nexus_replacement_getitem

    deps, replacements = yarn._get_deps(package_json, yarn_lock, allowlist)

    assert deps == expected_deps

    for dep_identifier in expected_replaced:
        assert dep_identifier in replacements
        assert replacements[dep_identifier] == mock_convert_hosted.return_value
    assert len(replacements) == len(expected_replaced)

    mock_convert_hosted.assert_has_calls(
        [mock.call(*call) for call in expected_convert_calls],
        # we are also mocking out a __getitem__ call which messes with the order
        any_order=True,
    )


@mock.patch("cachito.workers.pkg_managers.yarn._convert_to_nexus_hosted")
def test_get_deps_disallowed_file_dep(mock_convert_hosted):
    package_json = {}
    yarn_lock = {
        "subpackage@file:./subpath": {"version": "1.0.0"},
    }
    allowlist = set()

    err_msg = "The dependency ./subpath is hosted in an unsupported location"
    mock_convert_hosted.side_effect = [UnsupportedFeature(err_msg)]

    with pytest.raises(UnsupportedFeature, match=err_msg):
        yarn._get_deps(package_json, yarn_lock, allowlist)


@mock.patch.object(yarn.pyarn.lockfile.Lockfile, "from_file")
@mock.patch("cachito.workers.pkg_managers.yarn.get_worker_config")
@mock.patch("cachito.workers.pkg_managers.yarn._get_deps")
def test_get_package_and_deps(
    mock_get_deps,
    mock_get_config,
    mock_lockfile_fromfile,
    tmp_path,
):
    packjson_path = tmp_path / "package-lock.json"
    packjson_path.write_text('{"name": "foo", "version": "1.0.0"}')

    yarnlock_path = tmp_path / "yarn.lock"

    mock_get_config.return_value.cachito_yarn_file_deps_allowlist = {"foo": ["bar"]}

    mock_deps = mock.Mock()
    mock_replacements = {"some-dep@^1.0.0": mock.Mock()}

    mock_get_deps.return_value = (mock_deps, mock_replacements)

    rv = yarn._get_package_and_deps(packjson_path, yarnlock_path)
    assert rv == {
        "package": {"name": "foo", "version": "1.0.0", "type": "yarn"},
        "deps": mock_deps,
        "package.json": {"name": "foo", "version": "1.0.0"},
        "lock_file": mock_lockfile_fromfile.return_value.data,
        "nexus_replacements": mock_replacements,
    }

    mock_lockfile_fromfile.assert_called_once_with(str(yarnlock_path))
    mock_get_config.asssert_called_once()
    expected_lockfile = mock_lockfile_fromfile.return_value.data
    mock_get_deps.assert_called_once_with(rv["package.json"], expected_lockfile, {"bar"})


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
                assert key == expected_keys.pop()
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

    mock_get_package_and_deps.assert_called_once_with(
        Path("/some/path/package.json"), Path("/some/path/yarn.lock")
    )
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
