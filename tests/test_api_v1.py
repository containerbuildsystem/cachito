# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
import os
import tarfile
from unittest import mock

import pytest

from cachito.workers.tasks import fetch_app_source, fetch_gomod_source


def test_ping(client):
    rv = client.get('/api/v1/ping')
    assert json.loads(rv.data.decode('utf-8')) is True


@mock.patch('cachito.web.api_v1.chain')
def test_create_and_fetch_request(mock_chain, client, db):
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

    mock_chain.assert_called_once_with(
        fetch_app_source.s(
            'https://github.com/release-engineering/retrodep.git',
            'c50b93a32df1c9d700e3e80996845bc2e13be848'),
        fetch_gomod_source.s()
    )

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

    rv = client.get('/api/v1/requests/1/download')
    assert rv.status_code == 404


def test_malformed_request_id(client, db):
    rv = client.get('/api/v1/requests/spam')
    assert rv.status_code == 404


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


@mock.patch('tempfile.TemporaryDirectory')
@mock.patch('cachito.web.api_v1.Request')
@mock.patch('cachito.web.api_v1.chain')
def test_download_archive(
    mock_chain, mock_request, mock_temp_dir, client, db, app, tmpdir
):
    shared_volume = tmpdir.mkdir('shared')
    shared_workdir = shared_volume.mkdir('ephemeral')

    mock_temp_dir.return_value.__enter__.return_value = str(shared_workdir)

    app_archive_contents = {
        'app/spam.go': b'Spam mapS',
        'app/ham.go': b'Ham maH',
    }

    deps_archive_contents = {
        'gomod/pkg/mod/cache/download/server.com/dep1/@v/dep1.zip': b'dep1 archive',
        'gomod/pkg/mod/cache/download/server.com/dep2/@v/dep2.zip': b'dep2 archive',
    }

    def chain_side_effect(*args, **kwargs):
        # Create mocked application source archive
        app_archive_path = shared_workdir.join('app.tar.gz')
        with tarfile.open(app_archive_path, mode='w:gz') as app_archive:
            for name, data in app_archive_contents.items():
                fileobj = io.BytesIO(data)
                tarinfo = tarfile.TarInfo(name)
                tarinfo.size = len(fileobj.getvalue())
                app_archive.addfile(tarinfo, fileobj=fileobj)

        # Create mocked dependencies cache
        deps_dir = shared_workdir.mkdir('deps')
        for name, data in deps_archive_contents.items():
            path = deps_dir.join(name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, 'wb').write(data)

        return mock.Mock()

    mock_chain.side_effect = chain_side_effect

    with mock.patch.dict(app.config, {'CACHITO_SHARED_DIR': str(shared_volume)}):
        rv = client.get('/api/v1/requests/1/download')

    # Verify chain was called correctly.
    mock_chain.assert_called_once_with(
        fetch_app_source.s(
            mock_request.query.get_or_404().repo,
            mock_request.query.get_or_404().ref,
            copy_cache_to='ephemeral/app.tar.gz',
        ),
        fetch_gomod_source.s(copy_cache_to='ephemeral/deps')
    )

    # Verify contents of downloaded archive
    with tarfile.open(fileobj=io.BytesIO(rv.data), mode='r:*') as response_archive:
        for expected_member in list(app_archive_contents.keys()):
            response_archive.getmember(expected_member)
        for expected_member in list(deps_archive_contents.keys()):
            response_archive.getmember(os.path.join('deps', expected_member))
