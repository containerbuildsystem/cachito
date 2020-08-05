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
    Create a new request for every package manager in Cachito.

    :param test_env: Test environment configuration
    :return: a dict of packages with initial and completed responses from the Cachito API
    :rtype: dict
    """
    client = utils.Client(test_env["api_url"], test_env["api_auth_type"], test_env.get("timeout"))
    default_requests = {}
    packages = test_env["packages"]
    for package_name in packages:
        initial_response = client.create_new_request(
            payload={
                "repo": packages[package_name]["repo"],
                "ref": packages[package_name]["ref"],
                "pkg_managers": packages[package_name]["pkg_managers"],
            },
        )
        completed_response = client.wait_for_complete_request(initial_response)
        default_requests[package_name] = DefaultRequest(initial_response, completed_response)

    return default_requests
