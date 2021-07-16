# SPDX-License-Identifier: GPL-3.0-or-later
import os
from io import BytesIO
from unittest.mock import patch

import celery
import pytest

from cachito.errors import ConfigError
from cachito.workers.config import (
    configure_celery,
    validate_celery_config,
    validate_nexus_config,
    validate_npm_config,
    validate_pip_config,
    validate_yarn_config,
)


@patch("os.path.isfile", return_value=False)
def test_configure_celery_with_classes(mock_isfile):
    celery_app = celery.Celery()
    assert celery_app.conf.task_default_queue == "celery"
    configure_celery(celery_app)
    assert celery_app.conf.task_default_queue == "cachito"


@patch("os.getenv")
@patch("os.path.isfile", return_value=True)
@patch("cachito.workers.config.open")
def test_configure_celery_with_classes_and_files(mock_open, mock_isfile, mock_getenv):
    mock_getenv.return_value = ""
    mock_open.return_value = BytesIO(
        b'task_default_queue = "not-cachito"\ntimezone="America/New_York"\n'
    )
    celery_app = celery.Celery()
    assert celery_app.conf.task_default_queue == "celery"
    assert celery_app.conf.timezone is None
    configure_celery(celery_app)
    assert celery_app.conf.task_default_queue == "not-cachito"
    assert celery_app.conf.timezone == "America/New_York"


@patch("os.path.isdir", return_value=True)
def test_validate_celery_config(mock_isdir):
    celery_app = celery.Celery()
    celery_app.conf.cachito_api_url = "http://cachito-api/api/v1/"
    celery_app.conf.cachito_bundles_dir = "/tmp/some-path/bundles"
    celery_app.conf.cachito_default_environment_variables = {}
    celery_app.conf.cachito_sources_dir = "/tmp/some-path/sources"
    celery_app.conf.cachito_nexus_hoster_username = "cachito"
    celery_app.conf.cachito_nexus_hoster_password = "cachito-password"
    validate_celery_config(celery_app.conf)
    mock_isdir.assert_any_call(celery_app.conf.cachito_bundles_dir)
    mock_isdir.assert_any_call(celery_app.conf.cachito_sources_dir)


@patch("os.path.isdir", return_value=True)
@pytest.mark.parametrize("bundles_dir, sources_dir", ((False, True), (True, False)))
def test_validate_celery_config_failure(mock_isdir, bundles_dir, sources_dir):
    celery_app = celery.Celery()
    celery_app.conf.cachito_sources_dir = "/tmp/some-path/sources"

    if bundles_dir:
        celery_app.conf.cachito_bundles_dir = "/tmp/some-path/bundles"
        dir_name = "cachito_sources_dir"
    elif sources_dir:
        celery_app.conf.cachito_sources_dir = "/tmp/some-path/sources"
        dir_name = "cachito_bundles_dir"

    setattr(celery_app.conf, dir_name, None)
    expected = f'The configuration "{dir_name}" must be set to an existing directory'
    with pytest.raises(ConfigError, match=expected):
        validate_celery_config(celery_app.conf)


@patch("os.path.isdir", return_value=True)
@pytest.mark.parametrize(
    "default_env_vars, expected,",
    (
        (None, 'The configuration "cachito_default_environment_variables" must be a dictionary'),
        (
            {"npm": None},
            'The configuration "cachito_default_environment_variables" must be a dictionary of '
            "dictionaries",
        ),
        (
            {"npm": {"spam": "not a dict"}},
            'The configuration "cachito_default_environment_variables" must be a dictionary of '
            "dictionaries of dictionaries!",
        ),
        (
            {"npm": {"spam": {"extra": "not-allowed"}}},
            'Each environment variable in the "cachito_default_environment_variables" '
            'configuration must contain the "value" and "kind" keys',
        ),
        (
            {"npm": {"spam": {"value": "the-value", "kind": "the-kind", "extra": "not-allowed"}}},
            'Each environment variable in the "cachito_default_environment_variables" '
            'configuration must contain the "value" and "kind" keys',
        ),
        (
            {"gomod": {"GOCACHE": {"value": "invalid", "kind": "literal"}}},
            'The configuration "cachito_default_environment_variables.gomod" cannot overwrite the '
            "following environment variables: GOCACHE",
        ),
    ),
)
def test_validate_celery_config_failure_default_env_vars(mock_isdir, default_env_vars, expected):
    celery_app = celery.Celery()
    celery_app.conf.cachito_api_url = "http://cachito-api/api/v1/"
    celery_app.conf.cachito_bundles_dir = "/tmp/some-path/bundles"
    celery_app.conf.cachito_default_environment_variables = default_env_vars
    celery_app.conf.cachito_sources_dir = "/tmp/some-path/sources"
    celery_app.conf.cachito_nexus_hoster_username = "cachito"
    celery_app.conf.cachito_nexus_hoster_password = "cachito-password"

    with pytest.raises(ConfigError, match=expected):
        validate_celery_config(celery_app.conf)


@pytest.mark.parametrize(
    "hoster_username, hoster_password", ((None, "password"), ("username", None),)
)
@patch("os.path.isdir", return_value=True)
def test_validate_celery_config_invalid_nexus_hoster_config(
    mock_isdir, hoster_username, hoster_password
):
    celery_app = celery.Celery()
    celery_app.conf.cachito_api_url = "http://cachito-api/api/v1/"
    celery_app.conf.cachito_bundles_dir = "/tmp/some-path/bundles"
    celery_app.conf.cachito_sources_dir = "/tmp/some-path/sources"
    celery_app.conf.cachito_nexus_hoster_username = hoster_username
    celery_app.conf.cachito_nexus_hoster_password = hoster_password
    expected = (
        'If "cachito_nexus_hoster_username" or "cachito_nexus_hoster_password" is set, '
        "the other must also be set"
    )
    with pytest.raises(ConfigError, match=expected):
        validate_celery_config(celery_app.conf)


