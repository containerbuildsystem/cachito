# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os.path
import re
import tempfile
from http import HTTPStatus
from unittest import mock

import flask
import kombu.exceptions
import pytest

from cachito.errors import CachitoError, ValidationError
from cachito.web.content_manifest import BASE_ICM, PARENT_PURL_PLACEHOLDER
from cachito.web.models import (
    ConfigFileBase64,
    EnvironmentVariable,
    Flag,
    Package,
    Request,
    RequestPackage,
    RequestStateMapping,
    _validate_package_manager_exclusivity,
)
from cachito.web.utils import deep_sort_icm
from cachito.workers.paths import RequestBundleDir
from cachito.workers.tasks import (
    fetch_app_source,
    fetch_gomod_source,
    fetch_npm_source,
    fetch_pip_source,
    fetch_yarn_source,
    failed_request_callback,
    create_bundle_archive,
    add_git_submodules_as_package,
)

RE_INVALID_PACKAGES_VALUE = (
    r'The value of "packages.\w+" must be an array of objects with the following keys: \w+(, \w+)*'
)


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

    created_request["pkg_managers"] == expected_pkg_managers

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
    expected.append(create_bundle_archive.si(created_request["id"]).on_error(error_callback))
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
def test_create_and_fetch_request_gomod_package_configs(
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
        create_bundle_archive.si(1).on_error(error_callback),
    ]
    mock_chain.assert_called_once_with(expected)


@mock.patch("cachito.web.api_v1.chain")
def test_create_and_fetch_request_npm_package_configs(
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
        create_bundle_archive.si(1).on_error(error_callback),
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
def test_create_and_fetch_request_pip_package_configs(
    mock_chain, app, auth_env, client, db, pkg_value
):
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
        create_bundle_archive.si(1).on_error(error_callback),
    ]
    mock_chain.assert_called_once_with(expected)


@mock.patch("cachito.web.api_v1.chain")
def test_create_and_fetch_request_yarn_package_configs(
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
        create_bundle_archive.si(1).on_error(error_callback),
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
            create_bundle_archive.si(1).on_error(error_callback),
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
    app, auth_env, client, db, sample_deps_replace, sample_package, worker_auth_env
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
            db.session.add(request)
    db.session.commit()

    # Endpoint /requests/ returns requests in descending order of id.

    payload = {"dependencies": sample_deps_replace, "package": sample_package}
    client.patch(
        f"/api/v1/requests/{sample_requests_count}", json=payload, environ_base=worker_auth_env
    )
    client.patch("/api/v1/requests/40", json=payload, environ_base=worker_auth_env)

    # Sane defaults are provided
    rv = client.get("/api/v1/requests")
    assert rv.status_code == 200
    response = rv.json
    fetched_requests = response["items"]
    assert len(fetched_requests) == 10
    for i, request in enumerate(fetched_requests, 1):
        assert request["repo"] == repo_template.format(sample_requests_count - i)
    assert response["meta"]["previous"] is None
    assert fetched_requests[0]["dependencies"] == 14
    assert fetched_requests[0]["packages"] == 1

    # Invalid per_page defaults to 10
    rv = client.get("/api/v1/requests?per_page=tom_hanks")
    assert len(rv.json["items"]) == 10
    assert response["meta"]["per_page"] == 10

    # per_page and page parameters are honored
    rv = client.get("/api/v1/requests?page=3&per_page=5&verbose=True&state=in_progress")
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
        assert "state=in_progress" in pagination_metadata[page]
    assert pagination_metadata["total"] == sample_requests_count
    assert len(fetched_requests[0]["dependencies"]) == 14
    assert len(fetched_requests[0]["packages"]) == 1
    assert type(fetched_requests[0]["dependencies"]) == list


def test_fetch_request_multiple_packages(app, auth_env, client, db, worker_auth_env):
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=auth_env):
        data = {
            "repo": "https://github.com/release-engineering/console-ui.git",
            "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
            "pkg_managers": ["npm"],
        }
        request = Request.from_json(data)
        db.session.add(request)
    db.session.commit()

    payload = {
        "dependencies": [
            {"dev": True, "name": "rxjs", "replaces": None, "type": "npm", "version": "6.5.5"},
            {
                "dev": True,
                "name": "safe-regex",
                "replaces": None,
                "type": "npm",
                "version": "1.1.0",
            },
        ],
        "package": {"name": "client", "type": "npm", "version": "1.0.0"},
    }
    client.patch("/api/v1/requests/1", json=payload, environ_base=worker_auth_env)
    payload = {
        "dependencies": [
            {"dev": True, "name": "rxjs", "replaces": None, "type": "npm", "version": "6.5.5"},
            {"dev": False, "name": "react", "replaces": None, "type": "npm", "version": "16.13.1"},
        ],
        "package": {"name": "proxy", "type": "npm", "version": "1.0.0"},
    }
    client.patch("/api/v1/requests/1", json=payload, environ_base=worker_auth_env)

    # Test the request in the non-verbose format
    rv = client.get("/api/v1/requests")
    assert rv.status_code == 200
    assert rv.json["items"][0]["dependencies"] == 3
    assert rv.json["items"][0]["packages"] == 2

    # Test the request in the verbose format
    rv = client.get("/api/v1/requests/1")
    assert rv.status_code == 200
    assert len(rv.json["dependencies"]) == 3
    assert len(rv.json["packages"][0]["dependencies"]) == 2
    assert len(rv.json["packages"][1]["dependencies"]) == 2


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


