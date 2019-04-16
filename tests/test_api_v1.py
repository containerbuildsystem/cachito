# SPDX-License-Identifier: GPL-3.0-or-later
import json
from unittest import mock

import pytest


def test_ping(client):
    rv = client.get('/api/v1/ping')
    assert json.loads(rv.data.decode('utf-8')) is True


@mock.patch('cachito.web.api_v1.tasks.fetch_app_source.delay')
def test_create_and_fetch_request(mock_fetch, client, db):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod']
    }

    rv = client.post('/api/v1/requests', json=data)
    assert rv.status_code == 201
    created_request = json.loads(rv.data.decode('utf-8'))
    for key, expected_value in data.items():
        assert expected_value == created_request[key]

    mock_fetch.assert_called_once_with(
        'https://github.com/release-engineering/retrodep.git',
        'c50b93a32df1c9d700e3e80996845bc2e13be848')

    request_id = created_request['id']
    rv = client.get('/api/v1/requests/{}'.format(request_id))
    assert rv.status_code == 200
    fetched_request = json.loads(rv.data.decode('utf-8'))

    assert created_request == fetched_request


def test_create_and_fetch_request_invalid_ref(client, db):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'not-a-ref',
        'pkg_managers': ['gomod']
    }

    rv = client.post('/api/v1/requests', json=data)
    assert rv.status_code == 400
    error = json.loads(rv.data.decode('utf-8'))
    assert error['error'] == 'The "ref" parameter must be a 40 character hex string'


def test_missing_request(client, db):
    rv = client.get('/api/v1/requests/1')
    assert rv.status_code == 404


def test_malformed_request_id(client, db):
    rv = client.get('/api/v1/requests/spam')
    assert rv.status_code == 400
    error_msg = json.loads(rv.data.decode('utf-8'))['error']
    assert 'not a valid request ID' in error_msg


@pytest.mark.parametrize('removed_params', (
    ('repo', 'ref', 'pkg_managers'),
    ('repo',),
    ('ref',),
    ('pkg_managers',),
))
def test_validate_required_params(client, db, removed_params):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod']
    }
    for removed_param in removed_params:
        data.pop(removed_param)

    rv = client.post('/api/v1/requests', json=data)
    assert rv.status_code == 400
    error_msg = json.loads(rv.data.decode('utf-8'))['error']
    assert 'Missing required' in error_msg
    for removed_param in removed_params:
        assert removed_param in error_msg


def test_validate_extraneous_params(client, db):
    data = {
        'repo': 'https://github.com/release-engineering/retrodep.git',
        'ref': 'c50b93a32df1c9d700e3e80996845bc2e13be848',
        'pkg_managers': ['gomod'],
        'spam': 'maps',
    }

    rv = client.post('/api/v1/requests', json=data)
    assert rv.status_code == 400
    error_msg = json.loads(rv.data.decode('utf-8'))['error']
    assert 'invalid keyword argument' in error_msg
    assert 'spam' in error_msg
