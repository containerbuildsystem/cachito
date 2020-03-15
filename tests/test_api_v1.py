# SPDX-License-Identifier: GPL-3.0-or-later
from unittest import mock

import kombu.exceptions
import pytest

from cachito.web.models import Request, EnvironmentVariable, Flag, RequestStateMapping
from cachito.workers.paths import RequestBundleDir
from cachito.workers.tasks import (
    fetch_app_source, fetch_gomod_source, set_request_state, failed_request_callback,
    create_bundle_archive,
)


@pytest.mark.parametrize('dependency_replacements, pkg_managers, user', (
    ([], [], None),
    ([], ['gomod'], None),
    ([{'name': 'github.com/pkg/errors', 'type': 'gomod', 'version': 'v0.8.1'}], ['gomod'], None),
    (
        [{
            'name': 'github.com/pkg/errors',
            'new_name': 'github.com/pkg_new_errors',
            'type': 'gomod',
            'version': 'v0.8.1'
        }],
        ['gomod'],
        None,
    ),
    ([], [], 'tom_hanks@DOMAIN.LOCAL'),
))
@mock.patch('cachito.web.api_v1.chain')
def test_create_and_fetch_request(
    mock_chain, dependency_replacements, pkg_managers, user, app, auth_env, client, db,
):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': pkg_managers,
    }

    if dependency_replacements:
        data['dependency_replacements'] = dependency_replacements
    if user:
        data['user'] = user

    rv = client.post('/api/v1/requests', json=data, environ_base=auth_env)
    assert rv.status_code == 201
    created_request = rv.json

    for key, expected_value in data.items():
        # dependency_replacements aren't directly shown in the REST API
        if key == 'dependency_replacements':
            continue
        else:
            assert expected_value == created_request[key]

    if user:
        assert created_request['user'] == 'tom_hanks@DOMAIN.LOCAL'
        assert created_request['submitted_by'] == 'tbrady@DOMAIN.LOCAL'
    else:
        assert created_request['user'] == 'tbrady@DOMAIN.LOCAL'
        assert created_request['submitted_by'] is None

    error_callback = failed_request_callback.s(1)
    auto_detect = len(pkg_managers) == 0
    mock_chain.assert_called_once_with([
        fetch_app_source.s(
            'https://github.com/release-engineering/retrodep.git',
            'c50b93a32df1c9d700e3e80996845bc2e13be848',
            1,
        ).on_error(error_callback),
        fetch_gomod_source.si(1, auto_detect, dependency_replacements).on_error(error_callback),
        create_bundle_archive.si(1).on_error(error_callback),
        set_request_state.si(1, 'complete', 'Completed successfully'),
    ])

    request_id = created_request['id']
    rv = client.get('/api/v1/requests/{}'.format(request_id))
    assert rv.status_code == 200
    fetched_request = rv.json

    assert created_request == fetched_request
    assert fetched_request['state'] == 'in_progress'
    assert fetched_request['state_reason'] == 'The request was initiated'


@mock.patch('cachito.web.api_v1.chain')
def test_create_request_ssl_auth(mock_chain, auth_ssl_env, client, db):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
    }

    rv = client.post('/api/v1/requests', json=data, environ_base=auth_ssl_env)
    assert rv.status_code == 201
    created_request = rv.json

    cert_dn = 'CN=tbrady,OU=serviceusers,DC=domain,DC=local'
    assert created_request['user'] == cert_dn


@mock.patch('cachito.web.api_v1.chain')
def test_create_and_fetch_request_with_flag(mock_chain, app, auth_env, client, db):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod'],
        'flags': ['valid_flag']
    }

    # Add a new active flag to db
    flag = Flag.from_json('valid_flag')
    db.session.add(flag)
    db.session.commit()

    rv = client.post('/api/v1/requests', json=data, environ_base=auth_env)
    assert rv.status_code == 201
    created_request = rv.json
    for key, expected_value in data.items():
        assert expected_value == created_request[key]
    assert created_request['user'] == 'tbrady@DOMAIN.LOCAL'

    error_callback = failed_request_callback.s(1)
    mock_chain.assert_called_once_with([
        fetch_app_source.s(
            'https://github.com/release-engineering/retrodep.git',
            'c50b93a32df1c9d700e3e80996845bc2e13be848',
            1,
        ).on_error(error_callback),
        fetch_gomod_source.si(1, False, []).on_error(error_callback),
        create_bundle_archive.si(1).on_error(error_callback),
        set_request_state.si(1, 'complete', 'Completed successfully'),
    ])

    # Set the flag as inactive
    flag = Flag.query.filter_by(name='valid_flag').first()
    flag.active = False
    db.session.commit()

    request_id = created_request['id']
    rv = client.get('/api/v1/requests/{}'.format(request_id))
    assert rv.status_code == 200
    fetched_request = rv.json

    # The flag should be present even if it is inactive now
    assert fetched_request['flags'] == ['valid_flag']
    assert fetched_request['state'] == 'in_progress'
    assert fetched_request['state_reason'] == 'The request was initiated'


