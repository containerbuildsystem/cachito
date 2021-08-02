# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import json
import re
from http import HTTPStatus
from pathlib import Path
from typing import Any, Dict, List, Union
from unittest import mock

import flask
import kombu.exceptions
import pytest

from cachito.common.checksum import hash_file
from cachito.common.packages_data import PackagesData
from cachito.common.paths import RequestBundleDir
from cachito.errors import CachitoError, ValidationError
from cachito.web.content_manifest import BASE_ICM, PARENT_PURL_PLACEHOLDER, Package
from cachito.web.models import (
    ConfigFileBase64,
    EnvironmentVariable,
    Flag,
    Request,
    RequestStateMapping,
    _validate_package_manager_exclusivity,
)
from cachito.web.utils import deep_sort_icm
from cachito.workers.tasks import (
    add_git_submodules_as_package,
    failed_request_callback,
    fetch_app_source,
    fetch_gomod_source,
    fetch_npm_source,
    fetch_pip_source,
    fetch_yarn_source,
    finalize_request,
    process_fetched_sources,
)

RE_INVALID_PACKAGES_VALUE = (
    r'The value of "packages.\w+" must be an array of objects with the following keys: \w+(, \w+)*'
)


def _write_test_packages_data(packages: List[Dict[str, Any]], filename: Union[str, Path]) -> None:
    packages_data = PackagesData()
    for pkg in packages:
        packages_data.add_package(
            {"name": pkg["name"], "type": pkg["type"], "version": pkg["version"]},
            ".",
            pkg["dependencies"],
        )
    packages_data.write_to_file(filename)


@mock.patch("cachito.web.api_v1.status")
def test_get_status(mock_status, client):
    mock_status.return_value = {"can_process": {}, "services": [], "workers": []}
    rv = client.get("api/v1/status")
    assert rv.status_code == 200
    assert rv.json == mock_status.return_value
    mock_status.assert_called_once()


@pytest.mark.parametrize("error", [None, "something is wrong"])
@mock.patch("cachito.web.api_v1.status")
def test_get_status_short(mock_status, error, client):
    if error:
        mock_status.side_effect = [CachitoError(error)]
    rv = client.get("api/v1/status/short")

    if error:
        assert rv.json == {"ok": False, "reason": error}
        assert rv.status_code == 503
    else:
        assert rv.json == {"ok": True}
        assert rv.status_code == 200


@pytest.mark.parametrize(
    "dependency_replacements, pkg_managers, user, expected_pkg_managers, flags",
    (
        ([], [], None, [], None,),
        ([], ["gomod", "git-submodule"], None, ["gomod", "git-submodule"], None,),
        (
            [{"name": "github.com/pkg/errors", "type": "gomod", "version": "v0.8.1"}],
            ["gomod"],
            None,
            ["gomod"],
            None,
        ),
        (
            [
                {
                    "name": "github.com/pkg/errors",
                    "new_name": "github.com/pkg_new_errors",
                    "type": "gomod",
                    "version": "v0.8.1",
                }
            ],
            ["gomod", "git-submodule"],
            None,
            ["gomod", "git-submodule"],
            None,
        ),
        ([], [], "tom_hanks@DOMAIN.LOCAL", [], None,),
        ([], ["npm"], None, ["npm"], None,),
        ([], ["pip"], None, ["pip"], None,),
        ([], ["yarn"], None, ["yarn"], None,),
    ),
)
@mock.patch("cachito.web.api_v1.chain")
@mock.patch("cachito.web.models._validate_package_manager_exclusivity")
def test_create_and_fetch_request(
    mock_validate_exclusivity,
    mock_chain,
    dependency_replacements,
    pkg_managers,
    user,
    expected_pkg_managers,
    app,
    auth_env,
    client,
    db,
    flags,
):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": pkg_managers,
    }
    if dependency_replacements:
        data["dependency_replacements"] = dependency_replacements
    if user:
        data["user"] = user
    if flags is not None:
        data["flags"] = flags

    rv = client.post("/api/v1/requests", json=data, environ_base=auth_env)
    assert rv.status_code == 201
    created_request = rv.json

    for key in data.keys() - {"dependency_replacements", "pkg_managers"}:
        assert data[key] == created_request[key]

    assert created_request["pkg_managers"] == expected_pkg_managers

    if user:
        assert created_request["user"] == "tom_hanks@DOMAIN.LOCAL"
        assert created_request["submitted_by"] == "tbrady@DOMAIN.LOCAL"
    else:
        assert created_request["user"] == "tbrady@DOMAIN.LOCAL"
        assert created_request["submitted_by"] is None

    error_callback = failed_request_callback.s(created_request["id"])
    expected = [
        fetch_app_source.s(
            "https://github.com/release-engineering/retrodep.git",
            "c50b93a32df1c9d700e3e80996845bc2e13be848",
            1,
            "git-submodule" in expected_pkg_managers,
        ).on_error(error_callback)
    ]
    if "gomod" in expected_pkg_managers:
        expected.append(
            fetch_gomod_source.si(created_request["id"], dependency_replacements, []).on_error(
                error_callback
            )
        )
    if "npm" in expected_pkg_managers:
        expected.append(fetch_npm_source.si(created_request["id"], []).on_error(error_callback))
    if "pip" in expected_pkg_managers:
        expected.append(fetch_pip_source.si(created_request["id"], []).on_error(error_callback))
    if "git-submodule" in expected_pkg_managers:
        expected.append(
            add_git_submodules_as_package.si(created_request["id"]).on_error(error_callback)
        )
    if "yarn" in expected_pkg_managers:
        expected.append(fetch_yarn_source.si(created_request["id"], []).on_error(error_callback))
    expected.append(process_fetched_sources.si(created_request["id"]).on_error(error_callback))
    expected.append(finalize_request.s(created_request["id"]).on_error(error_callback))
    mock_chain.assert_called_once_with(expected)

    request_id = created_request["id"]
    rv = client.get("/api/v1/requests/{}".format(request_id))
    assert rv.status_code == 200
    fetched_request = rv.json

    assert created_request == fetched_request
    assert fetched_request["state"] == "in_progress"
    assert fetched_request["state_reason"] == "The request was initiated"

    mock_validate_exclusivity.assert_called_once_with(
        pkg_managers, {}, app.config["CACHITO_MUTUALLY_EXCLUSIVE_PACKAGE_MANAGERS"]
    )


@mock.patch("cachito.web.api_v1.chain")
def test_create_request_with_gomod_package_configs(
    mock_chain, app, auth_env, client, db,
):
    package_value = {"gomod": [{"path": "."}, {"path": "proxy"}]}
    data = {
        "repo": "https://github.com/release-engineering/web-terminal.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "packages": package_value,
        "pkg_managers": ["gomod"],
    }

    rv = client.post("/api/v1/requests", json=data, environ_base=auth_env)
    assert rv.status_code == 201

    error_callback = failed_request_callback.s(1)
    expected = [
        fetch_app_source.s(
            "https://github.com/release-engineering/web-terminal.git",
            "c50b93a32df1c9d700e3e80996845bc2e13be848",
            1,
            False,
        ).on_error(error_callback),
        fetch_gomod_source.si(1, [], package_value["gomod"]).on_error(error_callback),
        process_fetched_sources.si(1).on_error(error_callback),
        finalize_request.s(1).on_error(error_callback),
    ]
    mock_chain.assert_called_once_with(expected)


@mock.patch("cachito.web.api_v1.chain")
def test_create_request_with_npm_package_configs(
    mock_chain, app, auth_env, client, db,
):
    package_value = {"npm": [{"path": "client"}, {"path": "proxy"}]}
    data = {
        "repo": "https://github.com/release-engineering/web-terminal.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "packages": package_value,
        "pkg_managers": ["npm"],
    }

    rv = client.post("/api/v1/requests", json=data, environ_base=auth_env)
    assert rv.status_code == 201

    error_callback = failed_request_callback.s(1)
    expected = [
        fetch_app_source.s(
            "https://github.com/release-engineering/web-terminal.git",
            "c50b93a32df1c9d700e3e80996845bc2e13be848",
            1,
            False,
        ).on_error(error_callback),
        fetch_npm_source.si(1, package_value["npm"]).on_error(error_callback),
        process_fetched_sources.si(1).on_error(error_callback),
        finalize_request.s(1).on_error(error_callback),
    ]
    mock_chain.assert_called_once_with(expected)


@pytest.mark.parametrize(
    "pkg_value",
    [
        [{"path": "client"}, {"path": "proxy"}],
        [{"path": "proxy"}],
        [{"path": ".", "requirements_files": ["alt.txt"]}],
        [{"path": ".", "requirements_build_files": ["alt.txt"]}],
        [{"requirements_files": ["alt.txt"], "requirements_build_files": ["bld.txt"], "path": "."}],
    ],
)
@mock.patch("cachito.web.api_v1.chain")
def test_create_request_with_pip_package_configs(mock_chain, app, auth_env, client, db, pkg_value):
    package_value = {"pip": pkg_value}
    data = {
        "repo": "https://github.com/release-engineering/web-terminal.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "packages": package_value,
        "pkg_managers": ["pip"],
    }

    rv = client.post("/api/v1/requests", json=data, environ_base=auth_env)
    # assert rv.status_code == 201
    if not rv.status_code == 201:
        assert rv.json["error"] == 201

    error_callback = failed_request_callback.s(1)
    expected = [
        fetch_app_source.s(
            "https://github.com/release-engineering/web-terminal.git",
            "c50b93a32df1c9d700e3e80996845bc2e13be848",
            1,
            False,
        ).on_error(error_callback),
        fetch_pip_source.si(1, package_value["pip"]).on_error(error_callback),
        process_fetched_sources.si(1).on_error(error_callback),
        finalize_request.s(1).on_error(error_callback),
    ]
    mock_chain.assert_called_once_with(expected)


