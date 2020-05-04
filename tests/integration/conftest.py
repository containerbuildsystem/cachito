# SPDX-License-Identifier: GPL-3.0-or-later

import os
from collections import namedtuple

import pytest
import yaml

import utils

DefaultRequest = namedtuple("DefaultRequest", "initial_response complete_response")


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


@pytest.fixture(scope="session")
def default_request(test_env):
    """
    Create a new request in Cachito.

    :param test_env: Test environment configuration
    :return: a tuple that contains initial and completed response from the Cachito API
    :rtype: tuple
    """
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"])
    initial_response = client.create_new_request(
        payload={
            "repo": test_env["package"]["repo"],
            "ref": test_env["package"]["ref"],
            "pkg_managers": test_env["package"]["pkg_managers"],
        },
    )
    completed_response = client.wait_for_complete_request(initial_response)

    return DefaultRequest(initial_response, completed_response)
