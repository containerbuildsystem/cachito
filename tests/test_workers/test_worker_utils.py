# SPDX-License-Identifier: GPL-3.0-or-later
from typing import Dict, Any

import pytest
from unittest.mock import patch

from cachito.workers import run_cmd


@pytest.mark.parametrize(
    "input_params,expected_run_params",
    [
        [
            {"timeout": 300},
            {
                "timeout": 300,
                "capture_output": True,
                "universal_newlines": True,
                "encoding": "utf-8",
            },
        ],
        # No timeout is passed in, use the default in config
        [
            {},
            {
                "timeout": 3600,
                "capture_output": True,
                "universal_newlines": True,
                "encoding": "utf-8",
            },
        ],
    ],
)
@patch("subprocess.run")
def test_run_cmd_with_timeout(
    mock_run, input_params: Dict[str, Any], expected_run_params: Dict[str, Any]
):
    mock_run.return_value.returncode = 0
    cmd = ["git", "fcsk"]
    run_cmd(cmd, input_params)
    mock_run.assert_called_once_with(cmd, **expected_run_params)
