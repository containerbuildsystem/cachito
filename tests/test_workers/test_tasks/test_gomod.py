# SPDX-License-Identifier: GPL-3.0-or-later
from unittest import mock

import pytest

from cachito.workers import tasks
from cachito.workers.paths import RequestBundleDir


@pytest.mark.parametrize(
    "auto_detect, contains_go_mod, dep_replacements, expect_state_update",
    (
        (False, False, None, True),
        (True, True, None, True),
        (True, False, None, False),
        (
            True,
            True,
            False,
            [{"name": "github.com/pkg/errors", "type": "gomod", "version": "v0.8.1"}],
        ),
    ),
)
@mock.patch("cachito.workers.tasks.gomod.RequestBundleDir.exists")
@mock.patch("cachito.workers.tasks.gomod.update_request_with_packages")
@mock.patch("cachito.workers.tasks.gomod.update_request_with_deps")
@mock.patch("cachito.workers.tasks.gomod.set_request_state")
@mock.patch("cachito.workers.tasks.gomod.resolve_gomod")
def test_fetch_gomod_source(
    mock_resolve_gomod,
    mock_set_request_state,
    mock_update_request_with_deps,
    mock_update_request_with_packages,
    mock_exists,
    auto_detect,
    contains_go_mod,
    dep_replacements,
    expect_state_update,
    sample_deps_replace,
    sample_package,
    sample_env_vars,
):
    mock_request = mock.Mock()
    mock_set_request_state.return_value = mock_request
    mock_exists.return_value = contains_go_mod
    mock_resolve_gomod.return_value = sample_package, sample_deps_replace
    tasks.fetch_gomod_source(1, auto_detect, dep_replacements)

    if expect_state_update:
        mock_set_request_state.assert_called_once_with(
            1, "in_progress", "Fetching the gomod dependencies"
        )
        mock_update_request_with_packages.assert_called_once_with(
            1, [sample_package], "gomod", sample_env_vars
        )
        mock_update_request_with_deps.assert_called_once_with(1, sample_deps_replace)

    bundle_dir = RequestBundleDir(1)

    if auto_detect:
        mock_exists.assert_called_once()
        if contains_go_mod:
            mock_resolve_gomod.assert_called_once_with(
                str(bundle_dir.source_dir), mock_request, dep_replacements
            )
        else:
            mock_resolve_gomod.assert_not_called()
    else:
        mock_resolve_gomod.assert_called_once_with(
            str(bundle_dir.source_dir), mock_request, dep_replacements
        )
        mock_exists.assert_not_called()