@mock.patch("cachito.web.api_v1.chain")
def test_create_request_with_yarn_package_configs(
    mock_chain, app, auth_env, client, db,
):
    package_value = {"yarn": [{"path": "client"}, {"path": "proxy"}]}
    data = {
        "repo": "https://github.com/release-engineering/web-terminal.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "packages": package_value,
        "pkg_managers": ["yarn"],
    }

    rv = client.post("/api/v1/requests", json=data, environ_base=auth_env)
    assert rv.status_code == 201

    error_callback = failed_request_callback.s(1)
    expected = [
        fetch_app_source.s(
            "https://github.com/release-engineering/web-terminal.git",
            "c50b93a32df1c9d700e3e80996845bc2e13be848",
            1,
            False,
        ).on_error(error_callback),
        fetch_yarn_source.si(1, package_value["yarn"]).on_error(error_callback),
        process_fetched_sources.si(1).on_error(error_callback),
        finalize_request.s(1).on_error(error_callback),
    ]
    mock_chain.assert_called_once_with(expected)


@mock.patch("cachito.web.api_v1.chain")
def test_create_request_ssl_auth(mock_chain, auth_ssl_env, client, db):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
    }

    rv = client.post("/api/v1/requests", json=data, environ_base=auth_ssl_env)
    assert rv.status_code == 201
    created_request = rv.json

    cert_dn = "CN=tbrady,OU=serviceusers,DC=domain,DC=local"
    assert created_request["user"] == cert_dn


@mock.patch("cachito.web.api_v1.chain")
def test_create_and_fetch_request_with_flag(mock_chain, app, auth_env, client, db):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["gomod"],
        "flags": ["valid_flag"],
    }

    # Add a new active flag to db
    flag = Flag.from_json("valid_flag")
    db.session.add(flag)
    db.session.commit()

    rv = client.post("/api/v1/requests", json=data, environ_base=auth_env)
    assert rv.status_code == 201
    created_request = rv.json
    for key, expected_value in data.items():
        assert expected_value == created_request[key]
    assert created_request["user"] == "tbrady@DOMAIN.LOCAL"

    error_callback = failed_request_callback.s(1)
    mock_chain.assert_called_once_with(
        [
            fetch_app_source.s(
                "https://github.com/release-engineering/retrodep.git",
                "c50b93a32df1c9d700e3e80996845bc2e13be848",
                1,
                False,
            ).on_error(error_callback),
            fetch_gomod_source.si(1, [], []).on_error(error_callback),
            process_fetched_sources.si(1).on_error(error_callback),
            finalize_request.s(1).on_error(error_callback),
        ]
    )

    # Set the flag as inactive
    flag = Flag.query.filter_by(name="valid_flag").first()
    flag.active = False
    db.session.commit()

    request_id = created_request["id"]
    rv = client.get("/api/v1/requests/{}".format(request_id))
    assert rv.status_code == 200
    fetched_request = rv.json

    # The flag should be present even if it is inactive now
    assert fetched_request["flags"] == ["valid_flag"]
    assert fetched_request["state"] == "in_progress"
    assert fetched_request["state_reason"] == "The request was initiated"
    assert fetched_request["configuration_files"].endswith(
        f"/api/v1/requests/{request_id}/configuration-files"
    )
    assert fetched_request["logs"]["url"] == "http://localhost/api/v1/requests/1/logs"


def test_fetch_paginated_requests(
    app, auth_env, client, db, sample_deps_replace, sample_package, worker_auth_env, tmpdir
):
    sample_requests_count = 50
    repo_template = "https://github.com/release-engineering/retrodep{}.git"
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=auth_env):
        for i in range(sample_requests_count):
            data = {
                "repo": repo_template.format(i),
                "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
                "pkg_managers": ["gomod"],
            }
            request = Request.from_json(data)
            request.packages_count = 0
            request.dependencies_count = 0
            request.add_state(RequestStateMapping.complete.name, "Completed")

            db.session.add(request)
    db.session.commit()

    # Endpoint /requests/ returns requests in descending order of id.

    pkg_info = sample_package.copy()
    pkg_info["dependencies"] = sample_deps_replace
    packages_data = {"packages": [pkg_info]}

    cachito_bundles_dir = str(tmpdir)

    for r_id in [sample_requests_count, 40]:
        request = Request.query.get(r_id)
        request.packages_count = 1
        request.dependencies_count = len(sample_deps_replace)
        _write_test_packages_data(
            [pkg_info], RequestBundleDir(r_id, cachito_bundles_dir).packages_data
        )
    db.session.commit()

    flask.current_app.config["CACHITO_BUNDLES_DIR"] = cachito_bundles_dir

    # Sane defaults are provided
    rv = client.get("/api/v1/requests")
    assert rv.status_code == 200
    response = rv.json
    fetched_requests = response["items"]
    assert len(fetched_requests) == 10
    for i, request in enumerate(fetched_requests, 1):
        assert request["repo"] == repo_template.format(sample_requests_count - i)
    assert response["meta"]["previous"] is None
    assert fetched_requests[0]["dependencies"] == len(sample_deps_replace)
    assert fetched_requests[0]["packages"] == 1

    # Invalid per_page defaults to 10
    rv = client.get("/api/v1/requests?per_page=tom_hanks")
    assert len(rv.json["items"]) == 10
    assert response["meta"]["per_page"] == 10

    # per_page and page parameters are honored
    rv = client.get("/api/v1/requests?page=3&per_page=5&verbose=True&state=complete")
    assert rv.status_code == 200
    response = rv.json
    fetched_requests = response["items"]
    assert len(fetched_requests) == 5
    # Start at 10 because each page contains 5 items and we're processing the third page
    for i, request in enumerate(fetched_requests, 1):
        assert request["repo"] == repo_template.format(sample_requests_count - 10 - i)
    pagination_metadata = response["meta"]
    for page, page_num in [("next", 4), ("last", 10), ("previous", 2), ("first", 1)]:
        assert f"page={page_num}" in pagination_metadata[page]
        assert "per_page=5" in pagination_metadata[page]
        assert "verbose=True" in pagination_metadata[page]
        assert "state=complete" in pagination_metadata[page]
    assert pagination_metadata["total"] == sample_requests_count
    assert fetched_requests[0]["dependencies"] == pkg_info["dependencies"]
    assert fetched_requests[0]["packages"] == packages_data["packages"]


def test_create_request_filter_state(app, auth_env, client, db):
    """Test that requests can be filtered by state."""
    repo_template = "https://github.com/release-engineering/retrodep{}.git"
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=auth_env):
        # Make a request in 'in_progress' state
        data = {
            "repo": repo_template.format(0),
            "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
            "pkg_managers": ["gomod"],
        }
        request = Request.from_json(data)
        db.session.add(request)
        # Make a request in 'complete' state
        data_complete = {
            "repo": repo_template.format(1),
            "ref": "e1be527f39ec31323f0454f7d1422c6260b00580",
            "pkg_managers": ["gomod"],
        }
        request_complete = Request.from_json(data_complete)
        request_complete.add_state("complete", "Completed successfully")
        db.session.add(request_complete)
    db.session.commit()

    for state in ("in_progress", "complete"):
        rv = client.get(f"/api/v1/requests?state={state}")
        assert rv.status_code == 200
        fetched_requests = rv.json["items"]
        assert len(fetched_requests) == 1
        assert fetched_requests[0]["state"] == state


def test_fetch_request_config(app, client, db, worker_auth_env):
    data = {
        "repo": "https://github.com/namespace/project.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    config = ConfigFileBase64.get_or_create(
        path="app/.npmrc", content="cmVnaXN0cnk9aHR0cDovL2RvbWFpbi5sb2NhbC9yZXBvLwo="
    )
    db.session.add(config)
    config2 = ConfigFileBase64.get_or_create(
        path="app/.npmrc2", content="cmVnaXN0cnk9aHR0cDovL2RvbWFpbi5sb2NhbC9yZXBvLwo="
    )
    db.session.add(config2)
    request.config_files_base64.append(config)
    request.config_files_base64.append(config2)
    db.session.commit()

    rv = client.get("/api/v1/requests/1/configuration-files")
    expected = [
        {
            "content": "cmVnaXN0cnk9aHR0cDovL2RvbWFpbi5sb2NhbC9yZXBvLwo=",
            "path": "app/.npmrc",
            "type": "base64",
        },
        {
            "content": "cmVnaXN0cnk9aHR0cDovL2RvbWFpbi5sb2NhbC9yZXBvLwo=",
            "path": "app/.npmrc2",
            "type": "base64",
        },
    ]
    assert rv.json == expected


