# SPDX-License-Identifier: GPL-3.0-or-later

from typing import Optional
import pytest
from cachito.web.models import PackageManager


@pytest.mark.parametrize(
    "name,expected", [["gomod", "gomod"], ["", None], [None, None], ["unknown", None]],
)
def test_package_manager_get_by_name(name, expected, app, db, auth_env):
    pkg_manager: Optional[PackageManager] = PackageManager.get_by_name(name)
    if expected is None:
        assert expected == pkg_manager
    else:
        assert expected == pkg_manager.name
