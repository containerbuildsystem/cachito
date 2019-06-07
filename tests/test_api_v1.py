# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
import os
import tarfile
from unittest import mock

import pytest

from cachito.web.models import Request
from cachito.workers.tasks import (
    fetch_app_source, fetch_gomod_source, assemble_source_code_archive, set_request_state,
    failed_request_callback
)


@mock.patch('cachito.web.api_v1.chain')
def test_create_and_fetch_request(mock_chain, app, auth_env, client, db):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod']
    }

    with mock.patch.dict(app.config, {'LOGIN_DISABLED': False}):
        rv = client.post(
            '/api/v1/requests', json=data, environ_base=auth_env)
    assert rv.status_code == 201
    created_request = json.loads(rv.data.decode('utf-8'))
    for key, expected_value in data.items():
        assert expected_value == created_request[key]
    assert created_request['user'] == 'tbrady@domain.local'

    error_callback = failed_request_callback.s(1)
    mock_chain.assert_called_once_with(
        fetch_app_source.s(
            'https://github.com/release-engineering/retrodep.git',
            'c50b93a32df1c9d700e3e80996845bc2e13be848',
            request_id_to_update=1,
        ).on_error(error_callback),
        fetch_gomod_source.s(request_id_to_update=1).on_error(error_callback),
        set_request_state.si(1, 'complete', 'Completed successfully'),
    )

    request_id = created_request['id']
    rv = client.get('/api/v1/requests/{}'.format(request_id))
    assert rv.status_code == 200
    fetched_request = json.loads(rv.data.decode('utf-8'))

    assert created_request == fetched_request
    assert fetched_request['state'] == 'in_progress'
    assert fetched_request['state_reason'] == 'The request was initiated'


@mock.patch('cachito.web.api_v1.chain')
def test_fetch_paginated_requests(mock_chain, app, auth_env, client, db):

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

    # Sane defaults are provided
    rv = client.get('/api/v1/requests')
    assert rv.status_code == 200
    response = json.loads(rv.data.decode('utf-8'))
    fetched_requests = response['items']
    assert len(fetched_requests) == 20
    for repo_number, request in enumerate(fetched_requests):
        assert request['repo'] == repo_template.format(repo_number)

    # per_page and page parameters are honored
    rv = client.get('/api/v1/requests?page=2&per_page=10')
    assert rv.status_code == 200
    response = json.loads(rv.data.decode('utf-8'))
    fetched_requests = response['items']
    assert len(fetched_requests) == 10
    # Start at 10 because each page contains 10 items and we're processing the second page
    for repo_number, request in enumerate(fetched_requests, 10):
        assert request['repo'] == repo_template.format(repo_number)


def test_create_request_invalid_ref(auth_env, client, db):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'not-a-ref',
        'pkg_managers': ['gomod']
    }

    rv = client.post('/api/v1/requests', json=data, environ_base=auth_env)
    assert rv.status_code == 400
    error = json.loads(rv.data.decode('utf-8'))
    assert error['error'] == 'The "ref" parameter must be a 40 character hex string'


def test_create_request_invalid_parameter(auth_env, client, db):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod'],
        'user': 'uncle_sam',
    }

    rv = client.post('/api/v1/requests', json=data, environ_base=auth_env)
    assert rv.status_code == 400
    error = json.loads(rv.data.decode('utf-8'))
    assert error['error'] == 'The following parameters are invalid: user'


def test_create_request_not_logged_in(client, db):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod'],
    }

    rv = client.post('/api/v1/requests', json=data)
    assert rv.status_code == 401
    error = json.loads(rv.data.decode('utf-8'))
    assert error['error'] == (
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
    data = json.loads(rv.data.decode('utf-8'))
    assert data == {'error': 'The requested resource was not found'}


@pytest.mark.parametrize('removed_params', (
    ('repo', 'ref', 'pkg_managers'),
    ('repo',),
    ('ref',),
    ('pkg_managers',),
))
def test_validate_required_params(auth_env, client, db, removed_params):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod']
    }
    for removed_param in removed_params:
        data.pop(removed_param)

    rv = client.post('/api/v1/requests', json=data, environ_base=auth_env)
    assert rv.status_code == 400
    error_msg = json.loads(rv.data.decode('utf-8'))['error']
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
    error_msg = json.loads(rv.data.decode('utf-8'))['error']
    assert error_msg == 'The following parameters are invalid: spam'