@mock.patch("pathlib.Path.exists")
@mock.patch("cachito.web.api_v1.Request")
def test_download_archive(mock_request, mock_exists, client, app):
    request_id = 1
    request = mock.Mock(id=request_id)
    request.state.state_name = "complete"
    mock_request.query.get_or_404.return_value = request
    mock_exists.return_value = True

    with tempfile.TemporaryDirectory() as temp_dir:
        with open(os.path.join(temp_dir, "1.tar.gz"), "w") as f:
            f.write("hello")
        with mock.patch.dict(flask.current_app.config, values={"CACHITO_BUNDLES_DIR": temp_dir}):
            resp = client.get(f"/api/v1/requests/{request_id}/download")
            assert "hello" == resp.data.decode()
            assert "attachment; filename=cachito-1.tar.gz" == resp.headers["Content-Disposition"]


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


@pytest.mark.parametrize("state", ("complete", "failed"))
@mock.patch("pathlib.Path.exists")
@mock.patch("shutil.rmtree")
def test_set_state(mock_rmtree, mock_exists, state, app, client, db, worker_auth_env):
    mock_exists.return_value = True
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
    mock_exists.assert_called_once()
    mock_rmtree.assert_called_once_with(str(RequestBundleDir(request_id)))


@pytest.mark.parametrize("bundle_exists", (True, False))
@pytest.mark.parametrize("pkg_managers", (["gomod"], ["npm"], ["gomod", "npm"]))
@mock.patch("pathlib.Path.exists")
@mock.patch("pathlib.Path.unlink")
@mock.patch("cachito.web.api_v1.tasks.cleanup_npm_request")
def test_set_state_stale(
    mock_cleanup_npm,
    mock_remove,
    mock_exists,
    pkg_managers,
    bundle_exists,
    app,
    client,
    db,
    worker_auth_env,
):
    mock_exists.return_value = bundle_exists
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

    state = "stale"
    state_reason = "The request has expired"
    payload = {"state": state, "state_reason": state_reason}
    patch_rv = client.patch("/api/v1/requests/1", json=payload, environ_base=worker_auth_env)
    assert patch_rv.status_code == 200

    get_rv = client.get("/api/v1/requests/1")
    assert get_rv.status_code == 200

    fetched_request = get_rv.get_json()
    assert fetched_request["state"] == state
    assert fetched_request["state_reason"] == state_reason
    if bundle_exists:
        mock_remove.assert_called_once_with()
    else:
        mock_remove.assert_not_called()
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


def test_set_state_from_stale(app, client, db, worker_auth_env):
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
    request.add_state("stale", "The request has expired")
    db.session.commit()

    payload = {"state": "complete", "state_reason": "Unexpired"}
    patch_rv = client.patch("/api/v1/requests/1", json=payload, environ_base=worker_auth_env)
    assert patch_rv.status_code == 400
    assert patch_rv.get_json() == {"error": "A stale request cannot change states"}


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


