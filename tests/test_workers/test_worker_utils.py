# SPDX-License-Identifier: GPL-3.0-or-later
import json
from typing import Any, Dict
from unittest.mock import patch

import pytest

from cachito.workers import load_json_stream, run_cmd


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


@pytest.mark.parametrize(
    "test_input, expected_output",
    [
        ("\n", []),
        ("1 2 3 4", [1, 2, 3, 4]),
        ("[1, 2][3, 4]", [[1, 2], [3, 4]]),
        ('\n{"a": 1}\n\n{"b": 2}\n', [{"a": 1}, {"b": 2}]),
    ],
)
def test_load_json_stream(test_input, expected_output):
    assert list(load_json_stream(test_input)) == expected_output


def test_load_json_stream_invalid():
    invalid_input = "1 2 invalid"
    data = load_json_stream(invalid_input)
    assert next(data) == 1
    assert next(data) == 2
    with pytest.raises(json.JSONDecodeError, match="Expecting value: line 1 column 5"):
        next(data)
