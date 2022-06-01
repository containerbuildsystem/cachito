# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
from textwrap import dedent
from unittest import mock

import pytest

from cachito.errors import CachitoError, ValidationError
from cachito.workers.errors import NexusScriptError
from cachito.workers.pkg_managers import rubygems
from cachito.workers.pkg_managers.rubygems import GemMetadata, parse_gemlock


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


class TestGemlockParsing:
    @pytest.mark.parametrize(
        "file_contents, expected_dependencies",
        (
            # GEM dependency
            (
                dedent(
                    """
                    GEM
                      remote: https://rubygems.org/
                      specs:
                        zeitwerk (2.5.4)

                    PLATFORMS
                      ruby

                    DEPENDENCIES
                      zeitwerk
                    """
                ),
                [GemMetadata("zeitwerk", "2.5.4", "GEM", "https://rubygems.org/")],
            ),
            # GIT dependency
            (
                dedent(
                    """
                    GIT
                      remote: https://github.com/3scale/ci_reporter_shell.git
                      revision: 30b30d655512891f56463e5f1fa125ea1f2df886
                      specs:
                        ci_reporter_shell (0.1.0)
                          ci_reporter (~> 2.0)

                    GEM
                      remote: https://rubygems.org/
                      specs:
                        builder (3.2.4)
                        ci_reporter (2.0.0)
                          builder (>= 2.1.2)

                    PLATFORMS
                      ruby

                    DEPENDENCIES
                      ci_reporter_shell!
                    """
                ),
                [
                    GemMetadata(
                        "ci_reporter_shell",
                        "30b30d655512891f56463e5f1fa125ea1f2df886",
                        "GIT",
                        "https://github.com/3scale/ci_reporter_shell.git",
                    ),
                    GemMetadata("ci_reporter", "2.0.0", "GEM", "https://rubygems.org/"),
                    GemMetadata("builder", "3.2.4", "GEM", "https://rubygems.org/"),
                ],
            ),
        ),
    )
    def test_parsing_of_valid_cases(self, file_contents, expected_dependencies, tmpdir):
        gemfile_lock = tmpdir.join("Gemfile.lock")
        gemfile_lock.write(file_contents)

        dependencies = parse_gemlock(Path(gemfile_lock).parent, Path(gemfile_lock))

        assert len(dependencies) == len(expected_dependencies)
        for dep, expected_dep in zip(dependencies, expected_dependencies):
            assert expected_dep == dep

    def test_gemlock_not_a_file(self, tmpdir):
        gemfile_lock = tmpdir.join("Gemfile.lock")

        with pytest.raises(ValidationError) as exc_info:
            parse_gemlock(Path(gemfile_lock).parent, Path(gemfile_lock))

        expected_msg = (
            f"Gemfile.lock at path {gemfile_lock} does not exist or is not a regular file."
        )
        assert expected_msg == str(exc_info.value)

    def test_parsing_of_valid_path_dependency(self, tmpdir):
        gemlock_contents = dedent(
            """
            PATH
              remote: local/zeitwerk
              specs:
                zeitwerk (2.6.0)

            GEM
              remote: https://rubygems.org/
              specs:

            PLATFORMS
              ruby

            DEPENDENCIES
              zeitwerk!
            """
        )
        expected_dependencies = [GemMetadata("zeitwerk", "2.6.0", "PATH", "local/zeitwerk")]

        gemfile_lock = tmpdir.join("Gemfile.lock")
        gemfile_lock.write(gemlock_contents)
        tmpdir.mkdir("local").mkdir("zeitwerk")

        dependencies = parse_gemlock(Path(gemfile_lock).parent, Path(gemfile_lock))

        assert len(dependencies) == len(expected_dependencies)
        for dep, expected_dep in zip(dependencies, expected_dependencies):
            assert expected_dep == dep

    @pytest.mark.parametrize(
        "file_contents, expected_error",
        (
            (
                dedent(
                    """
                    GEM
                      remote: https://rubygems.org/
                      specs:
                        zeitwerk (2.5.4)

                    DEPENDENCIES
                      zeitwerk
                    """
                ),
                "PLATFORMS section of Gemfile.lock has to contain one and only platform - ruby.",
            ),
            (
                dedent(
                    """
                    GEM
                      remote: http://rubygems.org/
                      specs:
                        zeitwerk (2.5.4)

                    PLATFORMS
                      ruby

                    DEPENDENCIES
                      zeitwerk
                    """
                ),
                "Cachito supports only https://rubygems.org/ as a remote for Ruby GEM "
                "dependencies.",
            ),
            (
                dedent(
                    """
                    GIT
                      remote: http://github.com/3scale/json-schema.git
                      revision: 26487618a68443e94d623bb585cb464b07d36702
                      specs:
                        json-schema (3.0.0)
                          addressable (>= 2.4)

                    PLATFORMS
                      ruby

                    DEPENDENCIES
                      json-schema!
                    """
                ),
                "All Ruby GIT dependencies have to use HTTPS protocol.",
            ),
            (
                dedent(
                    """
                    GIT
                      remote: https://github.com/3scale/json-schema.git
                      revision: xxx
                      specs:
                        json-schema (3.0.0)
                          addressable (>= 2.4)

                    PLATFORMS
                      ruby

                    DEPENDENCIES
                      json-schema!
                    """
                ),
                "No git ref for gem: json-schema (expected 40 hexadecimal characters, got: xxx).",
            ),
            (
                dedent(
                    """
                    UNSUPPORTED
                      remote: vendor/active-docs
                      specs:
                        active-docs (1.0.0)
                          railties (> 3.1)

                    PLATFORMS
                      ruby

                    DEPENDENCIES
                      active-docs!
                    """
                ),
                "Gemfile.lock contains unsupported dependency type.",
            ),
            (
                dedent(
                    """
                    GEM
                      remote: https://rubygems.org/
                      specs:
                        zeitwerk

                    PLATFORMS
                      ruby

                    DEPENDENCIES
                      zeitwerk
                    """
                ),
                "Unspecified name or version of a RubyGem.",
            ),
            (
                dedent(
                    """
                    GEM
                      remote: https://rubygems.org/
                      specs:
                        zeitwerk (2.5.4)

                    PLATFORMS
                      ruby
                      x86_64-linux

                    DEPENDENCIES
                      zeitwerk
                    """
                ),
                "PLATFORMS section of Gemfile.lock has to contain one and only platform - ruby.",
            ),
        ),
    )
    def test_parsing_of_invalid_cases(self, file_contents, expected_error, tmpdir):
        """Test the invalid use cases of dependencies in a Gemfile.lock file."""
        gemfile_lock = tmpdir.join("Gemfile.lock")
        gemfile_lock.write(file_contents)

        with pytest.raises(ValidationError) as exc_info:
            parse_gemlock(Path(gemfile_lock).parent, Path(gemfile_lock))

        assert expected_error == str(exc_info.value)

    @pytest.mark.parametrize(
        "file_contents, expected_error",
        (
            (
                dedent(
                    """
                    PATH
                      remote: vendor/active-docs
                      specs:
                        active-docs (1.0.0)

                    PLATFORMS
                      ruby

                    DEPENDENCIES
                      active-docs!
                    """
                ),
                "PATH dependency active-docs references a non-existing path: ",
            ),
        ),
    )
    def test_parsing_of_invalid_paths(self, file_contents, expected_error, tmpdir):
        """Test the invalid path definition in a Gemfile.lock file."""
        gemfile_lock = tmpdir.join("Gemfile.lock")
        gemfile_lock.write(file_contents)

        with pytest.raises(ValidationError) as exc_info:
            parse_gemlock(Path(gemfile_lock).parent, Path(gemfile_lock))

        assert expected_error in str(exc_info.value)

    def test_path_is_not_a_subpath_absolute(self, tmpdir):
        active_docs = Path(tmpdir).joinpath("active-docs")
        active_docs.mkdir()

        gemlock_contents = dedent(
            f"""
            PATH
              remote: {str(active_docs)}
              specs:
                active-docs (1.0.0)

            PLATFORMS
              ruby

            DEPENDENCIES
              active-docs!
            """
        )
        gemfile_lock = tmpdir.mkdir("gem").join("Gemfile.lock")
        gemfile_lock.write(gemlock_contents)
        gemfile_lock = Path(gemfile_lock)

        with pytest.raises(ValueError) as exc_info:
            parse_gemlock(gemfile_lock.parent, gemfile_lock)

        expected_msg = f"{str(active_docs)} is not a subpath of {str(gemfile_lock.parent)}"
        assert expected_msg in str(exc_info.value)

    def test_paths_is_not_a_subpath_relative_path(self, tmpdir):
        gemlock_contents = dedent(
            """
            PATH
              remote: ../active-docs
              specs:
                active-docs (1.0.0)

            PLATFORMS
              ruby

            DEPENDENCIES
              active-docs!
            """
        )
        gemfile_lock = tmpdir.mkdir("gem").join("Gemfile.lock")
        gemfile_lock.write(gemlock_contents)
        gemfile_lock = Path(gemfile_lock)

        active_docs = Path(tmpdir).joinpath("active-docs")
        active_docs.mkdir()

        with pytest.raises(ValueError) as exc_info:
            parse_gemlock(gemfile_lock.parent, gemfile_lock)

        expected_msg = f"{str(active_docs)} is not a subpath of {str(gemfile_lock.parent)}"
        assert expected_msg in str(exc_info.value)
