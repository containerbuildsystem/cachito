# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from cachito.common import utils


@pytest.mark.parametrize(
    "url, expected_repo_name",
    [
        ("https://github.com/containerbuildsystem/cachito/", "containerbuildsystem/cachito"),
        ("https://github.com/containerbuildsystem/cachito.git/", "containerbuildsystem/cachito"),
        ("https://github.com/containerbuildsystem/cachito.git", "containerbuildsystem/cachito"),
    ],
)
def test_get_repo_name(url, expected_repo_name):
    repo_name = utils.get_repo_name(url)
    assert repo_name == expected_repo_name
