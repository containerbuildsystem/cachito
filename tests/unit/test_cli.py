import importlib.metadata
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Union
from unittest import mock

import pytest
import typer.testing

from cachi2.core.models.input import Request
from cachi2.core.models.output import RequestOutput
from cachi2.interface.cli import DEFAULT_OUTPUT, DEFAULT_SOURCE, app
from cachi2.interface.logging import LogLevel

runner = typer.testing.CliRunner()


@pytest.fixture
def tmp_cwd(tmp_path: Path) -> Iterator[Path]:
    """Temporarily change working directory to a pytest tmpdir."""
    cwd = Path.cwd()
    os.chdir(tmp_path)
    try:
        yield tmp_path
    finally:
        os.chdir(cwd)


@contextmanager
def mock_fetch_deps(
    expect_request: Optional[Request] = None, output: Optional[RequestOutput] = None
) -> Iterator[mock.MagicMock]:
    output = output or RequestOutput.empty()

    with mock.patch("cachi2.core.package_managers.gomod.fetch_gomod_source") as mock_gomod:
        mock_gomod.return_value = output
        yield mock_gomod

    if expect_request is not None:
        mock_gomod.assert_called_once_with(expect_request)


def invoke_expecting_sucess(app, args: list[str]) -> typer.testing.Result:
    result = runner.invoke(app, args, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return result


def assert_pattern_in_output(pattern: Union[str, re.Pattern], output: str) -> None:
    if isinstance(pattern, re.Pattern):
        match = bool(pattern.search(output))
    else:
        match = pattern in output

    assert match, f"pattern {pattern!r} not found!\noutput:\n{output}"


class TestTopLevelOpts:
    def test_version_option(self):
        expect_version = importlib.metadata.version("cachi2")
        result = invoke_expecting_sucess(app, ["--version"])
        assert result.output == f"cachi2 {expect_version}\n"


class TestLogLevelOpt:
    @mock.patch("cachi2.interface.cli.setup_logging")
    @pytest.mark.parametrize(
        "loglevel_args, expected_level",
        [
            ([], "INFO"),
            (["--log-level=debug"], "DEBUG"),
            (["--log-level", "WARNING"], "WARNING"),
        ],
    )
    def test_loglevel_option(
        self,
        mock_setup_logging,
        loglevel_args: list[str],
        expected_level: str,
        tmp_cwd,
    ):
        args = ["fetch-deps", "--package=gomod", *loglevel_args]

        with mock_fetch_deps():
            invoke_expecting_sucess(app, args)

        mock_setup_logging.assert_called_once_with(LogLevel(expected_level))

    @mock.patch("cachi2.interface.cli.setup_logging")
    def test_unknown_loglevel(self, mock_setup_logging, tmp_cwd):
        args = ["fetch-deps", "--package=gomod", "--log-level=unknown"]
        result = runner.invoke(app, args)
        assert result.exit_code != 0
        assert "Error: Invalid value for '--log-level': 'unknown' is not one of" in result.output


class TestFetchDeps:
    @pytest.mark.parametrize(
        "path_args, expect_source, expect_output",
        [
            (
                [],
                f"{{cwd}}/{DEFAULT_SOURCE}",
                f"{{cwd}}/{DEFAULT_OUTPUT}",
            ),
            (
                ["--source=./source/dir", "--output=./output/dir"],
                "{cwd}/source/dir",
                "{cwd}/output/dir",
            ),
            (
                ["--source={cwd}/source/dir", "--output={cwd}/output/dir"],
                "{cwd}/source/dir",
                "{cwd}/output/dir",
            ),
        ],
    )
    def test_specify_paths(
        self, path_args: list[str], expect_source: str, expect_output: str, tmp_cwd: Path
    ):
        tmp_cwd.joinpath("source", "dir").mkdir(parents=True, exist_ok=True)

        source_abspath = expect_source.format(cwd=tmp_cwd)
        output_abspath = expect_output.format(cwd=tmp_cwd)
        expect_request = Request(
            source_dir=source_abspath,
            output_dir=output_abspath,
            packages=[{"type": "gomod"}],
        )

        path_args = [arg.format(cwd=tmp_cwd) for arg in path_args]

        with mock_fetch_deps(expect_request):
            invoke_expecting_sucess(app, ["fetch-deps", *path_args, "--package=gomod"])

    @pytest.mark.parametrize(
        "path_args, expect_error",
        [
            (["--source=no-such-dir"], "'--source': Directory 'no-such-dir' does not exist"),
            (["--source=/no-such-dir"], "'--source': Directory '/no-such-dir' does not exist"),
            (["--source=not-a-directory"], "'--source': Directory 'not-a-directory' is a file"),
            (["--output=not-a-directory"], "'--output': Directory 'not-a-directory' is a file"),
        ],
    )
    def test_invalid_paths(self, path_args: list[str], expect_error: str, tmp_cwd: Path):
        tmp_cwd.joinpath("not-a-directory").touch()

        result = runner.invoke(app, ["fetch-deps", *path_args])
        assert result.exit_code != 0
        assert expect_error in result.output

    def test_no_packages(self):
        result = runner.invoke(app, ["fetch-deps"])
        assert result.exit_code != 0
        assert "Error: Missing option '--package'" in result.output

    @pytest.mark.parametrize(
        "package_args, expect_packages",
        [
            # specify a single basic package
            (["--package=gomod"], [{"type": "gomod"}]),
            (['--package={"type": "gomod"}'], [{"type": "gomod"}]),
            (['--package=[{"type": "gomod"}]'], [{"type": "gomod"}]),
            # specify multiple packages
            (
                ['--package={"type": "gomod"}', '--package={"type": "gomod", "path": "pkg_a"}'],
                [{"type": "gomod"}, {"type": "gomod", "path": "pkg_a"}],
            ),
            (
                ['--package=[{"type": "gomod"}, {"type": "gomod", "path": "pkg_a"}]'],
                [{"type": "gomod"}, {"type": "gomod", "path": "pkg_a"}],
            ),
            (
                [
                    "--package=gomod",
                    '--package={"type": "gomod", "path": "pkg_a"}',
                    '--package=[{"type": "gomod", "path": "pkg_b"}]',
                ],
                [
                    {"type": "gomod"},
                    {"type": "gomod", "path": "pkg_a"},
                    {"type": "gomod", "path": "pkg_b"},
                ],
            ),
        ],
    )
    def test_specify_packages(
        self, package_args: list[str], expect_packages: list[dict], tmp_cwd: Path
    ):
        tmp_cwd.joinpath("pkg_a").mkdir(exist_ok=True)
        tmp_cwd.joinpath("pkg_b").mkdir(exist_ok=True)

        expect_request = Request(
            source_dir=tmp_cwd / DEFAULT_SOURCE,
            output_dir=tmp_cwd / DEFAULT_OUTPUT,
            packages=expect_packages,
        )
        with mock_fetch_deps(expect_request):
            invoke_expecting_sucess(app, ["fetch-deps", *package_args])

    @pytest.mark.parametrize(
        "package_args, expect_error_lines",
        [
            (
                ["--package={notjson}"],
                ["--package: looks like JSON but is not valid JSON"],
            ),
            (
                ["--package=[notjson]"],
                ["--package: looks like JSON but is not valid JSON"],
            ),
            (
                ["--package=gomod", "--package={notjson}"],
                ["--package: looks like JSON but is not valid JSON"],
            ),
            (
                ["--package=idk"],
                [
                    "1 validation error for Request",
                    "packages -> 0 -> type",
                    re.compile(r"unexpected value; permitted: .* given=idk;"),
                ],
            ),
            (
                ["--package=", '--package={"type": "idk"}'],
                [
                    "2 validation errors for Request",
                    "packages -> 0 -> type",
                    re.compile(r"unexpected value; permitted: .* given=;"),
                    "packages -> 1 -> type",
                    re.compile(r"unexpected value; permitted: .* given=idk;"),
                ],
            ),
            (
                ["--package={}"],
                ["1 validation error for Request", "packages -> 0 -> type", "field required"],
            ),
            (
                ['--package=[{"type": "gomod"}, {}]', "--package={}"],
                [
                    "2 validation errors for Request",
                    "packages -> 1 -> type",
                    "packages -> 2 -> type",
                    "field required",
                ],
            ),
            (
                ['--package={"type": "gomod", "path": "/absolute"}'],
                [
                    "1 validation error for Request",
                    "packages -> 0 -> path",
                    "package path must be relative: /absolute",
                ],
            ),
            (
                ['--package={"type": "gomod", "path": "weird/../subpath"}'],
                [
                    "1 validation error for Request",
                    "packages -> 0 -> path",
                    "package path contains ..: weird/../subpath",
                ],
            ),
            (
                ['--package={"type": "gomod", "path": "suspicious-symlink"}'],
                [
                    "1 validation error for Request",
                    "packages -> 0",
                    "package path (a symlink?) leads outside source directory: suspicious-symlink",
                ],
            ),
            (
                ['--package={"type": "gomod", "path": "no-such-dir"}'],
                [
                    "1 validation error for Request",
                    "packages -> 0",
                    "package path does not exist (or is not a directory): no-such-dir",
                ],
            ),
            (
                ['--package={"type": "gomod", "what": "dunno"}'],
                [
                    "1 validation error for Request",
                    "packages -> 0 -> what",
                    "extra fields not permitted",
                ],
            ),
        ],
    )
    def test_invalid_packages(
        self, package_args: list[str], expect_error_lines: list[str], tmp_cwd: Path
    ):
        tmp_cwd.joinpath("suspicious-symlink").symlink_to("..")

        result = runner.invoke(app, ["fetch-deps", *package_args])
        assert result.exit_code != 0

        for pattern in expect_error_lines:
            assert_pattern_in_output(pattern, result.output)

    @pytest.mark.parametrize(
        "flag_args, expect_flags",
        [
            ([], {}),
            (["--gomod-vendor"], {"gomod-vendor"}),
            (["--flags=gomod-vendor"], {"gomod-vendor"}),
            (["--gomod-vendor", "--flags=gomod-vendor"], {"gomod-vendor"}),
            (
                [
                    "--gomod-vendor",
                    "--gomod-vendor-check",
                    "--cgo-disable",
                    "--force-gomod-tidy",
                ],
                {"gomod-vendor", "gomod-vendor-check", "cgo-disable", "force-gomod-tidy"},
            ),
            (
                ["--flags=gomod-vendor,gomod-vendor-check, cgo-disable,\tforce-gomod-tidy"],
                {"gomod-vendor", "gomod-vendor-check", "cgo-disable", "force-gomod-tidy"},
            ),
        ],
    )
    def test_specify_flags(self, flag_args: list[str], expect_flags: set[str], tmp_cwd):
        expect_request = Request(
            source_dir=tmp_cwd / DEFAULT_SOURCE,
            output_dir=tmp_cwd / DEFAULT_OUTPUT,
            packages=[{"type": "gomod"}],
            flags=frozenset(expect_flags),
        )
        with mock_fetch_deps(expect_request):
            invoke_expecting_sucess(app, ["fetch-deps", "--package=gomod", *flag_args])

    @pytest.mark.parametrize(
        "flag_args, expect_error",
        [
            (["--no-such-flag"], "Error: No such option: --no-such-flag"),
            (
                ["--flags=no-such-flag"],
                re.compile(
                    r"1 validation error for Request\n"
                    r"flags -> 0\n"
                    r"  unexpected value; permitted: .* given=no-such-flag",
                ),
            ),
        ],
    )
    def test_invalid_flags(self, flag_args: list[str], expect_error: str):
        result = runner.invoke(app, ["fetch-deps", "--package=gomod", *flag_args])
        assert result.exit_code != 0
        assert_pattern_in_output(expect_error, result.output)

    @pytest.mark.parametrize(
        "request_output",
        [
            RequestOutput.empty(),
            RequestOutput(
                packages=[
                    {
                        "name": "cool-package",
                        "version": "v1.0.0",
                        "type": "gomod",
                        "path": ".",
                        "dependencies": [],
                    },
                ],
                environment_variables=[
                    {"name": "GOMOD_SOMETHING", "value": "yes", "kind": "literal"},
                ],
            ),
        ],
    )
    def test_write_json_output(self, request_output: RequestOutput, tmp_cwd: Path):
        with mock_fetch_deps(output=request_output):
            invoke_expecting_sucess(app, ["fetch-deps", "--package=gomod"])

        output_json = tmp_cwd / DEFAULT_OUTPUT / "output.json"
        written_output = RequestOutput.parse_file(output_json)

        assert written_output == request_output
