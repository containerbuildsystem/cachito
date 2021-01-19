import copy
from pathlib import Path
from unittest import mock

import pytest

from cachito.errors import CachitoError
from cachito.workers.pkg_managers import yarn
from cachito.workers.pkg_managers.general_js import JSDependency

REGISTRY_DEP_URL = "https://registry.yarnpkg.com/chai/-/chai-4.2.0.tgz"

HTTP_DEP_URL = "https://example.org/fecha.tar.gz"
HTTP_DEP_URL_WITH_CHECKSUM = f"{HTTP_DEP_URL}#123456"

GIT_DEP_URL = "git+https://github.com/example/leftpad.git"
GIT_DEP_URL_WITH_REF = f"{GIT_DEP_URL}#abcdef"

MOCK_INTEGRITY = "sha1-abcdefghijklmnopqrstuvwxyzo="
MOCK_NEXUS_VERSION = "1.0.0-external"

EXAMPLE_PACKAGE_JSON = {
    "name": "foo",
    "version": "1.0.0",
    "dependencies": {"chai": "^4.2.0", "fecha": HTTP_DEP_URL},
    "devDependencies": {"leftpad": GIT_DEP_URL},
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
                "fecha", source="https://example.org/fecha.tar.gz#123456", integrity=MOCK_INTEGRITY,
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
        **dep_info,
        "version": mock_process_dep.return_value.version,
        "resolved": mock_process_dep.return_value.source,
        "integrity": mock_process_dep.return_value.integrity,
    }
    assert rv is not dep_info  # make sure original dict was copied

    mock_process_dep.assert_called_once_with(expected_jsdep)
    if "integrity" in dep_info:
        mock_pick_strongest_hash.assert_called_once_with(dep_info["integrity"])
    elif convert_sha_call:
        mock_convert_sha.assert_called_once_with(*convert_sha_call)