@mock.patch('cachito.web.api_v1.chain')
def test_fetch_paginated_requests(
    mock_chain, app, auth_env, client, db, sample_deps_replace, sample_package, worker_auth_env,
):
    repo_template = 'https://github.com/release-engineering/retrodep{}.git'
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=auth_env):
        for i in range(50):
            data = {
                'repo': repo_template.format(i),
                'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
                'pkg_managers': ['gomod'],
            }
            request = Request.from_json(data)
            db.session.add(request)
    db.session.commit()

    payload = {'dependencies': sample_deps_replace, 'packages': [sample_package]}
    client.patch('/api/v1/requests/1', json=payload, environ_base=worker_auth_env)
    client.patch('/api/v1/requests/11', json=payload, environ_base=worker_auth_env)

    # Sane defaults are provided
    rv = client.get('/api/v1/requests')
    assert rv.status_code == 200
    response = rv.json
    fetched_requests = response['items']
    assert len(fetched_requests) == 20
    for repo_number, request in enumerate(fetched_requests):
        assert request['repo'] == repo_template.format(repo_number)
    assert response['meta']['previous'] is None
    assert fetched_requests[0]['dependencies'] == 14
    assert fetched_requests[0]['packages'] == 1

    # per_page and page parameters are honored
    rv = client.get('/api/v1/requests?page=2&per_page=10&verbose=True&state=in_progress')
    assert rv.status_code == 200
    response = rv.json
    fetched_requests = response['items']
    assert len(fetched_requests) == 10
    # Start at 10 because each page contains 10 items and we're processing the second page
    for repo_number, request in enumerate(fetched_requests, 10):
        assert request['repo'] == repo_template.format(repo_number)
    pagination_metadata = response['meta']
    for page, page_num in [('next', 3), ('last', 5), ('previous', 1), ('first', 1)]:
        assert f'page={page_num}' in pagination_metadata[page]
        assert 'per_page=10' in pagination_metadata[page]
        assert 'verbose=True' in pagination_metadata[page]
        assert 'state=in_progress' in pagination_metadata[page]
    assert pagination_metadata['total'] == 50
    assert len(fetched_requests[0]['dependencies']) == 14
    assert len(fetched_requests[0]['packages']) == 1
    assert type(fetched_requests[0]['dependencies']) == list


def test_create_request_filter_state(app, auth_env, client, db):
    """Test that requests can be filtered by state."""
    repo_template = 'https://github.com/release-engineering/retrodep{}.git'
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=auth_env):
        # Make a request in 'in_progress' state
        data = {
            'repo': repo_template.format(0),
            'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
            'pkg_managers': ['gomod'],
        }
        request = Request.from_json(data)
        db.session.add(request)
        # Make a request in 'complete' state
        data_complete = {
            'repo': repo_template.format(1),
            'ref': 'e1be527f39ec31323f0454f7d1422c6260b00580',
            'pkg_managers': ['gomod'],
        }
        request_complete = Request.from_json(data_complete)
        request_complete.add_state('complete', 'Completed successfully')
        db.session.add(request_complete)
    db.session.commit()

    for state in ('in_progress', 'complete'):
        rv = client.get(f'/api/v1/requests?state={state}')
        assert rv.status_code == 200
        fetched_requests = rv.json['items']
        assert len(fetched_requests) == 1
        assert fetched_requests[0]['state'] == state


def test_invalid_state(app, auth_env, client, db):
    """Test that the proper error is thrown when an invalid state is entered."""
    rv = client.get('/api/v1/requests?state=complet')
    assert rv.status_code == 400
    response = rv.json
    states = ', '.join(RequestStateMapping.get_state_names())
    assert response['error'] == f'complet is not a valid request state. Valid states are: {states}'