def test_set_deps(app, client, db, worker_auth_env, sample_deps_replace, sample_package):
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

    # Test a dependency with no "replaces" key
    sample_deps_replace.insert(
        0, {"name": "all_systems_go", "type": "gomod", "version": "v1.0.0"},
    )
    payload = {
        "dependencies": sample_deps_replace,
        "package": sample_package,
    }
    patch_rv = client.patch("/api/v1/requests/1", json=payload, environ_base=worker_auth_env)
    assert patch_rv.status_code == 200

    get_rv = client.get("/api/v1/requests/1")
    assert get_rv.status_code == 200
    fetched_request = get_rv.json

    sample_deps_replace[0]["replaces"] = None
    assert fetched_request["dependencies"] == sample_deps_replace
    sample_package["dependencies"] = sample_deps_replace
    assert fetched_request["packages"] == [sample_package]


def test_set_deps_with_dev(app, client, db, worker_auth_env):
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

    dep = {
        "name": "@angular-devkit/build-angular",
        "dev": True,
        "type": "npm",
        "version": "0.803.26",
    }
    payload = {
        "dependencies": [dep],
        "package": {"name": "han-solo", "type": "npm", "version": "5.0.0"},
    }
    patch_rv = client.patch("/api/v1/requests/1", json=payload, environ_base=worker_auth_env)
    assert patch_rv.status_code == 200

    get_rv = client.get("/api/v1/requests/1")
    assert get_rv.status_code == 200

    dep["replaces"] = None
    assert get_rv.json["dependencies"] == [dep]


def test_add_dep_twice_diff_replaces(app, client, db, worker_auth_env):
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

    payload = {
        "dependencies": [{"name": "all_systems_go", "type": "gomod", "version": "v1.0.0"}],
        "package": {"name": "retrodep", "type": "gomod", "version": "v1.0.0"},
    }
    patch_rv = client.patch("/api/v1/requests/1", json=payload, environ_base=worker_auth_env)
    assert patch_rv.status_code == 200

    # Add the dependency again with replaces set this time
    payload2 = {
        "dependencies": [
            {
                "name": "all_systems_go",
                "type": "gomod",
                "replaces": {"name": "all_systems_go", "type": "gomod", "version": "v1.1.0"},
                "version": "v1.0.0",
            }
        ],
        "package": {"name": "retrodep", "type": "gomod", "version": "v1.0.0"},
    }

    patch_rv = client.patch("/api/v1/requests/1", json=payload2, environ_base=worker_auth_env)
    assert patch_rv.status_code == 400
    assert "can't have a new replacement set" in patch_rv.json["error"]


@pytest.mark.parametrize(
    "package_subpath, subpath_in_db", [(None, None), (".", None), ("some/path", "some/path")]
)
def test_set_package(
    package_subpath, subpath_in_db, app, client, db, sample_package, worker_auth_env
):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    payload = {"package": sample_package}
    if package_subpath is not None:
        payload["package_subpath"] = package_subpath

    patch_rv = client.patch("/api/v1/requests/1", json=payload, environ_base=worker_auth_env)
    assert patch_rv.status_code == 200

    request_package = RequestPackage.query.filter_by(request_id=1).first()
    assert request_package
    assert request_package.subpath == subpath_in_db

    get_rv = client.get("/api/v1/requests/1")
    assert get_rv.status_code == 200
    sample_package["dependencies"] = []
    if subpath_in_db is not None:
        sample_package["path"] = subpath_in_db
    assert get_rv.json["packages"] == [sample_package]


@pytest.mark.parametrize(
    "subpath_1, subpath_2, is_conflict",
    [
        ("some/path", "some/path", False),
        # "" and "." are stored as null, so the following are not conflicts
        (None, "", False),
        (None, ".", False),
        ("", ".", False),
        (".", "", False),
        # If subpath is not part of payload, there is never a conflict
        (None, None, False),
        (".", None, False),
        ("some/path", None, False),
        # Different subpath is a conflict
        (None, "some/path", True),
        (".", "some/path", True),
        ("some/path", ".", True),
        ("some/path", "some/other/path", True),
    ],
)
def test_set_package_subpath_conflict(
    subpath_1, subpath_2, is_conflict, app, client, db, sample_package, worker_auth_env
):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    payload_1 = {"package": sample_package}
    if subpath_1 is not None:
        payload_1["package_subpath"] = subpath_1

    rv_1 = client.patch("/api/v1/requests/1", json=payload_1, environ_base=worker_auth_env)
    assert rv_1.status_code == 200

    payload_2 = {"package": sample_package}
    if subpath_2 is not None:
        payload_2["package_subpath"] = subpath_2

    rv_2 = client.patch("/api/v1/requests/1", json=payload_2, environ_base=worker_auth_env)

    def normalize(subpath):
        if not subpath or subpath == ".":
            return None
        return subpath

    if is_conflict:
        assert rv_2.status_code == 400
        assert rv_2.json == {
            "error": (
                f"Cannot change subpath for package {sample_package!r} "
                f"(from: {normalize(subpath_1)!r}, to: {normalize(subpath_2)!r})"
            )
        }
    else:
        assert rv_2.status_code == 200
        # Check that subpath was not modified
        request_package = RequestPackage.query.filter_by(request_id=1).first()
        assert request_package
        assert request_package.subpath == normalize(subpath_1)


