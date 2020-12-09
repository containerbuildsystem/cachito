# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

import pytest

from cachito.workers.tasks import utils


@pytest.mark.parametrize("path", ["app/foo.cfg", Path("app/foo.cfg")])
def test_make_base64_config_file(path):
    content = "foo = bar"
    expected = {"content": "Zm9vID0gYmFy", "path": "app/foo.cfg", "type": "base64"}
    assert utils.make_base64_config_file(content, path) == expected