def assert_request_is_not_created(**criteria):
    assert 0 == Request.query.filter_by(**criteria).count()


@pytest.mark.parametrize('invalid_ref', ['not-a-ref', '23ae3f', '1234' * 20])
def test_create_request_invalid_ref(invalid_ref, auth_env, client, db):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': invalid_ref,
        'pkg_managers': ['gomod']
    }

    rv = client.post('/api/v1/requests', json=data, environ_base=auth_env)
    assert rv.status_code == 400
    assert rv.json['error'] == 'The "ref" parameter must be a 40 character hex string'
    assert_request_is_not_created(ref=invalid_ref)


def test_create_request_invalid_pkg_manager(auth_env, client, db):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['something_wrong']
    }

    rv = client.post('/api/v1/requests', json=data, environ_base=auth_env)
    assert rv.status_code == 400
    assert rv.json['error'] == 'The following package managers are invalid: something_wrong'


@pytest.mark.parametrize('dependency_replacements, error_msg', (
    (
        ['mypackage'],
        'A dependency replacement must be a JSON object with the following keys: name, type, '
        'version. It may also contain the following optional keys: new_name.',
    ),
    ('mypackage', '"dependency_replacements" must be an array'),
))
def test_create_request_invalid_dependency_replacement(
    dependency_replacements, error_msg, auth_env, client, db,
):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'dependency_replacements': dependency_replacements,
    }

    rv = client.post('/api/v1/requests', json=data, environ_base=auth_env)
    assert rv.status_code == 400
    assert rv.json['error'] == error_msg


def test_create_request_not_an_object(auth_env, client, db):
    rv = client.post('/api/v1/requests', json=None, environ_base=auth_env)
    assert rv.status_code == 400
    assert rv.json['error'] == 'The input data must be a JSON object'


def test_create_request_invalid_parameter(auth_env, client, db):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod'],
        'username': 'uncle_sam',
    }

    rv = client.post('/api/v1/requests', json=data, environ_base=auth_env)
    assert rv.status_code == 400
    assert rv.json['error'] == 'The following parameters are invalid: username'


def test_create_request_cannot_set_user(client, db):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'user': 'tom_hanks@DOMAIN.LOCAL',
    }

    auth_env = {'REMOTE_USER': 'homer_simpson@DOMAIN.LOCAL'}
    rv = client.post('/api/v1/requests', json=data, environ_base=auth_env)
    assert rv.status_code == 403
    error = rv.json
    assert error['error'] == 'You are not authorized to create a request on behalf of another user'


def test_cannot_set_user_if_auth_disabled(client_no_auth):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'user': 'tselleck',
    }

    rv = client_no_auth.post('/api/v1/requests', json=data)
    assert rv.status_code == 400
    assert rv.json['error'] == 'Cannot set "user" when authentication is disabled'


def test_create_request_not_logged_in(client, db):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod'],
    }

    rv = client.post('/api/v1/requests', json=data)
    assert rv.status_code == 401
    assert rv.json['error'] == (
        'The server could not verify that you are authorized to access the URL requested. You '
        'either supplied the wrong credentials (e.g. a bad password), or your browser doesn\'t '
        'understand how to supply the credentials required.'
    )


def test_missing_request(client, db):
    rv = client.get('/api/v1/requests/1')
    assert rv.status_code == 404

    rv = client.get('/api/v1/requests/1/download')
    assert rv.status_code == 404


def test_malformed_request_id(client, db):
    rv = client.get('/api/v1/requests/spam')
    assert rv.status_code == 404
    assert rv.json == {'error': 'The requested resource was not found'}


def test_create_request_invalid_flag(auth_env, client, db):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod'],
        'flags': ['invalid_flag']
    }

    rv = client.post('/api/v1/requests', json=data, environ_base=auth_env)
    assert rv.status_code == 400
    assert rv.json['error'] == 'Invalid/Inactive flag(s): invalid_flag'


@pytest.mark.parametrize('removed_params', (
    ('repo', 'ref'),
    ('repo',),
    ('ref',),
))
def test_validate_required_params(auth_env, client, db, removed_params):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
    }
    for removed_param in removed_params:
        data.pop(removed_param)

    rv = client.post('/api/v1/requests', json=data, environ_base=auth_env)
    assert rv.status_code == 400
    error_msg = rv.json['error']
    assert 'Missing required' in error_msg
    for removed_param in removed_params:
        assert removed_param in error_msg