@mock.patch('tempfile.TemporaryDirectory')
@mock.patch('cachito.web.api_v1.Request')
@mock.patch('cachito.web.api_v1.chain')
def test_download_archive(
    mock_chain, mock_request, mock_temp_dir, client, db, app, tmpdir
):
    ephemeral_dir_name = 'ephemeral123'
    shared_cachito_dir = tmpdir
    shared_temp_dir = shared_cachito_dir.mkdir(ephemeral_dir_name)
    mock_temp_dir.return_value.__enter__.return_value = str(shared_temp_dir)

    bundle_archive_contents = {
        'app/spam.go': b'Spam mapS',
        'app/ham.go': b'Ham maH',
        'gomod/pkg/mod/cache/download/server.com/dep1/@v/dep1.zip': b'dep1 archive',
        'gomod/pkg/mod/cache/download/server.com/dep2/@v/dep2.zip': b'dep2 archive',
    }
    bundle_archive_path = shared_temp_dir.join('bundle.tar.gz')

    def chain_side_effect(*args, **kwargs):
        # Create mocked bundle source code archive
        with tarfile.open(bundle_archive_path, mode='w:gz') as bundle_archive:
            for name, data in bundle_archive_contents.items():
                fileobj = io.BytesIO(data)
                tarinfo = tarfile.TarInfo(name)
                tarinfo.size = len(fileobj.getvalue())
                bundle_archive.addfile(tarinfo, fileobj=fileobj)
        return mock.Mock()

    mock_chain.side_effect = chain_side_effect

    with mock.patch.dict(app.config, {'CACHITO_SHARED_DIR': str(shared_cachito_dir)}):
        rv = client.get('/api/v1/requests/1/download')

    # Verify chain was called correctly.
    mock_chain.assert_called_once_with(
        fetch_app_source.s(
            mock_request.query.get_or_404().repo, mock_request.query.get_or_404().ref,
        ),
        fetch_gomod_source.s(copy_cache_to=os.path.join(ephemeral_dir_name, 'deps')),
        assemble_source_code_archive.s(
            deps_path=os.path.join(ephemeral_dir_name, 'deps'),
            bundle_archive_path=os.path.join(ephemeral_dir_name, 'bundle.tar.gz')),
    )

    # Verify contents of downloaded archive
    with tarfile.open(fileobj=io.BytesIO(rv.data), mode='r:*') as bundle_archive:
        for expected_member in list(bundle_archive_contents.keys()):
            bundle_archive.getmember(expected_member)


def test_set_state(app, client, db, worker_auth_env):
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
    patch_rv = client.patch('/api/v1/requests/1', json=payload, environ_base=worker_auth_env)
    assert patch_rv.status_code == 200

    get_rv = client.get('/api/v1/requests/1')
    assert get_rv.status_code == 200

    fetched_request = json.loads(get_rv.data.decode('utf-8'))
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


def test_set_state_not_logged_in(client, db):
    payload = {'state': 'complete', 'state_reason': 'Completed successfully'}
    rv = client.patch('/api/v1/requests/1', json=payload)
    assert rv.status_code == 401
    error = json.loads(rv.data.decode('utf-8'))
    assert error['error'] == (
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
        'in_progress.',
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
        {'state': 'complete', 'state_reason': 'Success', 'pkg_managers': ['javascript']},
        400,
        'The following keys are not allowed: pkg_managers',
    ),
    (
        1,
        {'state': 1, 'state_reason': 'Success'},
        400,
        'The value for "state" must be a string. It was the type int.',
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
    data = json.loads(rv.data.decode('utf-8'))
    assert data == {'error': message}