def test_set_state_not_logged_in(client, db):
    payload = {"state": "complete", "state_reason": "Completed successfully"}
    rv = client.patch("/api/v1/requests/1", json=payload)
    assert rv.status_code == 401
    assert rv.json["error"] == (
        "The server could not verify that you are authorized to access the URL requested. You "
        "either supplied the wrong credentials (e.g. a bad password), or your browser doesn't "
        "understand how to supply the credentials required."
    )


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
        (1, {"dependencies": "test"}, 400, 'The value for "dependencies" must be an array'),
        (
            1,
            {
                "dependencies": ["test"],
                "package": {"name": "han-solo", "type": "npm", "version": "5.0.0"},
            },
            400,
            (
                "A dependency must be a JSON object with the following keys: name, type, version. "
                "It may also contain the following optional keys if applicable: dev, replaces."
            ),
        ),
        (
            1,
            {
                "dependencies": [
                    {"name": "pizza", "type": "gomod", "replaces": "bad", "version": "v1.4.2"}
                ],
                "package": {"name": "han-solo", "type": "gomod", "version": "5.0.0"},
            },
            400,
            "A dependency must be a JSON object with the following keys: name, type, version. "
            "It may also contain the following optional keys if applicable: dev.",
        ),
        (
            1,
            {
                "dependencies": [{"type": "gomod", "version": "v1.4.2"}],
                "package": {"name": "han-solo", "type": "gomod", "version": "5.0.0"},
            },
            400,
            (
                "A dependency must be a JSON object with the following keys: name, type, version. "
                "It may also contain the following optional keys if applicable: dev, replaces."
            ),
        ),
        (
            1,
            {"package": {"type": "gomod", "version": "v1.4.2"}},
            400,
            "A package must be a JSON object with the following keys: name, type, version.",
        ),
        (
            1,
            {
                "package": {
                    "name": "github.com/release-engineering/retrodep/v2",
                    "type": "gomod",
                    "version": 3,
                }
            },
            400,
            'The "version" key of the package must be a string',
        ),
        (
            1,
            {
                "dependencies": [
                    {"name": "github.com/Masterminds/semver", "type": "gomod", "version": 3.0}
                ],
                "package": {"name": "han-solo", "type": "gomod", "version": "5.0.0"},
            },
            400,
            'The "version" key of the dependency must be a string',
        ),
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
        (
            1,
            {
                "dependencies": [
                    {
                        "dev": True,
                        "name": "github.com/Masterminds/semver",
                        "type": "gomod",
                        "version": "v3.0.0",
                    }
                ],
                "package": {"name": "han-solo", "type": "gomod", "version": "5.0.0"},
            },
            400,
            'The "dev" key is not supported on the package manager gomod',
        ),
        (
            1,
            {
                "dependencies": [
                    {
                        "dev": 123,
                        "name": "@angular-devkit/build-angular",
                        "type": "npm",
                        "version": "0.803.26",
                    }
                ],
                "package": {"name": "han-solo", "type": "npm", "version": "5.0.0"},
            },
            400,
            'The "dev" key of the dependency must be a boolean',
        ),
        (
            1,
            {
                "dependencies": [
                    {"name": "@angular-devkit/build-angular", "type": "npm", "version": "0.803.26"}
                ],
            },
            400,
            'The "package" object must also be provided if the "dependencies" array is provided',
        ),
        (1, {"package_subpath": None}, 400, 'The value for "package_subpath" must be a string'),
        (
            1,
            {"package_subpath": "some/path"},
            400,
            'The "package" object must also be provided if "package_subpath" is provided',
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
def test_fetch_request_content_manifest_go(
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

    # emulate worker
    payload = {"dependencies": sample_deps, "package": sample_package}
    client.patch("/api/v1/requests/1", json=payload, environ_base=worker_auth_env)
    payload = {"dependencies": sample_pkg_deps, "package": sample_pkg_lvl_pkg}
    client.patch("/api/v1/requests/1", json=payload, environ_base=worker_auth_env)

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
def test_fetch_request_content_manifest_npm_or_pip(
    app, client, db, auth_env, worker_auth_env, state, pkg_manager, purl_type
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
    payload = {
        "dependencies": deps[:2],
        "package": pkgs[0],
    }
    client.patch("/api/v1/requests/1", json=payload, environ_base=worker_auth_env)
    payload = {
        "dependencies": deps[2:],
        "package": pkgs[1],
    }
    client.patch("/api/v1/requests/1", json=payload, environ_base=worker_auth_env)

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

    EnvironmentVariable.query.count() == len(env_vars_expected)
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


@pytest.mark.parametrize("flag", [True, False])
@mock.patch("cachito.web.api_v1.chain")
def test_create_and_fetch_request_with_pip_preview(
    mock_chain, app, auth_env, client, db, flag,
):
    db.session.commit()
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["pip"],
    }
    if flag:
        data["flags"] = ["pip-dev-preview"]

    rv = client.post("/api/v1/requests", json=data, environ_base=auth_env)
    if flag:
        assert rv.status_code == 400
        assert rv.json == {"error": "Invalid/Inactive flag(s): pip-dev-preview"}
    else:
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
                    False,  # default value for gitsubmodule
                ).on_error(error_callback),
                fetch_pip_source.si(1, []).on_error(error_callback),
                create_bundle_archive.si(1).on_error(error_callback),
            ]
        )
        request_id = created_request["id"]
        rv = client.get("/api/v1/requests/{}".format(request_id))
        assert rv.status_code == 200
        fetched_request = rv.json
        assert fetched_request["state"] == "in_progress"
        assert fetched_request["state_reason"] == "The request was initiated"


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