def test_validate_extraneous_params(auth_env, client, db):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod'],
        'spam': 'maps',
    }

    rv = client.post('/api/v1/requests', json=data, environ_base=auth_env)
    assert rv.status_code == 400
    error_msg = rv.json['error']
    assert error_msg == 'The following parameters are invalid: spam'


@mock.patch('cachito.web.api_v1.chain')
def test_create_request_connection_error(mock_chain, app, auth_env, client, db):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod']
    }

    mock_chain.side_effect = kombu.exceptions.OperationalError('Failed to connect')
    rv = client.post('/api/v1/requests', json=data, environ_base=auth_env)

    assert rv.status_code == 500
    assert rv.json == {'error': 'Failed to connect to the broker to schedule a task'}


@mock.patch('pathlib.Path.exists')
@mock.patch('flask.send_file')
@mock.patch('cachito.web.api_v1.Request')
def test_download_archive(mock_request, mock_send_file, mock_exists, client, app):
    request_id = 1
    request = mock.Mock(id=request_id)
    request.state.state_name = 'complete'
    mock_request.query.get_or_404.return_value = request
    mock_send_file.return_value = 'something'
    mock_exists.return_value = True
    client.get(f'/api/v1/requests/{request_id}/download')
    mock_send_file.assert_called_once_with(
        str(RequestBundleDir(request_id).bundle_archive_file),
        mimetype='application/gzip')


@mock.patch('cachito.web.api_v1.Request')
def test_download_archive_no_bundle(mock_request, client, app):
    request = mock.Mock(id=1)
    request.state.state_name = 'complete'
    mock_request.query.get_or_404.return_value = request
    rv = client.get('/api/v1/requests/1/download')
    assert rv.status_code == 500


@mock.patch('cachito.web.api_v1.Request')
def test_download_archive_not_complete(mock_request, client, db, app):
    mock_request.query.get_or_404().last_state.state_name = 'in_progress'
    rv = client.get('/api/v1/requests/1/download')
    assert rv.status_code == 400
    assert rv.json == {
        'error': 'The request must be in the "complete" state before downloading the archive',
    }


@pytest.mark.parametrize('state', ('complete', 'failed'))
@mock.patch('pathlib.Path.exists')
@mock.patch('shutil.rmtree')
def test_set_state(mock_rmtree, mock_exists, state, app, client, db, worker_auth_env):
    mock_exists.return_value = True
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod'],
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    request_id = 1
    state = state
    state_reason = 'Some status'
    payload = {'state': state, 'state_reason': state_reason}
    patch_rv = client.patch(f'/api/v1/requests/{request_id}',
                            json=payload, environ_base=worker_auth_env)
    assert patch_rv.status_code == 200

    get_rv = client.get(f'/api/v1/requests/{request_id}')
    assert get_rv.status_code == 200

    fetched_request = get_rv.json
    assert fetched_request['state'] == state
    assert fetched_request['state_reason'] == state_reason
    # Since the date is always changing, the actual value can't be confirmed
    assert fetched_request['updated']
    assert len(fetched_request['state_history']) == 2
    # Make sure the order is from newest to oldest
    assert fetched_request['state_history'][0]['state'] == state
    assert fetched_request['state_history'][0]['state_reason'] == state_reason
    assert fetched_request['state_history'][0]['updated']
    assert fetched_request['state_history'][1]['state'] == 'in_progress'
    mock_exists.assert_called_once()
    mock_rmtree.assert_called_once_with(str(RequestBundleDir(request_id)))


def test_set_pkg_managers(app, client, db, worker_auth_env):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    payload = {'pkg_managers': ['gomod']}
    patch_rv = client.patch('/api/v1/requests/1', json=payload, environ_base=worker_auth_env)
    assert patch_rv.status_code == 200

    get_rv = client.get('/api/v1/requests/1')
    assert get_rv.status_code == 200
    assert get_rv.json['pkg_managers'] == ['gomod']


