from pathlib import Path
from textwrap import dedent

import pytest

from cachito.errors import ValidationError
from cachito.workers.pkg_managers.ruby import GemMetadata, parse_gemlock


class TestGemlockParsing:
    @pytest.mark.parametrize(
        "file_contents, expected_dependencies",
        (
            # GEM dependency
            (
                dedent(
                    """\
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
                    )
                ],
            ),
        ),
    )
    def test_parsing_of_valid_cases(self, file_contents, expected_dependencies, tmpdir):
        gemfile_lock = tmpdir.join("Gemfile.lock")
        gemfile_lock.write(file_contents)

        dependencies = parse_gemlock(Path(gemfile_lock))

        assert len(dependencies) == len(expected_dependencies)
        for dep, expected_dep in zip(dependencies, expected_dependencies):
            assert dep == expected_dep

    def test_parsing_of_valid_path_dependency(self, tmpdir):
        gemlock_contents = dedent(
            """
            PATH
              remote: vendor/active-docs
              specs:
                active-docs (1.0.0)
                  railties (> 3.1)

            PLATFORMS
              ruby

            DEPENDENCIES
              active-docs!
            """
        )
        expected_dependencies = [GemMetadata("active-docs", "1.0.0", "PATH", "vendor/active-docs")]

        gemfile_lock = tmpdir.join("Gemfile.lock")
        gemfile_lock.write(gemlock_contents)
        tmpdir.mkdir("vendor").mkdir("active-docs")

        dependencies = parse_gemlock(Path(gemfile_lock))

        assert len(dependencies) == len(expected_dependencies)
        for dep, expected_dep in zip(dependencies, expected_dependencies):
            assert dep == expected_dep

    @pytest.mark.parametrize(
        "file_contents, expected_error",
        (
            (
                dedent(
                    """
                    GEM
                      remote: http://rubygems.org/
                      specs:
                        zeitwerk (2.5.4)

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

                    DEPENDENCIES
                      json-schema!
                    """
                ),
                "Ruby GIT dependencies have to use HTTPS protocol.",
            ),
            (
                dedent(
                    """
                    PATH
                      remote: vendor/active-docs
                      specs:
                        active-docs (1.0.0)
                          railties (> 3.1)

                    DEPENDENCIES
                      active-docs!
                    """
                ),
                "PATH dependency references a non-existing path.",
            ),
            (
                dedent(
                    """
                    UNSUPPORTED
                      remote: vendor/active-docs
                      specs:
                        active-docs (1.0.0)
                          railties (> 3.1)

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

                    DEPENDENCIES
                      zeitwerk
                    """
                ),
                "Unspecified name or version",
            ),
        ),
    )
    def test_parsing_of_invalid_cases(self, file_contents, expected_error, tmpdir):
        """Test the invalid use cases of dependencies in a Gemfile.lock file."""
        gemfile_lock = tmpdir.join("Gemfile.lock")
        gemfile_lock.write(file_contents)

        with pytest.raises(ValidationError, match=expected_error):
            parse_gemlock(Path(gemfile_lock))