@pytest.mark.parametrize("auth_type", ("cert", "kerberos", None))
@pytest.mark.parametrize("has_cert", (False, True))
@pytest.mark.parametrize("auth_cert", ("/some/path", None))
@patch("os.path.isdir", return_value=True)
def test_validate_celery_config_missing_cert(mock_isdir, auth_type, has_cert, auth_cert):
    celery_app = celery.Celery()
    celery_app.conf.cachito_api_url = "http://cachito-api/api/v1/"
    celery_app.conf.cachito_default_environment_variables = {}
    celery_app.conf.cachito_bundles_dir = "/tmp/some-path/bundles"
    celery_app.conf.cachito_sources_dir = "/tmp/some-path/sources"
    celery_app.conf.cachito_auth_type = auth_type
    if has_cert:
        celery_app.conf.cachito_auth_cert = auth_cert

    if (has_cert and auth_cert) or auth_type != "cert":
        validate_celery_config(celery_app.conf)
    else:
        expected = 'cachito_auth_cert configuration must be set for "cert" authentication'
        with pytest.raises(ConfigError, match=expected):
            validate_celery_config(celery_app.conf)


@pytest.mark.parametrize(
    "missing_config", ("cachito_nexus_password", "cachito_nexus_url", "cachito_nexus_username"),
)
@patch("cachito.workers.config.get_worker_config")
def test_validate_nexus_config(mock_gwc, missing_config):
    config = {
        "cachito_nexus_password": "cachito",
        "cachito_nexus_url": "https://nexus.domain.local",
        "cachito_nexus_username": "cachito",
    }
    config.pop(missing_config)
    mock_gwc.return_value = config
    expected = f'The configuration "{missing_config}" must be set for this package manager'
    with pytest.raises(ConfigError, match=expected):
        validate_nexus_config()


@patch("cachito.workers.config.get_worker_config")
@patch("cachito.workers.config.validate_nexus_config")
def test_validate_npm_config(mock_vnc, mock_gwc):
    mock_gwc.return_value = {}
    expected = (
        'The configuration "cachito_nexus_npm_proxy_url" must be set for this package manager'
    )
    with pytest.raises(ConfigError, match=expected):
        validate_npm_config()


@patch("cachito.workers.config.get_worker_config")
@patch("cachito.workers.config.validate_nexus_config")
def test_validate_yarn_config(mock_vnc, mock_gwc):
    mock_gwc.return_value = {}
    expected = (
        'The configuration "cachito_nexus_npm_proxy_url" must be set for this package manager'
    )
    with pytest.raises(ConfigError, match=expected):
        validate_yarn_config()


@pytest.mark.parametrize(
    "missing_configs",
    (
        ["cachito_nexus_pypi_proxy_url"],
        ["cachito_nexus_pip_raw_repo_name"],
        ["cachito_nexus_pypi_proxy_url", "cachito_nexus_pip_raw_repo_name"],
        [],
    ),
)
@patch("cachito.workers.config.get_worker_config")
@patch("cachito.workers.config.validate_nexus_config")
def test_validate_pip_config(mock_vnc, mock_gwc, missing_configs):
    configs = {"cachito_nexus_pypi_proxy_url": "foo", "cachito_nexus_pip_raw_repo_name": "bar"}
    for conf in missing_configs:
        configs.pop(conf)

    mock_gwc.return_value = configs
    if missing_configs:
        expected = f'The configuration "{missing_configs[0]}" must be set for this package manager'
        with pytest.raises(ConfigError, match=expected):
            validate_pip_config()
    else:
        validate_pip_config()


@pytest.mark.parametrize(
    "file_type, access, error",
    (
        ("file", True, "cachito_request_file_logs_dir, {logs_dir}, must exist and be a directory"),
        (None, True, "cachito_request_file_logs_dir, {logs_dir}, must exist and be a directory"),
        ("dir", False, "cachito_request_file_logs_dir, {logs_dir}, is not writable!"),
    ),
)
def test_validate_celery_config_request_logs_dir_misconfigured(file_type, access, error, tmpdir):
    cachito_request_file_logs_dir = tmpdir.join("logs")
    cachito_bundles_dir = tmpdir.join("bundles")
    cachito_bundles_dir.mkdir()
    cachito_sources_dir = tmpdir.join("sources")
    cachito_sources_dir.mkdir()
    if file_type == "file":
        cachito_request_file_logs_dir.write("")
    elif file_type == "dir":
        cachito_request_file_logs_dir.mkdir()
    elif file_type is None:
        # Skip creating the file or directory altogether
        pass
    else:
        raise ValueError(f"Bad file_type {file_type}")

    if not access:
        if os.getuid() == 0:
            pytest.skip("Cannot restrict the root user from writing to any file")
        cachito_request_file_logs_dir.chmod(mode=0o555)

    celery_app = celery.Celery()
    celery_app.conf.cachito_api_url = "http://localhost:8080/api/v1/"
    celery_app.conf.cachito_bundles_dir = cachito_bundles_dir
    celery_app.conf.cachito_sources_dir = cachito_sources_dir
    celery_app.conf.cachito_default_environment_variables = {}
    celery_app.conf.cachito_request_file_logs_dir = cachito_request_file_logs_dir
    error = error.format(logs_dir=cachito_request_file_logs_dir)
    with pytest.raises(ConfigError, match=error):
        validate_celery_config(celery_app.conf)