@pytest.mark.parametrize('bundle_exists', (True, False))
@mock.patch('pathlib.Path.exists')
@mock.patch('pathlib.Path.unlink')
def test_set_state_stale(mock_remove, mock_exists, bundle_exists, app, client, db, worker_auth_env):
    mock_exists.return_value = bundle_exists
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod'],
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    state = 'stale'
    state_reason = 'The request has expired'
    payload = {'state': state, 'state_reason': state_reason}
    patch_rv = client.patch('/api/v1/requests/1', json=payload, environ_base=worker_auth_env)
    assert patch_rv.status_code == 200

    get_rv = client.get('/api/v1/requests/1')
    assert get_rv.status_code == 200

    fetched_request = get_rv.get_json()
    assert fetched_request['state'] == state
    assert fetched_request['state_reason'] == state_reason
    if bundle_exists:
        mock_remove.assert_called_once_with()
    else:
        mock_remove.assert_not_called()


def test_set_state_from_stale(app, client, db, worker_auth_env):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod'],
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()
    request.add_state('stale', 'The request has expired')
    db.session.commit()

    payload = {'state': 'complete', 'state_reason': 'Unexpired'}
    patch_rv = client.patch('/api/v1/requests/1', json=payload, environ_base=worker_auth_env)
    assert patch_rv.status_code == 400
    assert patch_rv.get_json() == {'error': 'A stale request cannot change states'}


def test_set_state_no_duplicate(app, client, db, worker_auth_env):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod'],
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    state = 'complete'
    state_reason = 'Completed successfully'
    payload = {'state': state, 'state_reason': state_reason}
    for i in range(3):
        patch_rv = client.patch('/api/v1/requests/1', json=payload, environ_base=worker_auth_env)
        assert patch_rv.status_code == 200

    get_rv = client.get('/api/v1/requests/1')
    assert get_rv.status_code == 200

    # Make sure no duplicate states were added
    assert len(get_rv.json['state_history']) == 2


@pytest.mark.parametrize('env_vars', (
    {},
    {'spam': 'maps'},
))
def test_set_deps(app, client, db, worker_auth_env, sample_deps_replace, env_vars):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod'],
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    # Test a dependency with no "replaces" key
    sample_deps_replace.append({
        'name': 'all_systems_go',
        'type': 'gomod',
        'version': 'v1.0.0',
    })
    payload = {'dependencies': sample_deps_replace, 'environment_variables': env_vars}
    patch_rv = client.patch('/api/v1/requests/1', json=payload, environ_base=worker_auth_env)
    assert patch_rv.status_code == 200

    len(EnvironmentVariable.query.all()) == len(env_vars.items())
    for name, value in env_vars.items():
        env_var_obj = EnvironmentVariable.query.filter_by(name=name, value=value).first()
        assert env_var_obj

    get_rv = client.get('/api/v1/requests/1')
    assert get_rv.status_code == 200
    fetched_request = get_rv.json

    # Add a null "replaces" key to match the API output
    sample_deps_replace[-1]['replaces'] = None
    assert fetched_request['dependencies'] == sample_deps_replace
    assert fetched_request['environment_variables'] == env_vars


def test_add_dep_twice_diff_replaces(app, client, db, worker_auth_env):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod'],
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    payload = {
        'dependencies': [{
            'name': 'all_systems_go',
            'type': 'gomod',
            'version': 'v1.0.0',
        }]
    }
    patch_rv = client.patch('/api/v1/requests/1', json=payload, environ_base=worker_auth_env)
    assert patch_rv.status_code == 200

    # Add the dependency again with replaces set this time
    payload2 = {
        'dependencies': [{
            'name': 'all_systems_go',
            'type': 'gomod',
            'replaces': {
                'name': 'all_systems_go',
                'type': 'gomod',
                'version': 'v1.1.0',
            },
            'version': 'v1.0.0',
        }]
    }

    patch_rv = client.patch('/api/v1/requests/1', json=payload2, environ_base=worker_auth_env)
    assert patch_rv.status_code == 400
    assert 'can\'t have a new replacement set' in patch_rv.json['error']


def test_set_packages(app, client, db, sample_package, worker_auth_env):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    payload = {'packages': [sample_package]}
    patch_rv = client.patch('/api/v1/requests/1', json=payload, environ_base=worker_auth_env)
    assert patch_rv.status_code == 200

    get_rv = client.get('/api/v1/requests/1')
    assert get_rv.status_code == 200
    assert get_rv.json['packages'] == [sample_package]