def test_fetch_request_config_empty(app, client, db, worker_auth_env):
    data = {
        "repo": "https://github.com/namespace/project.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    rv = client.get("/api/v1/requests/1/configuration-files")
    assert rv.json == []


def test_invalid_state(app, auth_env, client, db):
    """Test that the proper error is thrown when an invalid state is entered."""
    rv = client.get("/api/v1/requests?state=complet")
    assert rv.status_code == 400
    response = rv.json
    states = ", ".join(RequestStateMapping.get_state_names())
    assert response["error"] == f"complet is not a valid request state. Valid states are: {states}"


def assert_request_is_not_created(**criteria):
    assert 0 == Request.query.filter_by(**criteria).count()


@pytest.mark.parametrize("invalid_ref", ["not-a-ref", "23ae3f", "1234" * 20])
def test_create_request_invalid_ref(invalid_ref, auth_env, client, db):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": invalid_ref,
        "pkg_managers": ["gomod"],
    }

    rv = client.post("/api/v1/requests", json=data, environ_base=auth_env)
    assert rv.status_code == 400
    assert rv.json["error"] == 'The "ref" parameter must be a 40 character hex string'
    assert_request_is_not_created(ref=invalid_ref)


@pytest.mark.parametrize(
    "pkg_managers, expected",
    (
        (["something_wrong"], "The following package managers are invalid: something_wrong"),
        ("gomod", 'The "pkg_managers" value must be an array of strings'),
        ([True], 'The "pkg_managers" value must be an array of strings'),
    ),
)
def test_create_request_invalid_pkg_manager(pkg_managers, expected, auth_env, client, db):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": pkg_managers,
    }

    rv = client.post("/api/v1/requests", json=data, environ_base=auth_env)
    assert rv.status_code == 400
    assert rv.json["error"] == expected


@pytest.mark.parametrize(
    "pkg_manager, dependency_replacements, error_msg",
    (
        (
            "npm",
            ["mypackage"],
            "A dependency replacement must be a JSON object with the following keys: name, type, "
            "version. It may also contain the following optional keys: new_name.",
        ),
        ("npm", "mypackage", '"dependency_replacements" must be an array'),
        (
            "npm",
            [{"name": "rxjs", "type": "npm", "version": "6.5.5"}],
            "Dependency replacements are not yet supported for the npm package manager",
        ),
        (
            "pip",
            [{"name": "flexmock", "type": "pip", "version": "0.15"}],
            "Dependency replacements are not yet supported for the pip package manager",
        ),
        (
            "yarn",
            [{"name": "rxjs", "type": "yarn", "version": "6.5.5"}],
            "Dependency replacements are not yet supported for the yarn package manager",
        ),
    ),
)
def test_create_request_invalid_dependency_replacement(
    dependency_replacements, error_msg, auth_env, client, db, pkg_manager
):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "dependency_replacements": dependency_replacements,
        "pkg_managers": [pkg_manager],
    }

    rv = client.post("/api/v1/requests", json=data, environ_base=auth_env)
    assert rv.status_code == 400
    assert rv.json["error"] == error_msg


@pytest.mark.parametrize(
    "packages, pkg_managers, error_msg",
    (
        (["npm"], ["npm"], 'The "packages" parameter must be an object'),
        (
            {"gomod": [{"path": "client"}]},
            ["npm"],
            'The following package managers in the "packages" object do not apply: gomod',
        ),
        ({"gomod": {"path": "client"}}, ["gomod"], RE_INVALID_PACKAGES_VALUE),
        ({"gomod": ["path"]}, ["gomod"], RE_INVALID_PACKAGES_VALUE),
        ({"gomod": [{}]}, ["gomod"], RE_INVALID_PACKAGES_VALUE),
        (
            {"npm": [{"path": "client"}]},
            ["gomod"],
            'The following package managers in the "packages" object do not apply: npm',
        ),
        (
            {"gomod": [{"path": ""}]},
            ["gomod"],
            (
                'The "path" values in the "packages.gomod" value must be to a relative path in the '
                "source repository"
            ),
        ),
        (
            {"gomod": [{"path": "/foo/bar"}]},
            ["gomod"],
            (
                'The "path" values in the "packages.gomod" value must be to a relative path in the '
                "source repository"
            ),
        ),
        (
            {"gomod": [{"path": "../foo"}]},
            ["gomod"],
            (
                'The "path" values in the "packages.gomod" value must be to a relative path in the '
                "source repository"
            ),
        ),
        ({"npm": {"path": "client"}}, ["npm"], RE_INVALID_PACKAGES_VALUE),
        ({"npm": ["path"]}, ["npm"], RE_INVALID_PACKAGES_VALUE),
        ({"npm": [{}]}, ["npm"], RE_INVALID_PACKAGES_VALUE),
        (
            {"npm": [{"path": 1}]},
            ["npm"],
            (
                'The "path" values in the "packages.npm" value must be to a relative path in the '
                "source repository"
            ),
        ),
        (
            {"npm": [{"path": ""}]},
            ["npm"],
            (
                'The "path" values in the "packages.npm" value must be to a relative path in the '
                "source repository"
            ),
        ),
        (
            {"npm": [{"path": "/etc/httpd"}]},
            ["npm"],
            (
                'The "path" values in the "packages.npm" value must be to a relative path in the '
                "source repository"
            ),
        ),
        (
            {"npm": [{"path": "../../../../etc/httpd"}]},
            ["npm"],
            (
                'The "path" values in the "packages.npm" value must be to a relative path in the '
                "source repository"
            ),
        ),
        ({"pip": {"path": "client"}}, ["pip"], RE_INVALID_PACKAGES_VALUE),
        ({"pip": [{}]}, ["pip"], RE_INVALID_PACKAGES_VALUE),
        (
            {"pip": [{"requirements_files": ["../etc/httpd", "foo"]}]},
            ["pip"],
            (
                'The "requirements_files" values in the "packages.pip" value must be to a relative '
                "path in the source repository"
            ),
        ),
        (
            {"pip": [{"requirements_files": ["foo"], "requirements_build_files": ["../foo"]}]},
            ["pip"],
            (
                'The "requirements_build_files" values in the "packages.pip" value must be to a '
                "relative path in the source repository"
            ),
        ),
        ({"yarn": {"path": "client"}}, ["yarn"], RE_INVALID_PACKAGES_VALUE),
        ({"yarn": ["path"]}, ["yarn"], RE_INVALID_PACKAGES_VALUE),
        ({"yarn": [{}]}, ["yarn"], RE_INVALID_PACKAGES_VALUE),
        (
            {"yarn": [{"path": 1}]},
            ["yarn"],
            (
                'The "path" values in the "packages.yarn" value must be to a relative path in the '
                "source repository"
            ),
        ),
        (
            {"yarn": [{"path": ""}]},
            ["yarn"],
            (
                'The "path" values in the "packages.yarn" value must be to a relative path in the '
                "source repository"
            ),
        ),
        (
            {"yarn": [{"path": "/etc/httpd"}]},
            ["yarn"],
            (
                'The "path" values in the "packages.yarn" value must be to a relative path in the '
                "source repository"
            ),
        ),
        (
            {"yarn": [{"path": "../../../../etc/httpd"}]},
            ["yarn"],
            (
                'The "path" values in the "packages.yarn" value must be to a relative path in the '
                "source repository"
            ),
        ),
    ),
)
def test_create_request_invalid_packages(packages, pkg_managers, error_msg, auth_env, client, db):
    data = {
        "repo": "https://github.com/release-engineering/web-terminal.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "packages": packages,
        "pkg_managers": pkg_managers,
    }

    rv = client.post("/api/v1/requests", json=data, environ_base=auth_env)
    assert rv.status_code == 400
    assert re.match(error_msg, rv.json["error"])


def test_create_request_not_an_object(auth_env, client, db):
    rv = client.post("/api/v1/requests", json=None, environ_base=auth_env)
    assert rv.status_code == 400
    assert rv.json["error"] == "The input data must be a JSON object"


def test_create_request_invalid_parameter(auth_env, client, db):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["gomod"],
        "username": "uncle_sam",
    }

    rv = client.post("/api/v1/requests", json=data, environ_base=auth_env)
    assert rv.status_code == 400
    assert rv.json["error"] == "The following parameters are invalid: username"


def test_create_request_cannot_set_user(client, db):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "user": "tom_hanks@DOMAIN.LOCAL",
    }

    auth_env = {"REMOTE_USER": "homer_simpson@DOMAIN.LOCAL"}
    rv = client.post("/api/v1/requests", json=data, environ_base=auth_env)
    assert rv.status_code == 403
    error = rv.json
    assert error["error"] == "You are not authorized to create a request on behalf of another user"


def test_cannot_set_user_if_auth_disabled(client_no_auth):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "user": "tselleck",
    }

    rv = client_no_auth.post("/api/v1/requests", json=data)
    assert rv.status_code == 400
    assert rv.json["error"] == 'Cannot set "user" when authentication is disabled'


def test_create_request_not_logged_in(client, db):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["gomod"],
    }

    rv = client.post("/api/v1/requests", json=data)
    assert rv.status_code == 401
    assert rv.json["error"] == (
        "The server could not verify that you are authorized to access the URL requested. You "
        "either supplied the wrong credentials (e.g. a bad password), or your browser doesn't "
        "understand how to supply the credentials required."
    )


def test_missing_request(client, db):
    rv = client.get("/api/v1/requests/1")
    assert rv.status_code == 404

    rv = client.get("/api/v1/requests/1/download")
    assert rv.status_code == 404


def test_malformed_request_id(client, db):
    rv = client.get("/api/v1/requests/spam")
    assert rv.status_code == 404
    assert rv.json == {"error": "The requested resource was not found"}


