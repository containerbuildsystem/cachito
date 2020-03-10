# SPDX-License-Identifier: GPL-3.0-or-later

import os

import pytest
import yaml


@pytest.fixture(scope="session")
def test_env():
    """
    Load the test environment configuration.

    :return: Test environment configuration.
    :rtype:  dict
    """
    config_file = os.getenv("CACHITO_TEST_CONFIG", "test_env_vars.yaml")
    with open(config_file) as f:
        env = yaml.safe_load(f)
    return env