def test_set_state_not_logged_in(client, db):
    payload = {'state': 'complete', 'state_reason': 'Completed successfully'}
    rv = client.patch('/api/v1/requests/1', json=payload)
    assert rv.status_code == 401
    assert rv.json['error'] == (
        'The server could not verify that you are authorized to access the URL requested. You '
        'either supplied the wrong credentials (e.g. a bad password), or your browser doesn\'t '
        'understand how to supply the credentials required.'
    )


@pytest.mark.parametrize('request_id, payload, status_code, message', (
    (
        1,
        {'state': 'call_for_support', 'state_reason': 'It broke'},
        400,
        'The state "call_for_support" is invalid. It must be one of: complete, failed, '
        'in_progress, stale.',
    ),
    (
        1337,
        {'state': 'complete', 'state_reason': 'Success'},
        404,
        'The requested resource was not found',
    ),
    (
        1,
        {},
        400,
        'At least one key must be specified to update the request',
    ),
    (
        1,
        {'state': 'complete', 'state_reason': 'Success', 'id': 42},
        400,
        'The following keys are not allowed: id',
    ),
    (
        1,
        {'state': 1, 'state_reason': 'Success'},
        400,
        'The value for "state" must be a string',
    ),
    (
        1,
        {'state': 'complete'},
        400,
        'The "state_reason" key is required when "state" is supplied',
    ),
    (
        1,
        {'state_reason': 'Success'},
        400,
        'The "state" key is required when "state_reason" is supplied',
    ),
    (
        1,
        'some string',
        400,
        'The input data must be a JSON object',
    ),
    (
        1,
        {'dependencies': 'test'},
        400,
        'The value for "dependencies" must be an array',
    ),
    (
        1,
        {'packages': 'test'},
        400,
        'The value for "packages" must be an array',
    ),
    (
        1,
        {'pkg_managers': 'test'},
        400,
        'The value for "pkg_managers" must be an array',
    ),
    (
        1,
        {'pkg_managers': [1, 3]},
        400,
        'The value for "pkg_managers" must be an array of strings',
    ),
    (
        1,
        {'dependencies': ['test']},
        400,
        (
            'A dependency must be a JSON object with the following keys: name, type, version. It '
            'may also contain the following optional keys: replaces.'
        ),
    ),
    (
        1,
        {
            'dependencies': [
                {'name': 'pizza', 'type': 'gomod', 'replaces': 'bad', 'version': 'v1.4.2'},
            ]
        },
        400,
        'A dependency must be a JSON object with the following keys: name, type, version.',
    ),
    (
        1,
        {'dependencies': [{'type': 'gomod', 'version': 'v1.4.2'}]},
        400,
        (
            'A dependency must be a JSON object with the following keys: name, type, version. It '
            'may also contain the following optional keys: replaces.'
        ),
    ),
    (
        1,
        {'packages': [{'type': 'gomod', 'version': 'v1.4.2'}]},
        400,
        'A package must be a JSON object with the following keys: name, type, version.',
    ),
    (
        1,
        {
            'packages': [{
                'name': 'github.com/release-engineering/retrodep/v2',
                'type': 'gomod',
                'version': 3,
            }]
        },
        400,
        'The "version" key of the package must be a string',
    ),
    (
        1,
        {
            'dependencies': [
                {
                    'name': 'github.com/Masterminds/semver',
                    'type': 'gomod',
                    'version': 3.0,
                },
            ],
        },
        400,
        'The "version" key of the dependency must be a string',
    ),
    (
        1,
        {
            'environment_variables': 'spam',
        },
        400,
        'The value for "environment_variables" must be an object',
    ),
    (
        1,
        {
            'environment_variables': {'spam': None},
        },
        400,
        'The value of environment variables must be a string',
    ),
    (
        1,
        {
            'environment_variables': {'spam': ['maps']},
        },
        400,
        'The value of environment variables must be a string',
    ),
))
def test_state_change_invalid(
    app, client, db, worker_auth_env, request_id, payload, status_code, message
):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod'],
    }
    # flask_login.current_user is used in Request.from_json, which requires a request context
    with app.test_request_context(environ_base=worker_auth_env):
        request = Request.from_json(data)
    db.session.add(request)
    db.session.commit()

    rv = client.patch(f'/api/v1/requests/{request_id}', json=payload, environ_base=worker_auth_env)
    assert rv.status_code == status_code
    assert rv.json == {'error': message}