def test_get_content_manifests_by_requests(app, client, db, auth_env):
    data = {
        "repo": "https://github.com/release-engineering/dummy.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["npm"],
    }
    with app.test_request_context(environ_base=auth_env):
        request1 = Request.from_json(data)
    request1.add_state(RequestStateMapping.complete.name, "Set complete directly for test")
    db.session.add(request1)

    data = {
        "repo": "https://github.com/release-engineering/dummy-foo.git",
        "ref": "450b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["npm"],
    }
    with app.test_request_context(environ_base=auth_env):
        request2 = Request.from_json(data)
    request2.add_state(RequestStateMapping.complete.name, "Set complete directly for test")
    db.session.add(request1)

    data = {
        "repo": "https://github.com/release-engineering/dummy-bar.git",
        "ref": "b50b93a32df1c9d700e3e80996845bc2e13be848",
        "pkg_managers": ["npm"],
    }
    with app.test_request_context(environ_base=auth_env):
        request3 = Request.from_json(data)
    request3.add_state(RequestStateMapping.in_progress.name, "Set in_progress directly for test")
    db.session.add(request3)

    pkg1 = Package(name="pkg1", version="0.1", type="npm")
    pkg2 = Package(name="pkg2", version="0.2", type="npm")
    pkg3 = Package(name="pkg3", version="0.2", type="npm")
    db.session.add(pkg1)
    db.session.add(pkg2)
    db.session.add(pkg3)

    request1.add_package(pkg1)
    request2.add_package(pkg2)
    request3.add_package(pkg3)

    db.session.commit()

    test_data = [
        # requests, expected_image_contents
        ["", []],
        ["requests=", []],
        [
            f"requests={request1.id}",
            [{"purl": pkg1.to_top_level_purl(request1), "dependencies": [], "sources": []}],
        ],
        [
            f"requests={request1.id},,{request2.id}",
            [
                {"purl": pkg1.to_top_level_purl(request1), "dependencies": [], "sources": []},
                {"purl": pkg2.to_top_level_purl(request2), "dependencies": [], "sources": []},
            ],
        ],
        [
            f"requests={request3.id}",
            f"Request {request3.id} is in state {request3.state.state_name}",
        ],
        [
            f"requests={request1.id},{request3.id}",
            f"Request {request3.id} is in state {request3.state.state_name}",
        ],
        ["requests=a100", "a100 is not an integer"],
        [
            f"requests={request1.id},{request2.id + 100}",
            f"Cannot find request(s) {request2.id + 100}.",
        ],
    ]

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
