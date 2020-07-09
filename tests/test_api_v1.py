# SPDX-License-Identifier: GPL-3.0-or-later
import os.path
import tempfile
from unittest import mock

import flask
import kombu.exceptions
import pytest

from cachito.web.models import (
    ConfigFileBase64,
    EnvironmentVariable,
    Flag,
    Request,
    RequestStateMapping,
)
from cachito.workers.paths import RequestBundleDir
from cachito.workers.tasks import (
    fetch_app_source,
    fetch_gomod_source,
    fetch_npm_source,
    set_request_state,
    failed_request_callback,
    create_bundle_archive,
)


@pytest.mark.parametrize(
    "dependency_replacements, pkg_managers, user, expected_pkg_managers",
    (
        ([], [], None, []),
        ([], ["gomod"], None, ["gomod"]),
        (
            [{"name": "github.com/pkg/errors", "type": "gomod", "version": "v0.8.1"}],
            ["gomod"],
            None,
            ["gomod"],
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
            ["gomod"],
            None,
            ["gomod"],
        ),
        ([], [], "tom_hanks@DOMAIN.LOCAL", []),
        ([], ["npm"], None, ["npm"]),
    ),
)
@mock.patch("cachito.web.api_v1.chain")
def test_create_and_fetch_request(
    mock_chain,
    dependency_replacements,
    pkg_managers,
    user,
    expected_pkg_managers,
    app,
    auth_env,
    client,
    db,
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
        ).on_error(error_callback)
    ]
    if "gomod" in expected_pkg_managers:
        expected.append(
            fetch_gomod_source.si(created_request["id"], dependency_replacements).on_error(
                error_callback
            )
        )
    if "npm" in expected_pkg_managers:
        expected.append(fetch_npm_source.si(created_request["id"], {}).on_error(error_callback))
    expected.extend(
        [
            create_bundle_archive.si(created_request["id"]).on_error(error_callback),
            set_request_state.si(created_request["id"], "complete", "Completed successfully"),
        ]
    )
    mock_chain.assert_called_once_with(expected)

    request_id = created_request["id"]
    rv = client.get("/api/v1/requests/{}".format(request_id))
    assert rv.status_code == 200
    fetched_request = rv.json

    assert created_request == fetched_request
    assert fetched_request["state"] == "in_progress"
    assert fetched_request["state_reason"] == "The request was initiated"


@mock.patch("cachito.web.api_v1.chain")
def test_create_and_fetch_request_package_configs(
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
        ).on_error(error_callback),
        fetch_npm_source.si(1, package_value["npm"]).on_error(error_callback),
        create_bundle_archive.si(1).on_error(error_callback),
        set_request_state.si(1, "complete", "Completed successfully"),
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
            ).on_error(error_callback),
            fetch_gomod_source.si(1, []).on_error(error_callback),
            create_bundle_archive.si(1).on_error(error_callback),
            set_request_state.si(1, "complete", "Completed successfully"),
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
    assert len(fetched_requests) == 20
    for i, request in enumerate(fetched_requests, 1):
        assert request["repo"] == repo_template.format(sample_requests_count - i)
    assert response["meta"]["previous"] is None
    assert fetched_requests[0]["dependencies"] == 14
    assert fetched_requests[0]["packages"] == 1

    # per_page and page parameters are honored
    rv = client.get("/api/v1/requests?page=2&per_page=10&verbose=True&state=in_progress")
    assert rv.status_code == 200
    response = rv.json
    fetched_requests = response["items"]
    assert len(fetched_requests) == 10
    # Start at 10 because each page contains 10 items and we're processing the second page
    for i, request in enumerate(fetched_requests, 1):
        assert request["repo"] == repo_template.format(sample_requests_count - 10 - i)
    pagination_metadata = response["meta"]
    for page, page_num in [("next", 3), ("last", 5), ("previous", 1), ("first", 1)]:
        assert f"page={page_num}" in pagination_metadata[page]
        assert "per_page=10" in pagination_metadata[page]
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
    client.patch(f"/api/v1/requests/1", json=payload, environ_base=worker_auth_env)
    payload = {
        "dependencies": [
            {"dev": True, "name": "rxjs", "replaces": None, "type": "npm", "version": "6.5.5"},
            {"dev": False, "name": "react", "replaces": None, "type": "npm", "version": "16.13.1"},
        ],
        "package": {"name": "proxy", "type": "npm", "version": "1.0.0"},
    }
    client.patch(f"/api/v1/requests/1", json=payload, environ_base=worker_auth_env)

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
    "dependency_replacements, error_msg",
    (
        (
            ["mypackage"],
            "A dependency replacement must be a JSON object with the following keys: name, type, "
            "version. It may also contain the following optional keys: new_name.",
        ),
        ("mypackage", '"dependency_replacements" must be an array'),
        (
            [{"name": "rxjs", "type": "npm", "version": "6.5.5"}],
            "Dependency replacements are not yet supported for the npm package manager",
        ),
    ),
)
def test_create_request_invalid_dependency_replacement(
    dependency_replacements, error_msg, auth_env, client, db
):
    data = {
        "repo": "https://github.com/release-engineering/retrodep.git",
        "ref": "c50b93a32df1c9d700e3e80996845bc2e13be848",
        "dependency_replacements": dependency_replacements,
        "pkg_managers": ["npm"],
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
        (
            {"gomod": [{"path": "client"}]},
            ["gomod"],
            'The following package managers in the "packages" object are unsupported: gomod',
        ),
        (
            {"npm": {"path": "client"}},
            ["npm"],
            'The value of "packages.npm" must be an array of objects with the following keys: path',
        ),
        (
            {"npm": ["path"]},
            ["npm"],
            'The value of "packages.npm" must be an array of objects with the following keys: path',
        ),
        (
            {"npm": [{}]},
            ["npm"],
            'The value of "packages.npm" must be an array of objects with the following keys: path',
        ),
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
    assert rv.json["error"] == error_msg


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

    assert rv.status_code == 503
    assert rv.json == {"error": "Failed to schedule the task to the workers. Please try again."}
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


def test_set_package(app, client, db, sample_package, worker_auth_env):
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
    patch_rv = client.patch("/api/v1/requests/1", json=payload, environ_base=worker_auth_env)
    assert patch_rv.status_code == 200

    get_rv = client.get("/api/v1/requests/1")
    assert get_rv.status_code == 200
    sample_package["dependencies"] = []
    assert get_rv.json["packages"] == [sample_package]


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
    db.session.add(request)
    db.session.commit()

    rv = client.get("/api/v1/requests/1/content-manifest")

    expected = {
        "metadata": {"icm_version": 1, "icm_spec": json_schema_url, "image_layer_index": -1},
    }

    assert rv.json == expected


def test_request_fetch_request_content_manifest_invalid(client, worker_auth_env):
    rv = client.get("/api/v1/requests/2/content-manifest")

    assert rv.status_code == 404
    assert rv.json == {"error": "The requested resource was not found"}


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