@mock.patch("cachito.workers.pkg_managers.yarn._convert_to_nexus_hosted")
@pytest.mark.parametrize(
    "yarn_lock, allowlist, expected_deps, expected_replaced, expected_convert_calls",
    [
        # registry dependency
        (
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
            # yarn_lock
            {
                f"fecha@{HTTP_DEP_URL}": {
                    "version": "2.0.0",
                    "resolved": HTTP_DEP_URL_WITH_CHECKSUM,
                },
            },
            # allowlist
            set(),
            # expected_deps
            [
                {
                    "name": "fecha",
                    "version": HTTP_DEP_URL_WITH_CHECKSUM,
                    "version_in_nexus": MOCK_NEXUS_VERSION,
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
            # yarn_lock
            {f"leftpad@{GIT_DEP_URL}": {"version": "3.0.0", "resolved": GIT_DEP_URL_WITH_REF}},
            # allowlist
            set(),
            # expected_deps
            [
                {
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
            # yarn_lock
            {f"subpackage@file:./subpath": {"version": "4.0.0"}},
            # allowlist
            {"subpath"},
            # expected_deps
            [
                {
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
            # yarn_lock
            {
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
                    "name": "fecha",
                    "version": HTTP_DEP_URL_WITH_CHECKSUM,
                    "version_in_nexus": MOCK_NEXUS_VERSION,
                    "bundled": False,
                    "type": "yarn",
                },
                {
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

    deps, replacements = yarn._get_deps(yarn_lock, allowlist)

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
    yarn_lock = {
        f"subpackage@file:./subpath": {"version": "1.0.0"},
    }
    allowlist = set()

    err_msg = f"The dependency ./subpath is hosted in an unsupported location"
    mock_convert_hosted.side_effect = [CachitoError(err_msg)]

    with pytest.raises(CachitoError, match=err_msg):
        yarn._get_deps(yarn_lock, allowlist)


@mock.patch.object(yarn.pyarn.lockfile.Lockfile, "from_file")
@mock.patch("cachito.workers.pkg_managers.yarn.get_worker_config")
@mock.patch("cachito.workers.pkg_managers.yarn._get_deps")
@mock.patch("cachito.workers.pkg_managers.yarn._replace_deps_in_package_json")
@mock.patch("cachito.workers.pkg_managers.yarn._replace_deps_in_yarn_lock")
@pytest.mark.parametrize("have_nexus_replacements", [True, False])
def test_get_package_and_deps(
    mock_replace_yarnlock,
    mock_replace_packjson,
    mock_get_deps,
    mock_get_config,
    mock_lockfile_fromfile,
    have_nexus_replacements,
    tmp_path,
):
    packjson_path = tmp_path / "package-lock.json"
    packjson_path.write_text('{"name": "foo", "version": "1.0.0"}')

    yarnlock_path = tmp_path / "yarn.lock"

    mock_get_config.return_value.cachito_yarn_file_deps_allowlist = {"foo": ["bar"]}

    mock_deps = mock.Mock()
    if have_nexus_replacements:
        mock_replacements = {"some-dep@^1.0.0": mock.Mock()}
    else:
        mock_replacements = {}

    mock_get_deps.return_value = (mock_deps, mock_replacements)

    rv = yarn._get_package_and_deps(packjson_path, yarnlock_path)
    assert rv == {
        "package": {"name": "foo", "version": "1.0.0", "type": "yarn"},
        "deps": mock_deps,
        "package.json": mock_replace_packjson.return_value if have_nexus_replacements else None,
        "lock_file": mock_replace_yarnlock.return_value if have_nexus_replacements else None,
    }

    mock_lockfile_fromfile.assert_called_once_with(str(yarnlock_path))
    mock_get_config.asssert_called_once()
    expected_lockfile = mock_lockfile_fromfile.return_value.data
    mock_get_deps.assert_called_once_with(expected_lockfile, {"bar"})
    if have_nexus_replacements:
        mock_replace_packjson.assert_called_once_with(
            {"name": "foo", "version": "1.0.0"}, mock_replacements
        )
        mock_replace_yarnlock.assert_called_once_with(expected_lockfile, mock_replacements)
    else:
        mock_replace_packjson.assert_not_called()
        mock_replace_yarnlock.assert_not_called()


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
            # both external dependencies are replaced
            {
                f"fecha@{HTTP_DEP_URL}": {"version": "1.0.0-external"},
                f"leftpad@{GIT_DEP_URL}": {"version": "2.0.0-external"},
            },
            EXAMPLE_PACKAGE_JSON,
            replaced_example_packjson(
                [
                    ("dependencies", "fecha", "1.0.0-external"),
                    ("devDependencies", "leftpad", "2.0.0-external"),
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

    http_dep_nexus_url = "http://nexus.example.org/repository/js/fecha.tar.gz"
    http_dep_nexus_version = "1.0.0-external"
    http_dep_nexus_integrity = "sha512-placeholder-1"

    git_dep_nexus_url = "http://nexus.example.org/repository/js/leftpad.tar.gz"
    git_dep_nexus_version = "2.0.0-external"
    git_dep_nexus_integrity = "sha512-placeholder-2"

    replacements = {
        f"fecha@{HTTP_DEP_URL}": {
            "version": http_dep_nexus_version,
            "resolved": http_dep_nexus_url,
            "integrity": http_dep_nexus_integrity,
        },
        f"leftpad@{GIT_DEP_URL}": {
            "version": git_dep_nexus_version,
            "resolved": git_dep_nexus_url,
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
        f"fecha@{HTTP_DEP_URL}": {
            "version": http_dep_nexus_version,
            "resolved": http_dep_nexus_url,
            "integrity": http_dep_nexus_integrity,
        },
        f"leftpad@{GIT_DEP_URL}": {
            "version": git_dep_nexus_version,
            "resolved": git_dep_nexus_url,
            "integrity": git_dep_nexus_integrity,
        },
    }


@mock.patch("cachito.workers.pkg_managers.yarn._get_package_and_deps")
@mock.patch("cachito.workers.pkg_managers.yarn.get_yarn_proxy_repo_url")
@mock.patch("cachito.workers.pkg_managers.yarn.download_dependencies")
def test_resolve_yarn(mock_download_deps, mock_get_repo_url, mock_get_package_and_deps):
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

    mock_get_package_and_deps.return_value = {"package": mock_package, "deps": mock_deps}

    rv = yarn.resolve_yarn("/some/path", {"id": 1}, skip_deps={"foobar"})
    assert rv == mock_get_package_and_deps.return_value

    mock_get_package_and_deps.assert_called_once_with(
        Path("/some/path/package.json"), Path("/some/path/yarn.lock")
    )
    mock_get_repo_url.assert_called_once_with(1)
    mock_download_deps.assert_called_once_with(
        1, mock_deps, mock_get_repo_url.return_value, skip_deps={"foobar"}, pkg_manager="yarn"
    )
    assert n_pop_calls == len(mock_deps) * 2
