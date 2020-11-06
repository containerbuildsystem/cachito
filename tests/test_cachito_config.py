# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
from unittest.mock import patch

import pytest

from cachito.web.config import validate_cachito_config
from cachito.errors import ConfigError


@patch("os.path.isdir", return_value=True)
def test_validate_cachito_config_success(mock_isdir, app):
    validate_cachito_config(app.config)
    mock_isdir.assert_any_call(os.path.join(tempfile.gettempdir(), "cachito-archives/bundles"))


@patch("os.path.isdir", return_value=True)
@pytest.mark.parametrize(
    "variable_name",
    (
        "CACHITO_BUNDLES_DIR",
        "CACHITO_DEFAULT_PACKAGE_MANAGERS",
        "CACHITO_LOG_LEVEL",
        "CACHITO_MAX_PER_PAGE",
        "CACHITO_MUTUALLY_EXCLUSIVE_PACKAGE_MANAGERS",
        "CACHITO_LOG_FORMAT",
        "SQLALCHEMY_DATABASE_URI",
    ),
)
def test_validate_cachito_config_failure(mock_isdir, app, variable_name):
    expected = f'The configuration "{variable_name}" must be set'
    if variable_name == "CACHITO_BUNDLES_DIR":
        expected += " to an existing directory"
    with patch.dict(app.config, {variable_name: None}):
        with pytest.raises(ConfigError, match=expected):
            validate_cachito_config(app.config)


@patch("os.path.isdir")
def test_validate_cachito_config_cli(mock_isdir, app):
    validate_cachito_config(app.config, cli=True)
    mock_isdir.assert_not_called()


@pytest.mark.parametrize(
    "value, is_valid",
    [
        ([], True),
        (["gomod"], False),
        ([("gomod", "git-submodule")], True),
        ([["gomod", "git-submodule"]], True),
        ([("gomod",)], False),
        ([["gomod"]], False),
    ],
)
def test_validate_mutually_exclusive_package_managers(app, value, is_valid):
    config = app.config.copy()
    config["CACHITO_MUTUALLY_EXCLUSIVE_PACKAGE_MANAGERS"] = value

    if is_valid:
        validate_cachito_config(config)
    else:
        expected = (
            r'All values in "CACHITO_MUTUALLY_EXCLUSIVE_PACKAGE_MANAGERS" '
            r"must be pairs \(2-tuples or 2-item lists\)"
        )
        with pytest.raises(ConfigError, match=expected):
            validate_cachito_config(config)
