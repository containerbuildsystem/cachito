# SPDX-License-Identifier: GPL-3.0-or-later
from unittest import mock

import pytest

from cachito.errors import CachitoError
from cachito.workers import tasks


@pytest.mark.parametrize(
    "dep_replacements, expect_state_update",
    (
        (None, True),
        (None, False),
        (False, [{"name": "github.com/pkg/errors", "type": "gomod", "version": "v0.8.1"}]),
    ),
)
@pytest.mark.parametrize("has_pkg_lvl_deps", (True, False))
@mock.patch("cachito.workers.tasks.gomod.RequestBundleDir")
@mock.patch("cachito.workers.tasks.gomod.update_request_with_package")
@mock.patch("cachito.workers.tasks.gomod.update_request_with_deps")
@mock.patch("cachito.workers.tasks.gomod.set_request_state")
@mock.patch("cachito.workers.tasks.gomod.resolve_gomod")
def test_fetch_gomod_source(
    mock_resolve_gomod,
    mock_set_request_state,
    mock_update_request_with_deps,
    mock_update_request_with_package,
    mock_bundle_dir,
    dep_replacements,
    expect_state_update,
    has_pkg_lvl_deps,
    sample_deps_replace,
    sample_package,
    sample_pkg_deps,
    sample_pkg_lvl_pkg,
    sample_env_vars,
):
    # Add the default environment variables from the configuration
    env_vars = {
        "GO111MODULE": {"value": "on", "kind": "literal"},
        "GOSUMDB": {"value": "off", "kind": "literal"},
    }
    sample_env_vars.update(env_vars)
    mock_request = mock.Mock()
    mock_set_request_state.return_value = mock_request
    pkg_lvl_deps = []
    if has_pkg_lvl_deps:
        pkg_lvl_deps = sample_pkg_deps
    mock_resolve_gomod.return_value = {
        "module": sample_package,
        "module_deps": sample_deps_replace,
        "packages": [{"pkg": sample_pkg_lvl_pkg, "pkg_deps": pkg_lvl_deps}],
    }
    tasks.fetch_gomod_source(1, dep_replacements)
    if expect_state_update:
        mock_set_request_state.assert_called_once_with(
            1, "in_progress", "Fetching the gomod dependencies"
        )
        pkg_calls = [
            mock.call(1, sample_package, sample_env_vars),
        ]
        dep_calls = [
            mock.call(1, sample_package, sample_deps_replace),
        ]
        if has_pkg_lvl_deps:
            dep_calls.append(mock.call(1, sample_pkg_lvl_pkg, sample_pkg_deps))
        else:
            pkg_calls.append(mock.call(1, sample_pkg_lvl_pkg))
        mock_update_request_with_package.assert_has_calls(pkg_calls)
        assert mock_update_request_with_package.call_count == len(pkg_calls)
        mock_update_request_with_deps.assert_has_calls(dep_calls)
        assert mock_update_request_with_deps.call_count == len(dep_calls)

    mock_resolve_gomod.assert_called_once_with(
        str(mock_bundle_dir().source_dir), mock_request, dep_replacements
    )


@pytest.mark.parametrize(
    "ignore_missing_gomod_file, exc_expected", ((True, False), (False, True)),
)
@mock.patch("cachito.workers.tasks.gomod.get_worker_config")
@mock.patch("cachito.workers.tasks.gomod.RequestBundleDir")
@mock.patch("cachito.workers.tasks.gomod.resolve_gomod")
def test_fetch_gomod_source_no_go_mod_file(
    mock_resolve_gomod, mock_bundle_dir, mock_gwc, ignore_missing_gomod_file, exc_expected,
):
    mock_config = mock.Mock()
    mock_config.cachito_gomod_ignore_missing_gomod_file = ignore_missing_gomod_file
    mock_gwc.return_value = mock_config
    mock_bundle_dir.return_value.go_mod_file.exists.return_value = False
    if exc_expected:
        with pytest.raises(CachitoError, match="The go.mod file is missing"):
            tasks.fetch_gomod_source(1)
    else:
        tasks.fetch_gomod_source(1)

    mock_resolve_gomod.assert_not_called()