def test_create_request_invalid_flag(auth_env, client, db):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["gomod"],
        "flags": ["invalid_flag"],
    }

    rv = client.post("/api/v1/requests", json=data, environ_base=auth_env)
    assert rv.status_code == 400
    assert rv.json["error"] == "Invalid/Inactive flag(s): invalid_flag"


@pytest.mark.parametrize("removed_params", (("repo", "ref"), ("repo",), ("ref",)))
def test_validate_required_params(auth_env, client, db, removed_params):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
    }
    for removed_param in removed_params:
        data.pop(removed_param)

    rv = client.post("/api/v1/requests", json=data, environ_base=auth_env)
    assert rv.status_code == 400
    error_msg = rv.json["error"]
    assert "Missing required" in error_msg
    for removed_param in removed_params:
        assert removed_param in error_msg


def test_validate_extraneous_params(auth_env, client, db):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["gomod"],
        "spam": "maps",
    }

    rv = client.post("/api/v1/requests", json=data, environ_base=auth_env)
    assert rv.status_code == 400
    error_msg = rv.json["error"]
    assert error_msg == "The following parameters are invalid: spam"


@mock.patch("cachito.web.api_v1.chain")
def test_create_request_connection_error(mock_chain, app, auth_env, client, db):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["gomod"],
    }

    mock_chain.return_value.delay.side_effect = kombu.exceptions.OperationalError(
        "Failed to connect"
    )
    rv = client.post("/api/v1/requests", json=data, environ_base=auth_env)

    engine = db.create_engine(app.config["SQLALCHEMY_DATABASE_URI"], {})
    connection = engine.connect()
    request_state = db.Table("request_state", db.MetaData(), autoload=True, autoload_with=engine)
    query = db.select([request_state]).where(request_state.columns.request_id == 1)

    state_reasons = []
    for res in connection.execute(query):
        state_reasons.append(res[2])

    error = "Failed to schedule the task to the workers. Please try again."
    assert any(elem == error for elem in state_reasons)

    assert rv.status_code == 503
    assert rv.json == {"error": error}
    # Verify that the request is in the failed state
    assert Request.query.get(1).state.state_name == "failed"


def test_create_request_using_disabled_pkg_manager(app, auth_env, client, db):
    app.config["CACHITO_PACKAGE_MANAGERS"] = ["gomod"]
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["npm"],
    }

    rv = client.post("/api/v1/requests", json=data, environ_base=auth_env)

    assert rv.status_code == 400
    assert rv.json == {"error": "The following package managers are not enabled: npm"}


def test_download_archive(app, client, db, tmpdir):
    request = Request(repo="https://git.host/ns/tool.git", ref="1234")
    request.add_state(RequestStateMapping.complete.name, "For testing download.")
    db.session.add(request)
    db.session.commit()

    app.config["CACHITO_BUNDLES_DIR"] = str(tmpdir)

    file_content = b"1234"
    bundle_dir = RequestBundleDir(request.id, str(tmpdir))
    bundle_dir.bundle_archive_file.write_bytes(file_content)
    hasher = hash_file(bundle_dir.bundle_archive_file)
    bundle_dir.bundle_archive_checksum.write_text(hasher.hexdigest(), encoding="utf-8")

    resp = client.get(f"/api/v1/requests/{request.id}/download")
    assert file_content == resp.data
    filename = bundle_dir.bundle_archive_file.name
    assert f"attachment; filename=cachito-{filename}" == resp.headers["Content-Disposition"]
    assert "sha-256=A6xnQhbz4Vx2HuGl4lXwZ5U2I8iziLRFnhP5eNfIRvQ=" == resp.headers["Digest"]


@mock.patch("cachito.web.api_v1.Request")
def test_download_archive_no_bundle(mock_request, client, app):
    request = mock.Mock(id=1)
    request.state.state_name = "complete"
    mock_request.query.get_or_404.return_value = request
    rv = client.get("/api/v1/requests/1/download")
    assert rv.status_code == 500


@mock.patch("cachito.web.api_v1.Request")
def test_download_archive_not_complete(mock_request, client, db, app):
    mock_request.query.get_or_404().last_state.state_name = "in_progress"
    rv = client.get("/api/v1/requests/1/download")
    assert rv.status_code == 400
    assert rv.json == {
        "error": 'The request must be in the "complete" state before downloading the archive'
    }


def test_download_modified_bundle_archive(app, client, db, tmpdir):
    request = Request(repo="https://git.host/ns/tool.git", ref="1234")
    request.add_state(RequestStateMapping.complete.name, "For testing download.")
    db.session.add(request)
    db.session.commit()

    app.config["CACHITO_BUNDLES_DIR"] = str(tmpdir)
    logger = mock.Mock()
    app.logger = logger

    bundle_dir = RequestBundleDir(request.id, str(tmpdir))
    bundle_dir.bundle_archive_checksum.write_text("1234", encoding="utf-8")
    # Modify the bundle archive. So, when download, a different checksum will be computed.
    bundle_dir.bundle_archive_file.write_bytes(b"1234")

    rv = client.get("/api/v1/requests/1/download")
    assert rv.status_code == 500
    assert rv.json == {
        "error": f"Checksum of bundle archive {bundle_dir.bundle_archive_file.name} has changed."
    }


@pytest.mark.parametrize("state", ("complete", "failed"))
def test_set_state(state, app, client, db, worker_auth_env, tmpdir):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["gomod"],
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    request_id = 1
    state = state
    state_reason = "Some status"
    payload = {"state": state, "state_reason": state_reason}

    bundle_dir = RequestBundleDir(request_id, tmpdir)
    bundle_dir.mkdir(parents=True)
    with mock.patch("cachito.web.api_v1.RequestBundleDir", return_value=bundle_dir):
        patch_rv = client.patch(
            f"/api/v1/requests/{request_id}", json=payload, environ_base=worker_auth_env
        )

    assert patch_rv.status_code == 200

    get_rv = client.get(f"/api/v1/requests/{request_id}")
    assert get_rv.status_code == 200

    fetched_request = get_rv.json
    assert fetched_request["state"] == state
    assert fetched_request["state_reason"] == state_reason
    # Since the date is always changing, the actual value can't be confirmed
    assert fetched_request["updated"]
    assert len(fetched_request["state_history"]) == 2
    # Make sure the order is from newest to oldest
    assert fetched_request["state_history"][0]["state"] == state
    assert fetched_request["state_history"][0]["state_reason"] == state_reason
    assert fetched_request["state_history"][0]["updated"]
    assert fetched_request["state_history"][1]["state"] == "in_progress"

    assert not bundle_dir.exists()


