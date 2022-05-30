from unittest import mock

import pytest

from cachito.errors import CachitoError
from cachito.workers.errors import NexusScriptError
from cachito.workers.pkg_managers import rubygems


class TestNexus:
    """Nexus related tests."""

    @mock.patch("cachito.workers.pkg_managers.rubygems.nexus.execute_script")
    def test_prepare_nexus_for_rubygems_request(self, mock_exec_script):
        """Check whether groovy script is called with proper args."""
        rubygems.prepare_nexus_for_rubygems_request(
            "cachito-rubygems-hosted-1", "cachito-rubygems-raw-1"
        )

        mock_exec_script.assert_called_once_with(
            "rubygems_before_content_staged",
            {
                "rubygems_repository_name": "cachito-rubygems-hosted-1",
                "raw_repository_name": "cachito-rubygems-raw-1",
            },
        )

    @mock.patch("cachito.workers.pkg_managers.rubygems.nexus.execute_script")
    def test_prepare_nexus_for_rubygems_request_failed(self, mock_exec_script):
        """Check whether proper error is raised on groovy script failures."""
        mock_exec_script.side_effect = NexusScriptError()

        expected = "Failed to prepare Nexus for Cachito to stage Rubygems content"
        with pytest.raises(CachitoError, match=expected):
            rubygems.prepare_nexus_for_rubygems_request(
                "cachito-rubygems-hosted-1", "cachito-rubygems-raw-1"
            )