@pytest.mark.parametrize("bundle_exists", (True, False))
@pytest.mark.parametrize("pkg_managers", (["gomod"], ["npm"], ["gomod", "npm"]))
@mock.patch("cachito.web.api_v1.tasks.cleanup_npm_request")
def test_set_state_stale(
    mock_cleanup_npm, pkg_managers, bundle_exists, app, client, db, worker_auth_env, tmpdir,
):
    data = {
        "repo": "https://github.com/release-engineering/project.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": pkg_managers,
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    bundle_dir = RequestBundleDir(1, str(tmpdir))
    bundle_dir.mkdir(parents=True)
    bundle_dir.bundle_archive_file.write_bytes(b"01234")
    bundle_dir.packages_data.write_bytes(b"{}")

    bundle_dir.bundle_archive_checksum.write_text("1234", encoding="utf-8")

    state = "stale"
    state_reason = "The request has expired"
    payload = {"state": state, "state_reason": state_reason}

    with mock.patch("cachito.web.api_v1.RequestBundleDir", return_value=bundle_dir):
        patch_rv = client.patch("/api/v1/requests/1", json=payload, environ_base=worker_auth_env)

    assert patch_rv.status_code == 200

    get_rv = client.get("/api/v1/requests/1")
    assert get_rv.status_code == 200

    fetched_request = get_rv.get_json()
    assert fetched_request["state"] == state
    assert fetched_request["state_reason"] == state_reason

    assert not bundle_dir.bundle_archive_file.exists()
    assert not bundle_dir.bundle_archive_checksum.exists()
    assert not bundle_dir.packages_data.exists()

    if "npm" in pkg_managers:
        mock_cleanup_npm.delay.assert_called_once_with(1)
    else:
        mock_cleanup_npm.assert_not_called()


@mock.patch("pathlib.Path.exists")
@mock.patch("pathlib.Path.unlink")
@mock.patch("cachito.web.api_v1.tasks.cleanup_npm_request")
def test_set_state_stale_failed_to_schedule(
    mock_cleanup_npm, mock_remove, mock_exists, app, client, db, worker_auth_env
):
    mock_cleanup_npm.delay.side_effect = kombu.exceptions.OperationalError("Failed to connect")
    mock_exists.return_value = True
    data = {
        "repo": "https://github.com/release-engineering/project.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["npm"],
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    payload = {"state": "stale", "state_reason": "The request has expired"}
    patch_rv = client.patch("/api/v1/requests/1", json=payload, environ_base=worker_auth_env)

    # Verify that even though the cleanup_npm_request task failed to schedule, the PATCH
    # request still succeeded
    assert patch_rv.status_code == 200
    mock_cleanup_npm.delay.assert_called_once()


@pytest.mark.parametrize(
    "initial_state,to_state,expected_resp_code",
    [
        ["complete", "stale", HTTPStatus.OK],
        ["failed", "stale", HTTPStatus.OK],
        ["in_progress", "complete", HTTPStatus.OK],
        ["in_progress", "failed", HTTPStatus.OK],
        ["in_progress", "in_progress", HTTPStatus.OK],
        ["in_progress", "stale", HTTPStatus.OK],
        # Invalid transition
        ["complete", "complete", HTTPStatus.BAD_REQUEST],
        ["complete", "failed", HTTPStatus.BAD_REQUEST],
        ["complete", "in_progress", HTTPStatus.BAD_REQUEST],
        ["failed", "complete", HTTPStatus.BAD_REQUEST],
        ["failed", "failed", HTTPStatus.BAD_REQUEST],
        ["failed", "in_progress", HTTPStatus.BAD_REQUEST],
        ["stale", "complete", HTTPStatus.BAD_REQUEST],
        ["stale", "failed", HTTPStatus.BAD_REQUEST],
        ["stale", "in_progress", HTTPStatus.BAD_REQUEST],
        ["stale", "stale", HTTPStatus.BAD_REQUEST],
    ],
)
def test_request_state_transition(
    initial_state: str, to_state: str, expected_resp_code: int, app, db, client, worker_auth_env
):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["gomod"],
    }
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()
    request.add_state(initial_state, "Set initial state")
    db.session.commit()

    payload = {"state": to_state, "state_reason": "for testing"}
    rv = client.patch(f"/api/v1/requests/{request.id}", json=payload, environ_base=worker_auth_env)

    assert expected_resp_code == rv.status_code
    if expected_resp_code == HTTPStatus.BAD_REQUEST:
        assert initial_state == Request.query.get(request.id).state.state_name
        assert {
            "error": f"State transition is not allowed from {initial_state} to {to_state}."
        } == rv.get_json()
    else:
        assert to_state == Request.query.get(request.id).state.state_name


def test_set_state_no_duplicate(app, client, db, worker_auth_env):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["gomod"],
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    state = "complete"
    state_reason = "Completed successfully"
    payload = {"state": state, "state_reason": state_reason}
    for i in range(3):
        patch_rv = client.patch("/api/v1/requests/1", json=payload, environ_base=worker_auth_env)
        assert patch_rv.status_code == 200

    get_rv = client.get("/api/v1/requests/1")
    assert get_rv.status_code == 200

    # Make sure no duplicate states were added
    assert len(get_rv.json["state_history"]) == 2


def test_set_state_not_logged_in(client, db):
    payload = {"state": "complete", "state_reason": "Completed successfully"}
    rv = client.patch("/api/v1/requests/1", json=payload)
    assert rv.status_code == 401
    assert rv.json["error"] == (
        "The server could not verify that you are authorized to access the URL requested. You "
        "either supplied the wrong credentials (e.g. a bad password), or your browser doesn't "
        "understand how to supply the credentials required."
    )


def test_set_packages_and_deps_count(app, client, db, worker_auth_env):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    assert request.packages_count is None
    assert request.dependencies_count is None

    rv = client.patch(
        "/api/v1/requests/1",
        json={"packages_count": 0, "dependencies_count": 0},
        environ_base=worker_auth_env,
    )
    assert rv.status_code == 200
    assert request.packages_count == 0
    assert request.dependencies_count == 0

    rv = client.patch(
        "/api/v1/requests/1",
        json={"packages_count": 10, "dependencies_count": 100},
        environ_base=worker_auth_env,
    )
    assert rv.status_code == 200
    assert request.packages_count == 10
    assert request.dependencies_count == 100


@pytest.mark.parametrize(
    "request_id, payload, status_code, message",
    (
        (
            1,
            {"state": "call_for_support", "state_reason": "It broke"},
            400,
            'The state "call_for_support" is invalid. It must be one of: complete, failed, '
            "in_progress, stale.",
        ),
        (
            1337,
            {"state": "complete", "state_reason": "Success"},
            404,
            "The requested resource was not found",
        ),
        (1, {}, 400, "At least one key must be specified to update the request"),
        (
            1,
            {"state": "complete", "state_reason": "Success", "id": 42},
            400,
            "The following keys are not allowed: id",
        ),
        (1, {"state": 1, "state_reason": "Success"}, 400, 'The value for "state" must be a string'),
        (
            1,
            {"state": "complete"},
            400,
            'The "state_reason" key is required when "state" is supplied',
        ),
        (
            1,
            {"state_reason": "Success"},
            400,
            'The "state" key is required when "state_reason" is supplied',
        ),
        (1, "some string", 400, "The input data must be a JSON object"),
        (
            1,
            {"environment_variables": "spam"},
            400,
            'The value for "environment_variables" must be an object',
        ),
        (
            1,
            {"environment_variables": {"spam": None}},
            400,
            "The info of environment variables must be an object",
        ),
        (
            1,
            {"environment_variables": {"spam": ["maps"]}},
            400,
            "The info of environment variables must be an object",
        ),
        (
            1,
            {"environment_variables": {"spam": {}}},
            400,
            "The following keys must be set in the info of the environment variables: kind, value",
        ),
        (
            1,
            {"environment_variables": {"spam": {"value": "maps", "kind": "literal", "x": "ham"}}},
            400,
            "The following keys are not allowed in the info of the environment variables: x",
        ),
        (
            1,
            {"environment_variables": {"spam": {"value": 101, "kind": "literal"}}},
            400,
            "The value of environment variables must be a string",
        ),
        (
            1,
            {"environment_variables": {"spam": {"value": "maps", "kind": 101}}},
            400,
            "The kind of environment variables must be a string",
        ),
        (
            1,
            {"environment_variables": {"spam": {"value": "maps", "kind": "ham"}}},
            400,
            "The environment variable kind, ham, is not supported",
        ),
        (1, {"packages_count": 1.5}, 400, 'The value for "packages_count" must be an integer'),
        (
            1,
            {"dependencies_count": 2.5},
            400,
            'The value for "dependencies_count" must be an integer',
        ),
    ),
)
def test_request_patch_invalid(
    app, client, db, worker_auth_env, request_id, payload, status_code, message
):
    data = {
        "repo": "https://github.com/release-engineering/project.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": [],
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    rv = client.patch(f"/api/v1/requests/{request_id}", json=payload, environ_base=worker_auth_env)
    assert rv.status_code == status_code
    assert rv.json == {"error": message}


def test_request_patch_not_authorized(auth_env, client, db):
    rv = client.patch("/api/v1/requests/1", json={}, environ_base=auth_env)
    assert rv.status_code == 403
    assert rv.json["error"] == "This API endpoint is restricted to Cachito workers"


def test_request_post_config(app, client, db, worker_auth_env):
    data = {
        "repo": "https://github.com/namespace/project.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    payload = [
        {
            "content": "cmVnaXN0cnk9aHR0cDovL2RvbWFpbi5sb2NhbC9yZXBvLwo=",
            "path": "app/.npmrc",
            "type": "base64",
        },
        {
            "content": "cmVnaXN0cnk9aHR0cDovL2RvbWFpbi5sb2NhbC9yZXBvLwo=",
            "path": "app/.npmrc2",
            "type": "base64",
        },
    ]
    rv = client.post(
        "/api/v1/requests/1/configuration-files", json=payload, environ_base=worker_auth_env
    )
    assert rv.status_code == 204


@pytest.mark.parametrize(
    "request_id, payload, status_code, message",
    (
        (1, {"hello": "world"}, 400, "The input data must be a JSON array"),
        (1337, [], 404, "The requested resource was not found"),
        (
            1,
            [{"content": "cmVnaXN0cnk9aHR0cDovL2RvbWFpbi5sb2NhbC9yZXBvLwo=", "type": "base64"}],
            400,
            "The following keys for the base64 configuration file are missing: path",
        ),
        (
            1,
            [{"content": "Home on the range", "path": "app/music", "type": "song"}],
            400,
            'The configuration type of "song" is invalid',
        ),
        (
            1,
            [
                {
                    "content": "cmVnaXN0cnk9aHR0cDovL2RvbWFpbi5sb2NhbC9yZXBvLwo=",
                    "lunch": "time",
                    "path": "app/.npmrc",
                    "type": "base64",
                }
            ],
            400,
            "The following keys for the base64 configuration file are invalid: lunch",
        ),
        (
            1,
            [
                {
                    "content": "cmVnaXN0cnk9aHR0cDovL2RvbWFpbi5sb2NhbC9yZXBvLwo=",
                    "path": 3,
                    "type": "base64",
                }
            ],
            400,
            'The base64 configuration file key of "path" must be a string',
        ),
        (
            1,
            [{"content": 123, "path": "src/.npmrc", "type": "base64"}],
            400,
            'The base64 configuration file key of "content" must be a string',
        ),
    ),
)
def test_request_post_config_invalid(
    app, client, db, worker_auth_env, request_id, payload, status_code, message
):
    data = {
        "repo": "https://github.com/namespace/project.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    rv = client.post(
        f"/api/v1/requests/{request_id}/configuration-files",
        json=payload,
        environ_base=worker_auth_env,
    )
    assert rv.status_code == status_code
    assert rv.json == {"error": message}


def test_request_config_post_not_authorized(auth_env, client, db):
    rv = client.post("/api/v1/requests/1/configuration-files", json={}, environ_base=auth_env)
    assert rv.status_code == 403
    assert rv.json["error"] == "This API endpoint is restricted to Cachito workers"


def test_fetch_request_content_manifest_empty(app, client, db, worker_auth_env):
    json_schema_url = (
        "https://raw.githubusercontent.com/containerbuildsystem/atomic-reactor/"
        "f4abcfdaf8247a6b074f94fa84f3846f82d781c6/atomic_reactor/schemas/content_manifest.json"
    )
    data = {
        "repo": "https://github.com/namespace/project.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    request.add_state("complete", "Completed successfully")
    db.session.add(request)
    db.session.commit()

    rv = client.get("/api/v1/requests/1/content-manifest")

    expected = {
        "metadata": {"icm_version": 1, "icm_spec": json_schema_url, "image_layer_index": -1},
        "image_contents": [],
    }

    assert rv.json == expected


def test_request_fetch_request_content_manifest_invalid(client, worker_auth_env):
    rv = client.get("/api/v1/requests/2/content-manifest")

    assert rv.status_code == 404
    assert rv.json == {"error": "The requested resource was not found"}


@pytest.mark.parametrize("state", ["complete", "stale", "in_progress", "failed"])
@mock.patch("cachito.web.models.Request._get_packages_data")
def test_fetch_request_content_manifest_go(
    mock_get_packages_data,
    app,
    client,
    db,
    worker_auth_env,
    sample_package,
    sample_deps,
    sample_pkg_lvl_pkg,
    sample_pkg_deps,
    state,
):
    json_schema_url = (
        "https://raw.githubusercontent.com/containerbuildsystem/atomic-reactor/"
        "f4abcfdaf8247a6b074f94fa84f3846f82d781c6/atomic_reactor/schemas/content_manifest.json"
    )
    data = {
        "repo": "https://github.com/namespace/project.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["gomod"],
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    request.add_state(state, "Some state")
    db.session.add(request)
    db.session.commit()

    # set expectations
    main_pkg = Package.from_json(sample_pkg_lvl_pkg).to_purl()
    image_content = {"purl": main_pkg, "dependencies": [], "sources": []}

    for d in sample_deps:
        d.pop("replaces")
        p = Package.from_json(d).to_purl().replace(PARENT_PURL_PLACEHOLDER, main_pkg)
        image_content["sources"].append({"purl": p})
    for d in sample_pkg_deps:
        d.pop("replaces")
        p = Package.from_json(d).to_purl().replace(PARENT_PURL_PLACEHOLDER, main_pkg)
        image_content["dependencies"].append({"purl": p})

    expected = {
        "image_contents": [image_content],
        "metadata": {"icm_version": 1, "icm_spec": json_schema_url, "image_layer_index": -1},
    }
    expected = deep_sort_icm(expected)

    # mock packages.json file contents
    sample_pkg_lvl_pkg["dependencies"] = sample_pkg_deps
    sample_package["dependencies"] = sample_deps
    packages_data = PackagesData()
    packages_data._packages = [sample_pkg_lvl_pkg, sample_package]
    mock_get_packages_data.return_value = packages_data

    rv = client.get("/api/v1/requests/1")
    assert rv.status_code == 200
    response = rv.json
    assert response["content_manifest"].endswith("/api/v1/requests/1/content-manifest")

    rv = client.get("/api/v1/requests/1/content-manifest")
    if state in ("complete", "stale"):
        assert rv.status_code == 200
        assert rv.json == expected
    else:
        assert rv.status_code == 400
        err_msg = (
            'Content manifests are only available for requests in the "complete" or "stale" states'
        )
        assert rv.json == {"error": err_msg}


@pytest.mark.parametrize("pkg_manager, purl_type", [("npm", "npm"), ("pip", "pypi")])
@pytest.mark.parametrize("state", ["complete", "stale", "in_progress", "failed"])
@mock.patch("cachito.web.models.Request._get_packages_data")
def test_fetch_request_content_manifest_npm_or_pip(
    mock_get_packages_data,
    app,
    client,
    db,
    auth_env,
    worker_auth_env,
    state,
    pkg_manager,
    purl_type,
):
    data = {
        "repo": "https://github.com/release-engineering/dummy.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": [pkg_manager],
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=auth_env):
        request = Request.from_json(data)
    request.add_state(state, "Some state")
    db.session.add(request)
    db.session.commit()

    pkgs = [
        {"name": "pkg-aa", "type": pkg_manager, "version": "1.0.0"},
        {"name": "pkg-bb", "type": pkg_manager, "version": "1.0.0"},
    ]
    deps = [
        {"dev": True, "name": "dep-aa", "replaces": None, "type": pkg_manager, "version": "1.0.0"},
        {"dev": True, "name": "dep-bb", "replaces": None, "type": pkg_manager, "version": "2.0.0"},
        {"dev": True, "name": "dep-cc", "replaces": None, "type": pkg_manager, "version": "3.0.0"},
        {"dev": False, "name": "dep-dd", "replaces": None, "type": pkg_manager, "version": "4.0.0"},
    ]

    pkgs[0]["dependencies"] = deps[:2]
    pkgs[1]["dependencies"] = deps[2:]
    packages_data = PackagesData()
    packages_data._packages = pkgs

    mock_get_packages_data.return_value = packages_data

    rv = client.get("/api/v1/requests/1")
    assert rv.status_code == 200

    # set expectations
    json_schema_url = (
        "https://raw.githubusercontent.com/containerbuildsystem/atomic-reactor/"
        "f4abcfdaf8247a6b074f94fa84f3846f82d781c6/atomic_reactor/schemas/content_manifest.json"
    )

    image_contents = [
        {
            "dependencies": [],
            "purl": f"pkg:github/release-engineering/dummy@{data['ref']}",
            "sources": [
                {"purl": f"pkg:{purl_type}/dep-aa@1.0.0"},
                {"purl": f"pkg:{purl_type}/dep-bb@2.0.0"},
            ],
        },
        {
            "dependencies": [{"purl": f"pkg:{purl_type}/dep-dd@4.0.0"}],
            "purl": f"pkg:github/release-engineering/dummy@{data['ref']}",
            "sources": [
                {"purl": f"pkg:{purl_type}/dep-cc@3.0.0"},
                {"purl": f"pkg:{purl_type}/dep-dd@4.0.0"},
            ],
        },
    ]

    expected = {
        "image_contents": image_contents,
        "metadata": {"icm_version": 1, "icm_spec": json_schema_url, "image_layer_index": -1},
    }

    rv = client.get("/api/v1/requests/1")
    assert rv.status_code == 200
    response = rv.json
    assert response["content_manifest"].endswith("/api/v1/requests/1/content-manifest")

    rv = client.get("/api/v1/requests/1/content-manifest")
    if state in ("complete", "stale"):
        assert rv.status_code == 200
        assert rv.json == expected
    else:
        assert rv.status_code == 400
        err_msg = (
            'Content manifests are only available for requests in the "complete" or "stale" states'
        )
        assert rv.json == {"error": err_msg}


@pytest.mark.parametrize("pkg_type", ["unknown", "gomod"])
def test_fetch_request_content_manifest_non_implemented_type(
    app, client, db, worker_auth_env, sample_package, sample_deps, pkg_type
):
    json_schema_url = (
        "https://raw.githubusercontent.com/containerbuildsystem/atomic-reactor/"
        "f4abcfdaf8247a6b074f94fa84f3846f82d781c6/atomic_reactor/schemas/content_manifest.json"
    )
    data = {
        "repo": "https://github.com/namespace/project.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["gomod"],
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    request.add_state("complete", "Completed successfully")
    db.session.add(request)
    db.session.commit()

    for p in sample_deps + [sample_package]:
        p["type"] = pkg_type

    image_contents = []

    expected = {
        "metadata": {"icm_version": 1, "icm_spec": json_schema_url, "image_layer_index": -1},
        "image_contents": image_contents,
    }

    # emulate worker
    payload = {"dependencies": sample_deps, "package": sample_package}
    client.patch("/api/v1/requests/1", json=payload, environ_base=worker_auth_env)

    rv = client.get("/api/v1/requests/1")
    assert rv.status_code == 200
    response = rv.json
    assert response["content_manifest"].endswith("/api/v1/requests/1/content-manifest")

    rv = client.get("/api/v1/requests/1/content-manifest")
    assert rv.json == expected


@pytest.mark.parametrize(
    "env_vars",
    (
        None,
        {},
        {"spam": {"value": "maps", "kind": "literal"}, "ham": {"value": "mah", "kind": "path"}},
    ),
)
def test_get_environment_variables(app, client, db, worker_auth_env, env_vars):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["gomod"],
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    # If env_vars is None skip the PATCH request to verify information about environment variables
    # are available prior to being set.
    if env_vars is not None:
        payload = {"environment_variables": env_vars}
        patch_rv = client.patch(
            f"/api/v1/requests/{request.id}", json=payload, environ_base=worker_auth_env
        )
        assert patch_rv.status_code == 200

    env_vars_expected = env_vars or {}

    assert EnvironmentVariable.query.count() == len(env_vars_expected)

    for name, info in env_vars_expected.items():
        env_var_obj = EnvironmentVariable.query.filter_by(name=name, **info).first()
        assert env_var_obj

    get_rv = client.get(f"/api/v1/requests/{request.id}")
    assert get_rv.status_code == 200
    fetched_request = get_rv.json
    assert fetched_request["environment_variables"] == {
        name: info["value"] for name, info in env_vars_expected.items()
    }
    assert fetched_request["environment_variables_info"].endswith(
        f"/api/v1/requests/{request.id}/environment-variables"
    )

    get_env_vars_rv = client.get(f"/api/v1/requests/{request.id}/environment-variables")
    assert get_env_vars_rv.status_code == 200
    assert get_env_vars_rv.json == env_vars_expected


@pytest.mark.parametrize(
    ("logs_content", "stale", "finalized", "expected"),
    (
        ("foobar", False, False, {"status": 200, "mimetype": "text/plain", "data": "foobar"}),
        ("foobar", True, False, {"status": 200, "mimetype": "text/plain", "data": "foobar"}),
        ("", False, False, {"status": 200, "mimetype": "text/plain", "data": ""}),
        ("", True, False, {"status": 200, "mimetype": "text/plain", "data": ""}),
        (None, False, False, {"status": 200, "mimetype": "text/plain", "data": ""}),
        (
            None,
            True,
            False,
            {"status": 410, "mimetype": "application/json", "json": {"error": mock.ANY}},
        ),
        (
            None,
            False,
            True,
            {"status": 404, "mimetype": "application/json", "json": {"error": mock.ANY}},
        ),
    ),
)
def test_get_request_logs(
    app, client, db, worker_auth_env, tmpdir, logs_content, stale, finalized, expected
):
    data = {
        "repo": "https://github.com/namespace/project.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["gomod"],
    }
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    request.add_state("in_progress", "Starting things up!")
    db.session.commit()

    client.application.config["CACHITO_REQUEST_FILE_LOGS_DIR"] = str(tmpdir)
    if finalized:
        request.add_state("complete", "The request is complete")
        db.session.commit()
    if stale:
        request.add_state("stale", "The request is stale")
        db.session.commit()
    request_id = request.id
    if logs_content is not None:
        tmpdir.join(f"{request_id}.log").write(logs_content)
    rv = client.get(f"/api/v1/requests/{request_id}/logs")
    assert rv.status_code == expected["status"]
    assert rv.mimetype == expected["mimetype"]
    if "data" in expected:
        assert rv.data.decode("utf-8") == expected["data"]
    if "json" in expected:
        assert rv.json == expected["json"]


def test_get_request_logs_not_configured(app, client, db, worker_auth_env):
    data = {
        "repo": "https://github.com/namespace/project.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["gomod"],
    }
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    request.add_state("in_progress", "Starting things up!")
    db.session.commit()

    client.application.config["CACHITO_REQUEST_FILE_LOGS_DIR"] = None
    request_id = request.id
    rv = client.get(f"/api/v1/requests/{request_id}/logs")
    assert rv.status_code == 404
    assert rv.mimetype == "application/json"
    assert rv.json == {"error": "The requested resource was not found"}

    rv = client.get(f"/api/v1/requests/{request_id}")
    assert rv.status_code == 200
    assert "logs" not in rv.json


@pytest.mark.parametrize(
    "mutually_exclusive, pkg_managers, package_configs, expect_error",
    [
        (
            # npm and yarn have an implicit conflict
            [("npm", "yarn")],
            ["npm", "yarn"],
            {},
            "The following paths cannot be processed by both 'npm' and 'yarn': .",
        ),
        (
            # npm and yarn have an explicit conflict
            [("npm", "yarn")],
            ["npm", "yarn"],
            {"npm": [{"path": "same/path"}], "yarn": [{"path": "same/path"}]},
            "The following paths cannot be processed by both 'npm' and 'yarn': same/path",
        ),
        (
            # npm and yarn have a 1/2 explicit conflict
            [("npm", "yarn")],
            ["npm", "yarn"],
            {"yarn": [{"path": "."}]},
            "The following paths cannot be processed by both 'npm' and 'yarn': .",
        ),
        (
            # npm and yarn have a 1/4 explicit conflict
            [("npm", "yarn")],
            ["npm", "yarn"],
            {"yarn": [{}]},
            "The following paths cannot be processed by both 'npm' and 'yarn': .",
        ),
        (
            # npm and yarn have multiple conflicts
            [("npm", "yarn")],
            ["npm", "yarn"],
            {
                "npm": [{"path": "1"}, {"path": "2"}, {"path": "3"}],
                "yarn": [{"path": "2"}, {"path": "3"}, {"path": "4"}],
            },
            "The following paths cannot be processed by both 'npm' and 'yarn': 2, 3",
        ),
        (
            # npm and yarn do not have a conflict
            [("npm", "yarn")],
            ["npm", "yarn"],
            {"npm": [{"path": "some/path"}], "yarn": [{"path": "other/path"}]},
            None,
        ),
        (
            # npm and yarn do not have a conflict, 1/2 implicitly
            [("npm", "yarn")],
            ["npm", "yarn"],
            {"yarn": [{"path": "not/root"}]},
            None,
        ),
        (
            # gomod and git-submodule have a conflict
            [("gomod", "git-submodule")],
            ["gomod", "git-submodule"],
            {"gomod": [{"path": "not/root"}]},
            "Cannot process non-root packages with 'gomod' when 'git-submodule' is also set",
        ),
        (
            # gomod and git-submodule do not have a conflict, implicitly
            [("gomod", "git-submodule")],
            ["gomod", "git-submodule"],
            {},
            None,
        ),
        (
            # gomod and git-submodule do not have a conflict, explicitly
            [("gomod", "git-submodule")],
            ["gomod", "git-submodule"],
            {"gomod": [{"path": "."}]},
            None,
        ),
        (
            # no mutual exclusivity, no conflict
            [],
            ["npm", "yarn"],
            {"npm": [{"path": "same/path"}], "yarn": [{"path": "same/path"}]},
            None,
        ),
        (
            # mutual exclusivity does not apply, no conflict
            [("npm", "yarn")],
            ["npm", "pip"],
            {"npm": [{"path": "same/path"}], "pip": [{"path": "same/path"}]},
            None,
        ),
        (
            # mutual exclusivity does not apply, no conflict
            [("gomod", "git-submodule")],
            ["pip", "git-submodule"],
            {"pip": [{"path": "not/root"}]},
            None,
        ),
        (
            # pairs can also be 2-item lists (not recommended, but possible)
            [["npm", "yarn"]],
            ["npm", "yarn"],
            {},
            "The following paths cannot be processed by both 'npm' and 'yarn': .",
        ),
    ],
)
@pytest.mark.parametrize("flip_relation", [False, True])
def test_validate_package_manager_exclusivity(
    mutually_exclusive, pkg_managers, package_configs, expect_error, flip_relation
):
    if flip_relation:
        mutually_exclusive = [(b, a) for a, b in mutually_exclusive]

    if expect_error:
        with pytest.raises(ValidationError, match=expect_error):
            _validate_package_manager_exclusivity(pkg_managers, package_configs, mutually_exclusive)
    else:
        _validate_package_manager_exclusivity(pkg_managers, package_configs, mutually_exclusive)


def test_get_content_manifests_by_requests(app, client, db, auth_env, tmpdir):
    # create request objects
    request_data = [
        (
            # request_json, request_state
            {
                "repo": "https://github.com/release-engineering/dummy.git",
                "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
                "pkg_managers": ["npm"],
            },
            RequestStateMapping.complete,
        ),
        (
            {
                "repo": "https://github.com/release-engineering/dummy-foo.git",
                "ref": "450b93a32df1c9d700e3e80996845bc2e13be848",
                "pkg_managers": ["npm"],
            },
            RequestStateMapping.complete,
        ),
        (
            {
                "repo": "https://github.com/release-engineering/dummy-bar.git",
                "ref": "b50b93a32df1c9d700e3e80996845bc2e13be848",
                "pkg_managers": ["npm"],
            },
            RequestStateMapping.in_progress,
        ),
    ]

    requests = []

    for item in request_data:
        request_json = item[0]
        state = item[1]

        with app.test_request_context(environ_base=auth_env):
            request = Request.from_json(request_json)

        request.add_state(state.name, f"Set {state.name} directly for test")
        db.session.add(request)

        requests.append(request)

    db.session.commit()

    # create packages json files
    cachito_bundles_dir = str(tmpdir)
    app.config["CACHITO_BUNDLES_DIR"] = cachito_bundles_dir

    package_data = [
        {"name": "pkg1", "version": "0.1", "type": "npm", "dependencies": []},
        {"name": "pkg2", "version": "0.2", "type": "npm", "dependencies": []},
        {"name": "pkg3", "version": "0.2", "type": "npm", "dependencies": []},
    ]

    for i in range(len(package_data)):
        to_write = [package_data[i]]
        request_id = requests[i].id

        bundle_dir = RequestBundleDir(request_id, root=cachito_bundles_dir)
        _write_test_packages_data(to_write, bundle_dir.packages_data)

    # define api call parameters and corresponding expected result data
    pkg1 = Package.from_json(package_data[0])
    pkg2 = Package.from_json(package_data[1])

    test_data = [
        # requests, expected_image_contents
        ["", []],
        ["requests=", []],
        [
            f"requests={requests[0].id}",
            [{"purl": pkg1.to_top_level_purl(requests[0]), "dependencies": [], "sources": []}],
        ],
        [
            f"requests={requests[0].id},,{requests[1].id}",
            [
                {"purl": pkg1.to_top_level_purl(requests[0]), "dependencies": [], "sources": []},
                {"purl": pkg2.to_top_level_purl(requests[1]), "dependencies": [], "sources": []},
            ],
        ],
        [
            f"requests={requests[2].id}",
            f"Request {requests[2].id} is in state {requests[2].state.state_name}",
        ],
        [
            f"requests={requests[1].id},{requests[2].id}",
            f"Request {requests[2].id} is in state {requests[2].state.state_name}",
        ],
        ["requests=a100", "a100 is not an integer"],
        [
            f"requests={requests[0].id},{requests[1].id + 100}",
            f"Cannot find request(s) {requests[1].id + 100}.",
        ],
    ]

    # run tests
    for requests, expected_image_contents in test_data:
        resp = client.get(f"/api/v1/content-manifest?{requests}")
        if isinstance(expected_image_contents, str):
            assert HTTPStatus.BAD_REQUEST == resp.status_code
            assert expected_image_contents in resp.data.decode()
        else:
            assembled_icm = BASE_ICM.copy()
            assembled_icm["image_contents"] = expected_image_contents
            assert deep_sort_icm(assembled_icm) == json.loads(resp.data)


@pytest.mark.parametrize(
    "querystring,expected_items_count,expected_repos",
    [
        ["repo=https://github.com/org/bar.git", 1, ["https://github.com/org/bar.git"]],
        [
            "repo=",
            3,
            [
                "https://github.com/org/bar.git",
                "https://github.com/org/baz.git",
                "https://github.com/org/foo.git",
            ],
        ],
        ["ref=b50b93a32df1c9d700e3e80996845bc2e13be848", 1, ["https://github.com/org/bar.git"]],
        [
            "ref=",
            3,
            [
                "https://github.com/org/bar.git",
                "https://github.com/org/baz.git",
                "https://github.com/org/foo.git",
            ],
        ],
        ["ref=a-git-ref", None, "a-git-ref is not a valid ref"],
        [
            "pkg_manager=gomod",
            2,
            ["https://github.com/org/foo.git", "https://github.com/org/baz.git"],
        ],
        [
            "pkg_manager=",
            3,
            [
                "https://github.com/org/bar.git",
                "https://github.com/org/baz.git",
                "https://github.com/org/foo.git",
            ],
        ],
        ["pkg_manager=yarn&pkg_manager=gomod", 1, ["https://github.com/org/baz.git"]],
        ["pkg_manager=yarn&pkg_manager=gomod&pkg_manager=", 1, ["https://github.com/org/baz.git"]],
        ["pkg_manager=gomod&repo=https://github.com/org/bar.git", 0, []],
        ["pkg_manager=coolmanager", None, "Cachito does not have package manager coolmanager"],
    ],
)
def test_filter_requests(
    querystring, expected_items_count, expected_repos, app, db, client, worker_auth_env
):
    data = [
        {
            "repo": "https://github.com/org/foo.git",
            "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
            "pkg_managers": ["gomod"],
        },
        {
            "repo": "https://github.com/org/bar.git",
            "ref": "b50b93a32df1c9d700e3e80996845bc2e13be848",
            "pkg_managers": ["pip"],
        },
        {
            "repo": "https://github.com/org/baz.git",
            "ref": "d50b93a32df1c9d700e3e80996845bc2e13be848",
            "pkg_managers": ["gomod", "yarn", "pip"],
        },
    ]
    for item in data:
        with app.test_request_context(environ_base=worker_auth_env):
            request = Request.from_json(item)
        db.session.add(request)
    db.session.commit()

    rv = client.get(f"/api/v1/requests?{querystring}")

    if expected_items_count is None:
        assert HTTPStatus.BAD_REQUEST == rv.status_code
        assert expected_repos in rv.data.decode()
    else:
        result = json.loads(rv.data)
        assert expected_items_count == len(result["items"])
        got_repos = [item["repo"] for item in result["items"]]
        assert sorted(expected_repos) == sorted(got_repos)


def create_request_in_db(app, db, auth_env, state=RequestStateMapping.complete):
    data = {
        "repo": "https://localhost.git/dummy.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["npm"],
    }
    with app.test_request_context(environ_base=auth_env):
        request = Request.from_json(data)
    request.add_state(state.name, "Set complete directly for test")
    db.session.add(request)
    db.session.commit()
    return request


# For writing test easily, these packages are sorted.
resolved_packages = [
    {
        "name": "n2",
        "type": "go-package",
        "version": "v2",
        "dependencies": [
            {"name": "d1", "type": "go-package", "version": "1"},
            {
                "name": "d2",
                "type": "go-package",
                "version": "2",
                "replaces": {"name": "rp", "type": "go-package", "version": "0.1"},
            },
        ],
    },
    {
        "name": "n1",
        "type": "gomod",
        "version": "v1",
        "dependencies": [
            {"name": "d1", "type": "gomod", "version": "1"},
            {"name": "d2", "type": "gomod", "version": "2", "replaces": None},
        ],
    },
    {
        "name": "p1",
        "type": "npm",
        "version": "v2",
        "dependencies": [{"name": "async", "type": "npm", "version": "1.2.0"}],
    },
    {
        "name": "p2",
        "type": "npm",
        "version": "20210621",
        "dependencies": [
            {"name": "async", "type": "npm", "version": "1.2.0"},
            {"name": "underscore", "type": "npm", "version": "1.13.0"},
        ],
    },
]

expected_dependencies = [
    {"name": "d1", "type": "go-package", "version": "1", "replaces": None},
    {
        "name": "d2",
        "type": "go-package",
        "version": "2",
        "replaces": {"name": "rp", "type": "go-package", "version": "0.1"},
    },
    {"name": "d1", "type": "gomod", "version": "1", "replaces": None},
    {"name": "d2", "type": "gomod", "version": "2", "replaces": None},
    # Only one async in the final dependencies list
    {"name": "async", "type": "npm", "version": "1.2.0", "replaces": None},
    {"name": "underscore", "type": "npm", "version": "1.13.0", "replaces": None},
]


@pytest.mark.parametrize(
    "packages,expected_deps",
    [
        [None, []],  # do not create the packages.json
        [[], []],
        [copy.deepcopy(resolved_packages), copy.deepcopy(expected_dependencies)],
    ],
)
def test_fetch_request_packages_and_dependencies(
    packages, expected_deps, app, db, client, auth_env, tmpdir
):
    """Test fetch a request with correct packages and dependencies read from packages data file."""
    request = create_request_in_db(app, db, auth_env)

    cachito_bundles_dir = str(tmpdir)
    app.config["CACHITO_BUNDLES_DIR"] = cachito_bundles_dir

    if packages is not None:
        _write_test_packages_data(
            packages, RequestBundleDir(request.id, root=cachito_bundles_dir).packages_data,
        )

    rv = client.get(f"/api/v1/requests/{request.id}")

    response_data = json.loads(rv.data)
    if packages is not None:
        for dep in (pkg_dep for pkg in packages for pkg_dep in pkg["dependencies"]):
            dep.setdefault("replaces", None)
    assert response_data["packages"] == ([] if packages is None else packages)
    assert response_data["dependencies"] == expected_deps


@pytest.mark.parametrize("verbose", [True, False])
def test_fetch_requests_packages_and_dependencies(verbose, app, db, client, auth_env, tmpdir):
    """Test packages and dependencies inside the fetched requests."""
    request = create_request_in_db(app, db, auth_env)

    packages = copy.deepcopy(resolved_packages)
    expected_deps = copy.deepcopy(expected_dependencies)

    # Since the tasks do not run asynchronously, set the number of packages
    # and dependencies manually for this test.
    request.packages_count = len(packages)
    request.dependencies_count = len(expected_deps)
    db.session.commit()

    cachito_bundles_dir = str(tmpdir)
    bundle_dir = RequestBundleDir(request.id, root=cachito_bundles_dir)
    app.config["CACHITO_BUNDLES_DIR"] = cachito_bundles_dir

    _write_test_packages_data(resolved_packages, bundle_dir.packages_data)

    rv = client.get(f"/api/v1/requests?verbose={str(verbose).lower()}")

    response_data = json.loads(rv.data)
    for package in response_data["items"]:
        if verbose:
            for dep in (pkg_dep for pkg in packages for pkg_dep in pkg["dependencies"]):
                dep.setdefault("replaces", None)
            assert package["packages"] == packages
            assert package["dependencies"] == expected_deps
        else:
            assert package["packages"] == len(packages)
            assert package["dependencies"] == len(expected_deps)


def test_fetch_packages_file(app, db, client, auth_env, tmpdir):
    request = create_request_in_db(app, db, auth_env)
    db.session.commit()

    packages = copy.deepcopy(resolved_packages)
    expected_deps = copy.deepcopy(expected_dependencies)

    cachito_bundles_dir = str(tmpdir)
    bundle_dir = RequestBundleDir(request.id, root=cachito_bundles_dir)
    app.config["CACHITO_BUNDLES_DIR"] = cachito_bundles_dir

    _write_test_packages_data(resolved_packages, bundle_dir.packages_data)

    rv = client.get(f"/api/v1/requests/{request.id}/packages")

    response_data = json.loads(rv.data)

    for dep in response_data["dependencies"]:
        dep.setdefault("replaces", None)

    assert response_data["packages"] == packages
    assert response_data["dependencies"] == expected_deps


@pytest.mark.parametrize(
    "state,expected_status",
    [
        [RequestStateMapping.complete, 500],
        [RequestStateMapping.in_progress, 404],
        [RequestStateMapping.failed, 404],
        [RequestStateMapping.stale, 404],
    ],
)
def test_fetch_missing_packages_file(app, db, client, auth_env, state, expected_status):
    request = create_request_in_db(app, db, auth_env, state)

    rv = client.get(f"/api/v1/requests/{request.id}/packages")

    assert rv.status_code == expected_status
