# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import re
from pathlib import Path
from textwrap import dedent
from unittest import mock
from urllib.parse import urlparse
from xml.etree import ElementTree

import pytest
import requests

from cachito.errors import CachitoError, ValidationError
from cachito.workers.errors import NexusScriptError
from cachito.workers.pkg_managers import pip, general
from cachito.workers.requests import requests_session
from tests.helper_utils import write_file_tree


GIT_REF = "9a557920b2a6d4110f838506120904a6fda421a2"


def setup_module():
    """Re-enable logging that was disabled at some point in previous tests."""
    pip.log.disabled = False
    pip.log.setLevel(logging.DEBUG)


@pytest.mark.parametrize("py_exists", [True, False])
@pytest.mark.parametrize("py_name", ["name_in_setup_py", None])
@pytest.mark.parametrize("py_version", ["version_in_setup_py", None])
@pytest.mark.parametrize("cfg_exists", [True, False])
@pytest.mark.parametrize("cfg_name", ["name_in_setup_cfg", None])
@pytest.mark.parametrize("cfg_version", ["version_in_setup_cfg", None])
@mock.patch("cachito.workers.pkg_managers.pip.SetupCFG")
@mock.patch("cachito.workers.pkg_managers.pip.SetupPY")
def test_get_pip_metadata(
    mock_setup_py,
    mock_setup_cfg,
    py_exists,
    py_name,
    py_version,
    cfg_exists,
    cfg_name,
    cfg_version,
    caplog,
):
    """
    Test get_pip_metadata() function.

    More thorough tests of setup.py and setup.cfg handling are in their respective classes.
    """
    if not py_exists:
        py_name = None
        py_version = None
    if not cfg_exists:
        cfg_name = None
        cfg_version = None

    setup_py = mock_setup_py.return_value
    setup_py.exists.return_value = py_exists
    setup_py.get_name.return_value = py_name
    setup_py.get_version.return_value = py_version

    setup_cfg = mock_setup_cfg.return_value
    setup_cfg.exists.return_value = cfg_exists
    setup_cfg.get_name.return_value = cfg_name
    setup_cfg.get_version.return_value = cfg_version

    expect_name = py_name or cfg_name
    expect_version = py_version or cfg_version

    if expect_name and expect_version:
        name, version = pip.get_pip_metadata("/foo/package_dir")

        assert name == expect_name
        assert version == expect_version
    else:
        with pytest.raises(CachitoError) as exc_info:
            pip.get_pip_metadata("/foo/package_dir")

        if expect_name:
            missing = "version"
        elif expect_version:
            missing = "name"
        else:
            missing = "name, version"

        assert str(exc_info.value) == f"Could not resolve package metadata: {missing}"

    assert setup_py.get_name.called == py_exists
    assert setup_py.get_version.called == py_exists

    assert setup_cfg.get_name.called == (py_name is None and cfg_exists)
    assert setup_cfg.get_version.called == (py_version is None and cfg_exists)

    if py_exists:
        assert "Extracting metadata from setup.py" in caplog.text
    else:
        assert "No setup.py in directory, package is likely not Pip compatible" in caplog.text

    if not (py_name and py_version) and cfg_exists:
        assert "Filling in missing metadata from setup.cfg" in caplog.text

    if expect_name:
        assert f"Resolved package name: '{expect_name}'" in caplog.text
    else:
        assert "Could not resolve package name" in caplog.text

    if expect_version:
        assert f"Resolved package version: '{expect_version}'" in caplog.text
    else:
        assert "Could not resolve package version" in caplog.text


class TestSetupCFG:
    """SetupCFG tests."""

    @pytest.mark.parametrize("exists", [True, False])
    def test_exists(self, exists, tmpdir):
        """Test file existence check."""
        if exists:
            tmpdir.join("setup.cfg").write("")

        setup_cfg = pip.SetupCFG(tmpdir.strpath)
        assert setup_cfg.exists() == exists

    @pytest.mark.parametrize(
        "cfg_content, expect_name, expect_logs",
        [
            (
                "",
                None,
                ["Parsing setup.cfg at '{tmpdir}/setup.cfg'", "No metadata.name in setup.cfg"],
            ),
            ("[metadata]", None, ["No metadata.name in setup.cfg"]),
            (
                dedent(
                    """\
                    [metadata]
                    name = foo
                    """
                ),
                "foo",
                [
                    "Parsing setup.cfg at '{tmpdir}/setup.cfg'",
                    "Found metadata.name in setup.cfg: 'foo'",
                ],
            ),
            (
                "[malformed",
                None,
                [
                    "Parsing setup.cfg at '{tmpdir}/setup.cfg'",
                    "Failed to parse setup.cfg: File contains no section headers",
                    "No metadata.name in setup.cfg",
                ],
            ),
        ],
    )
    def test_get_name(self, cfg_content, expect_name, expect_logs, tmpdir, caplog):
        """Test get_name() method."""
        setup_cfg = tmpdir.join("setup.cfg")
        setup_cfg.write(cfg_content)

        assert pip.SetupCFG(tmpdir.strpath).get_name() == expect_name
        self._assert_has_logs(expect_logs, tmpdir, caplog)

    @pytest.mark.parametrize(
        "cfg_content, expect_version, expect_logs",
        [
            (
                "",
                None,
                ["Parsing setup.cfg at '{tmpdir}/setup.cfg'", "No metadata.version in setup.cfg"],
            ),
            ("[metadata]", None, ["No metadata.version in setup.cfg"]),
            (
                dedent(
                    """\
                    [metadata]
                    version = 1.0.0
                    """
                ),
                "1.0.0",
                [
                    "Parsing setup.cfg at '{tmpdir}/setup.cfg'",
                    "Resolving metadata.version in setup.cfg from '1.0.0'",
                    "Found metadata.version in setup.cfg: '1.0.0'",
                ],
            ),
            (
                "[malformed",
                None,
                [
                    "Parsing setup.cfg at '{tmpdir}/setup.cfg'",
                    "Failed to parse setup.cfg: File contains no section headers",
                    "No metadata.version in setup.cfg",
                ],
            ),
        ],
    )
    def test_get_version_basic(self, cfg_content, expect_version, expect_logs, tmpdir, caplog):
        """Test get_version() method with basic cases."""
        setup_cfg = tmpdir.join("setup.cfg")
        setup_cfg.write(cfg_content)

        assert pip.SetupCFG(tmpdir.strpath).get_version() == expect_version
        self._assert_has_logs(expect_logs, tmpdir, caplog)

    def _assert_has_logs(self, expect_logs, tmpdir, caplog):
        for log in expect_logs:
            assert log.format(tmpdir=tmpdir.strpath) in caplog.text

    def _test_version_with_file_tree(
        self, project_tree, expect_version, expect_logs, expect_error, tmpdir, caplog
    ):
        """Test resolving version from file: or attr: directive."""
        write_file_tree(project_tree, tmpdir.strpath)
        setup_cfg = pip.SetupCFG(tmpdir.strpath)

        if expect_error is None:
            assert setup_cfg.get_version() == expect_version
        else:
            with pytest.raises(ValidationError) as exc_info:
                setup_cfg.get_version()
            assert str(exc_info.value) == expect_error.format(tmpdir=tmpdir.strpath)

        logs = expect_logs.copy()
        # Does not actually have to be at index 0, this is just to be more obvious
        logs.insert(0, f"Parsing setup.cfg at '{tmpdir.join('setup.cfg')}'")
        if expect_version is not None:
            logs.append(f"Found metadata.version in setup.cfg: '{expect_version}'")
        elif expect_error is None:
            logs.append("Failed to resolve metadata.version in setup.cfg")

        self._assert_has_logs(logs, tmpdir, caplog)

    @pytest.mark.parametrize(
        "project_tree, expect_version, expect_logs, expect_error",
        [
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = file: missing.txt
                        """
                    ),
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'file: missing.txt'",
                    "Version file 'missing.txt' does not exist or is not a file",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = file: version.txt
                        """
                    ),
                    "version.txt": "1.0.0",
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'file: version.txt'",
                    "Read version from 'version.txt': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = file: version.txt
                        """
                    ),
                    "version.txt": "\n1.0.0\n",
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'file: version.txt'",
                    "Read version from 'version.txt': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = file: data/version.txt
                        """
                    ),
                    "data": {"version.txt": "1.0.0"},
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'file: data/version.txt'",
                    "Read version from 'data/version.txt': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = file: ../version.txt
                        """
                    ),
                },
                None,
                ["Resolving metadata.version in setup.cfg from 'file: ../version.txt'"],
                "'../version.txt' is not a subpath of '{tmpdir}'",
            ),
        ],
    )
    def test_get_version_file(
        self, project_tree, expect_version, expect_logs, expect_error, tmpdir, caplog
    ):
        """Test get_version() method with file: directive."""
        self._test_version_with_file_tree(
            project_tree, expect_version, expect_logs, expect_error, tmpdir, caplog
        )

    @pytest.mark.parametrize(
        "project_tree, expect_version, expect_logs, expect_error",
        [
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: missing_file.__ver__
                        """
                    ),
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'attr: missing_file.__ver__'",
                    "Attempting to find attribute '__ver__' in 'missing_file'",
                    "Module 'missing_file' not found",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: syntax_error.__ver__
                        """
                    ),
                    "syntax_error.py": "syntax error",
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'attr: syntax_error.__ver__'",
                    "Attempting to find attribute '__ver__' in 'syntax_error'",
                    "Found module 'syntax_error' at '{tmpdir}/syntax_error.py'",
                    "Syntax error when parsing module: invalid syntax (syntax_error.py, line 1)",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: missing_attr.__ver__
                        """
                    ),
                    "missing_attr.py": "",
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'attr: missing_attr.__ver__'",
                    "Attempting to find attribute '__ver__' in 'missing_attr'",
                    "Found module 'missing_attr' at '{tmpdir}/missing_attr.py'",
                    "Could not find attribute in 'missing_attr': '__ver__' not found",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: not_a_literal.__ver__
                        """
                    ),
                    "not_a_literal.py": "__ver__ = get_version()",
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'attr: not_a_literal.__ver__'",
                    "Attempting to find attribute '__ver__' in 'not_a_literal'",
                    "Found module 'not_a_literal' at '{tmpdir}/not_a_literal.py'",
                    (
                        "Could not find attribute in 'not_a_literal': "
                        "'__ver__' is not assigned to a literal expression"
                    ),
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: module.__ver__
                        """
                    ),
                    "module.py": "__ver__ = '1.0.0'",
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: module.__ver__'",
                    "Attempting to find attribute '__ver__' in 'module'",
                    "Found module 'module' at '{tmpdir}/module.py'",
                    "Found attribute '__ver__' in 'module': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: package.__ver__
                        """
                    ),
                    "package": {"__init__.py": "__ver__ = '1.0.0'"},
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: package.__ver__'",
                    "Attempting to find attribute '__ver__' in 'package'",
                    "Found module 'package' at '{tmpdir}/package/__init__.py'",
                    "Found attribute '__ver__' in 'package': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: package.module.__ver__
                        """
                    ),
                    "package": {"module.py": "__ver__ = '1.0.0'"},
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: package.module.__ver__'",
                    "Attempting to find attribute '__ver__' in 'package.module'",
                    "Found module 'package.module' at '{tmpdir}/package/module.py'",
                    "Found attribute '__ver__' in 'package.module': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: package_before_module.__ver__
                        """
                    ),
                    "package_before_module": {"__init__.py": "__ver__ = '1.0.0'"},
                    "package_before_module.py": "__ver__ = '2.0.0'",
                },
                "1.0.0",
                [
                    (
                        "Resolving metadata.version in setup.cfg from "
                        "'attr: package_before_module.__ver__'"
                    ),
                    "Attempting to find attribute '__ver__' in 'package_before_module'",
                    (
                        "Found module 'package_before_module' at "
                        "'{tmpdir}/package_before_module/__init__.py'"
                    ),
                    "Found attribute '__ver__' in 'package_before_module': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: __ver__
                        """
                    ),
                    "__init__.py": "__ver__ = '1.0.0'",
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: __ver__'",
                    "Attempting to find attribute '__ver__' in '__init__'",
                    "Found module '__init__' at '{tmpdir}/__init__.py'",
                    "Found attribute '__ver__' in '__init__': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: .__ver__
                        """
                    ),
                    "__init__.py": "__ver__ = '1.0.0'",
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: .__ver__'",
                    "Attempting to find attribute '__ver__' in '__init__'",
                    "Found module '__init__' at '{tmpdir}/__init__.py'",
                    "Found attribute '__ver__' in '__init__': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: is_tuple.__ver__
                        """
                    ),
                    "is_tuple.py": "__ver__ = (1, 0, 'alpha', 1)",
                },
                "1.0a1",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: is_tuple.__ver__'",
                    "Attempting to find attribute '__ver__' in 'is_tuple'",
                    "Found module 'is_tuple' at '{tmpdir}/is_tuple.py'",
                    "Found attribute '__ver__' in 'is_tuple': (1, 0, 'alpha', 1)",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: is_integer.__ver__
                        """
                    ),
                    "is_integer.py": "__ver__ = 1",
                },
                "1",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: is_integer.__ver__'",
                    "Attempting to find attribute '__ver__' in 'is_integer'",
                    "Found module 'is_integer' at '{tmpdir}/is_integer.py'",
                    "Found attribute '__ver__' in 'is_integer': 1",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: ..module.__ver__
                        """
                    ),
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'attr: ..module.__ver__'",
                    "Attempting to find attribute '__ver__' in '..module'",
                ],
                "'..module' is not an accepted module name",
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: /root.module.__ver__
                        """
                    ),
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'attr: /root.module.__ver__'",
                    "Attempting to find attribute '__ver__' in '/root.module'",
                ],
                "'/root.module' is not an accepted module name",
            ),
        ],
    )
    def test_get_version_attr(
        self, project_tree, expect_version, expect_logs, expect_error, tmpdir, caplog
    ):
        """Test get_version() method with attr: directive."""
        self._test_version_with_file_tree(
            project_tree, expect_version, expect_logs, expect_error, tmpdir, caplog
        )

    @pytest.mark.parametrize(
        "project_tree, expect_version, expect_logs, expect_error",
        [
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: module.__ver__

                        [options]
                        package_dir =
                            =src
                        """
                    ),
                    "src": {"module.py": "__ver__ = '1.0.0'"},
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: module.__ver__'",
                    "Attempting to find attribute '__ver__' in 'module'",
                    "Custom path set for all root modules: 'src'",
                    "Found module 'module' at '{tmpdir}/src/module.py'",
                    "Found attribute '__ver__' in 'module': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: module.__ver__

                        [options]
                        package_dir =
                            module = src/module
                        """
                    ),
                    "src": {"module.py": "__ver__ = '1.0.0'"},
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: module.__ver__'",
                    "Attempting to find attribute '__ver__' in 'module'",
                    "Custom path set for root module 'module': 'src/module'",
                    "Found module 'module' at '{tmpdir}/src/module.py'",
                    "Found attribute '__ver__' in 'module': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: module.__ver__

                        [options]
                        package_dir = module=src/module, =src
                        """
                    ),
                    "src": {"module.py": "__ver__ = '1.0.0'"},
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: module.__ver__'",
                    "Attempting to find attribute '__ver__' in 'module'",
                    "Custom path set for root module 'module': 'src/module'",
                    "Found module 'module' at '{tmpdir}/src/module.py'",
                    "Found attribute '__ver__' in 'module': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: module.__ver__

                        [options]
                        package_dir =
                            = ..
                        """
                    ),
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'attr: module.__ver__'",
                    "Attempting to find attribute '__ver__' in 'module'",
                    "Custom path set for all root modules: '..'",
                ],
                "'../module' is not a subpath of '{tmpdir}'",
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: module.__ver__

                        [options]
                        package_dir =
                            module = ../module
                        """
                    ),
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'attr: module.__ver__'",
                    "Attempting to find attribute '__ver__' in 'module'",
                    "Custom path set for root module 'module': '../module'",
                ],
                "'../module' is not a subpath of '{tmpdir}'",
            ),
        ],
    )
    def test_get_version_attr_with_package_dir(
        self, project_tree, expect_version, expect_logs, expect_error, tmpdir, caplog
    ):
        """Test get_version() method with attr: directive and options.package_dir."""
        self._test_version_with_file_tree(
            project_tree, expect_version, expect_logs, expect_error, tmpdir, caplog
        )


class TestSetupPY:
    """SetupPY tests."""

    @pytest.mark.parametrize("exists", [True, False])
    def test_exists(self, exists, tmpdir):
        """Test file existence check."""
        if exists:
            tmpdir.join("setup.py").write("")

        setup_py = pip.SetupPY(tmpdir.strpath)
        assert setup_py.exists() == exists

    def _test_get_value(self, tmpdir, caplog, script_content, expect_val, expect_logs, what="name"):
        """Test getting name or version from setup.py."""
        tmpdir.join("setup.py").write(script_content.format(what=what))
        setup_py = pip.SetupPY(tmpdir.strpath)

        if what == "name":
            value = setup_py.get_name()
        elif what == "version":
            value = setup_py.get_version()
        else:
            assert False, "'what' must be one of 'name', 'version'"

        assert value == expect_val

        logs = expect_logs.copy()
        # Does not actually have to be at index 0, this is just to be more obvious
        logs.insert(0, f"Parsing setup.py at '{tmpdir.join('setup.py')}'")
        if expect_val is None:
            msg = (
                "Version in setup.py was either not found, or failed to resolve to a valid value"
                if what == "version"
                else "Name in setup.py was either not found, or failed to resolve to a valid string"
            )
            logs.append(msg)
        else:
            logs.append(f"Found {what} in setup.py: '{expect_val}'")

        for log in logs:
            assert log.format(tmpdir=tmpdir.strpath, what=what) in caplog.text

    @pytest.mark.parametrize(
        "script_content, expect_val, expect_logs",
        [
            ("", None, ["File does not seem to have a setup call"]),
            ("my_module.setup()", None, ["File does not seem to have a setup call"]),
            (
                "syntax error",
                None,
                ["Syntax error when parsing setup.py: invalid syntax (setup.py, line 1)"],
            ),
            (
                # Note that it absolutely does not matter whether you imported anything
                "setup()",
                None,
                [
                    "Found setup call on line 1",
                    "Pseudo-path: Module.body[0] -> Expr(#1).value",
                    "setup kwarg '{what}' not found",
                ],
            ),
            (
                "setuptools.setup()",
                None,
                [
                    "Found setup call on line 1",
                    "Pseudo-path: Module.body[0] -> Expr(#1).value",
                    "setup kwarg '{what}' not found",
                ],
            ),
            (
                dedent(
                    """\
                    from setuptools import setup; setup()
                    """
                ),
                None,
                [
                    "Found setup call on line 1",
                    "Pseudo-path: Module.body[1] -> Expr(#1).value",
                    "setup kwarg '{what}' not found",
                ],
            ),
            (
                dedent(
                    """\
                    from setuptools import setup

                    setup()
                    """
                ),
                None,
                [
                    "Found setup call on line 3",
                    "Pseudo-path: Module.body[1] -> Expr(#3).value",
                    "setup kwarg '{what}' not found",
                ],
            ),
            (
                dedent(
                    """\
                    from setuptools import setup

                    setup({what}=None)
                    """
                ),
                None,
                [
                    "Found setup call on line 3",
                    "Pseudo-path: Module.body[1] -> Expr(#3).value",
                    "setup kwarg '{what}' is a literal: None",
                ],
            ),
            (
                dedent(
                    """\
                    from setuptools import setup

                    setup({what}="foo")
                    """
                ),
                "foo",
                [
                    "Found setup call on line 3",
                    "Pseudo-path: Module.body[1] -> Expr(#3).value",
                    "setup kwarg '{what}' is a literal: 'foo'",
                ],
            ),
        ],
    )
    @pytest.mark.parametrize("what", ["name", "version"])
    def test_get_kwarg_literal(self, script_content, expect_val, expect_logs, what, tmpdir, caplog):
        """
        Basic tests for getting kwarg value from a literal.

        Test cases only call setup() at top level, location of setup call is much more
        important for tests with variables.
        """
        self._test_get_value(tmpdir, caplog, script_content, expect_val, expect_logs, what=what)

    @pytest.mark.parametrize(
        "version_val, expect_version",
        [("1.0.alpha.1", "1.0a1"), (1, "1"), ((1, 0, "alpha", 1), "1.0a1")],
    )
    def test_get_version_special(self, version_val, expect_version, tmpdir, caplog):
        """Test cases where version values get special handling."""
        script_content = f"setup(version={version_val!r})"
        expect_logs = [
            "Found setup call on line 1",
            "Pseudo-path: Module.body[0] -> Expr(#1).value",
            f"setup kwarg 'version' is a literal: {version_val!r}",
        ]
        self._test_get_value(
            tmpdir, caplog, script_content, expect_version, expect_logs, what="version"
        )

    @pytest.mark.parametrize(
        "script_content, expect_val, expect_logs",
        [
            (
                "setup({what}=foo)",
                None,
                [
                    "Pseudo-path: Module.body[0] -> Expr(#1).value",
                    "Variable 'foo' not found along the setup call branch",
                ],
            ),
            (
                dedent(
                    """\
                    setup({what}=foo)

                    foo = "bar"
                    """
                ),
                None,
                [
                    "Pseudo-path: Module.body[0] -> Expr(#1).value",
                    "Variable 'foo' not found along the setup call branch",
                ],
            ),
            (
                dedent(
                    """\
                    if True:
                        foo = "bar"

                    setup({what}=foo)
                    """
                ),
                None,
                [
                    "Pseudo-path: Module.body[1] -> Expr(#4).value",
                    "Variable 'foo' not found along the setup call branch",
                ],
            ),
            (
                dedent(
                    """\
                    foo = get_version()

                    setup({what}=foo)
                    """
                ),
                None,
                [
                    "Pseudo-path: Module.body[1] -> Expr(#3).value",
                    "Variable cannot be resolved: 'foo' is not assigned to a literal expression",
                ],
            ),
            (
                dedent(
                    """\
                    foo = None

                    setup({what}=foo)
                    """
                ),
                None,
                ["Pseudo-path: Module.body[1] -> Expr(#3).value", "Found variable 'foo': None"],
            ),
            (
                dedent(
                    """\
                    foo = "bar"

                    setup({what}=foo)
                    """
                ),
                "bar",
                ["Pseudo-path: Module.body[1] -> Expr(#3).value", "Found variable 'foo': 'bar'"],
            ),
            (
                dedent(
                    """\
                    foo = "bar"

                    if True:
                        setup({what}=foo)
                    """
                ),
                "bar",
                [
                    "Pseudo-path: Module.body[1] -> If(#3).body[0] -> Expr(#4).value",
                    "Found variable 'foo': 'bar'",
                ],
            ),
            (
                # Variable will be found only if it is in the same branch
                dedent(
                    """\
                    if True:
                        foo = "bar"
                    else:
                        setup({what}=foo)
                    """
                ),
                None,
                [
                    "Pseudo-path: Module.body[0] -> If(#1).orelse[0] -> Expr(#4).value",
                    "Variable 'foo' not found along the setup call branch",
                ],
            ),
            (
                dedent(
                    """\
                    if True:
                        foo = "bar"
                        setup({what}=foo)
                    """
                ),
                "bar",
                [
                    "Pseudo-path: Module.body[0] -> If(#1).body[1] -> Expr(#3).value",
                    "Found variable 'foo': 'bar'",
                ],
            ),
            (
                # Try statements are kinda special, because not only do they have 3 bodies,
                # they also have a list of 'handlers' (1 for each except clause)
                dedent(
                    """\
                    try:
                        pass
                    except A:
                        foo = "bar"
                    except B:
                        setup({what}=foo)
                    else:
                        pass
                    finally:
                        pass
                    """
                ),
                None,
                [
                    (
                        "Pseudo-path: Module.body[0] -> Try(#1).handlers[1] "
                        "-> ExceptHandler(#5).body[0] -> Expr(#6).value"
                    ),
                    "Variable 'foo' not found along the setup call branch",
                ],
            ),
            (
                dedent(
                    """\
                    try:
                        pass
                    except A:
                        pass
                    except B:
                        foo = "bar"
                        setup({what}=foo)
                    else:
                        pass
                    finally:
                        pass
                    """
                ),
                "bar",
                [
                    (
                        "Pseudo-path: Module.body[0] -> Try(#1).handlers[1] "
                        "-> ExceptHandler(#5).body[1] -> Expr(#7).value"
                    ),
                    "Found variable 'foo': 'bar'",
                ],
            ),
            (
                # setup() inside a FunctionDef is pretty much the same thing as setup()
                # inside an If, except this could support late binding and doesn't
                dedent(
                    """\
                    def f():
                        setup({what}=foo)

                    foo = "bar"

                    f()
                    """
                ),
                None,
                [
                    "Pseudo-path: Module.body[0] -> FunctionDef(#1).body[0] -> Expr(#2).value",
                    "Variable 'foo' not found along the setup call branch",
                ],
            ),
            (
                # Variable defined closer should take precedence
                dedent(
                    """\
                    foo = "baz"

                    if True:
                        foo = "bar"
                        setup({what}=foo)
                    """
                ),
                "bar",
                [
                    "Pseudo-path: Module.body[1] -> If(#3).body[1] -> Expr(#5).value",
                    "Found variable 'foo': 'bar'",
                ],
            ),
            (
                # Search for setup() should be depth-first, i.e. find the first setup()
                # call even if it is at a deeper level of indentation
                dedent(
                    """\
                    if True:
                        setup({what}=foo)

                    foo = "bar"
                    setup({what}=foo)
                    """
                ),
                None,
                [
                    "Pseudo-path: Module.body[0] -> If(#1).body[0] -> Expr(#2).value",
                    "Variable 'foo' not found along the setup call branch",
                ],
            ),
            (
                # Sanity check: all statements with bodies (except async def / async for)
                dedent(
                    """\
                    foo = "bar"

                    class C:
                        def f():
                            if True:
                                for x in y:
                                    while True:
                                        with x:
                                            try:
                                                pass
                                            except:
                                                setup({what}=foo)
                    """
                ),
                "bar",
                [
                    (
                        "Pseudo-path: Module.body[1] -> ClassDef(#3).body[0] "
                        "-> FunctionDef(#4).body[0] -> If(#5).body[0] -> For(#6).body[0] "
                        "-> While(#7).body[0] -> With(#8).body[0] -> Try(#9).handlers[0] "
                        "-> ExceptHandler(#11).body[0] -> Expr(#12).value"
                    ),
                    "Found variable 'foo': 'bar'",
                ],
            ),
        ],
    )
    @pytest.mark.parametrize("what", ["name", "version"])
    def test_get_kwarg_var(self, script_content, expect_val, expect_logs, what, tmpdir, caplog):
        """Tests for getting kwarg value from a variable."""
        lineno = next(
            i + 1 for i, line in enumerate(script_content.splitlines()) if "setup" in line
        )
        logs = expect_logs + [
            f"Found setup call on line {lineno}",
            "setup kwarg '{what}' looks like a variable",
            f"Backtracking up the AST from line {lineno} to find variable 'foo'",
        ]
        self._test_get_value(tmpdir, caplog, script_content, expect_val, logs, what=what)

    @pytest.mark.parametrize(
        "version_val, expect_version",
        [("1.0.alpha.1", "1.0a1"), (1, "1"), ((1, 0, "alpha", 1), "1.0a1")],
    )
    def test_version_var_special(self, version_val, expect_version, tmpdir, caplog):
        """Test that special version values are supported also for variables."""
        script_content = dedent(
            f"""\
            foo = {version_val!r}

            setup(version=foo)
            """
        )
        expect_logs = [
            "Found setup call on line 3",
            "Pseudo-path: Module.body[1] -> Expr(#3).value",
            "setup kwarg 'version' looks like a variable",
            "Backtracking up the AST from line 3 to find variable 'foo'",
            f"Found variable 'foo': {version_val!r}",
        ]
        self._test_get_value(
            tmpdir, caplog, script_content, expect_version, expect_logs, what="version"
        )

    @pytest.mark.parametrize("what", ["name", "version"])
    def test_kwarg_unsupported_expr(self, what, tmpdir, caplog):
        """Value of kwarg is neither a literal nor a Name."""
        script_content = f"setup({what}=get_version())"
        expect_logs = [
            "Found setup call on line 1",
            "Pseudo-path: Module.body[0] -> Expr(#1).value",
            f"setup kwarg '{what}' is an unsupported expression: Call",
        ]
        self._test_get_value(tmpdir, caplog, script_content, None, expect_logs, what=what)


class TestPipRequirementsFile:
    """PipRequirementsFile tests."""

    PIP_REQUIREMENT_ATTRS = {
        "download_line": None,
        "environment_marker": None,
        "extras": [],
        "hashes": [],
        "kind": None,
        "options": [],
        "package": None,
        "qualifiers": {},
        "raw_package": None,
        "version_specs": [],
    }

    @pytest.mark.parametrize(
        "file_contents, expected_requirements, expected_global_options",
        (
            # Dependency from pypi
            (
                "aiowsgi",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi",
                        "raw_package": "aiowsgi",
                    }
                ],
                [],
            ),
            # Dependency from pypi with pinned version
            (
                "aiowsgi==0.7",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi==0.7",
                        "version_specs": [("==", "0.7")],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with minimum version
            (
                "aiowsgi>=0.7",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi>=0.7",
                        "version_specs": [(">=", "0.7")],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with version range
            (
                "aiowsgi>=0.7,<1.0",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi>=0.7,<1.0",
                        "version_specs": [(">=", "0.7"), ("<", "1.0")],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with picky version
            (
                "aiowsgi>=0.7,<1.0,!=0.8",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi>=0.7,<1.0,!=0.8",
                        "version_specs": [(">=", "0.7"), ("<", "1.0"), ("!=", "0.8")],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with extras
            (
                "aiowsgi[spam,bacon]==0.7",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi[spam,bacon]==0.7",
                        "version_specs": [("==", "0.7")],
                        "extras": ["spam", "bacon"],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with major version compatibility
            (
                "aiowsgi~=0.6",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi~=0.6",
                        "version_specs": [("~=", "0.6")],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with environment markers
            (
                'aiowsgi; python_version < "2.7"',
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": 'aiowsgi; python_version < "2.7"',
                        "environment_marker": 'python_version < "2.7"',
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with hashes
            (
                dedent(
                    """\
                    amqp==2.5.2 \\
                       --hash=sha256:6e649ca13a7df3faacdc8bbb280aa9a6602d22fd9d545 \\
                       --hash=sha256:77f1aef9410698d20eaeac5b73a87817365f457a507d8
                    """
                ),
                [
                    {
                        "package": "amqp",
                        "kind": "pypi",
                        "download_line": "amqp==2.5.2",
                        "version_specs": [("==", "2.5.2")],
                        "hashes": [
                            "sha256:6e649ca13a7df3faacdc8bbb280aa9a6602d22fd9d545",
                            "sha256:77f1aef9410698d20eaeac5b73a87817365f457a507d8",
                        ],
                        "raw_package": "amqp",
                    },
                ],
                [],
            ),
            # Dependency from URL with egg name
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server"
                        ),
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                        ),
                    },
                ],
                [],
            ),
            # Dependency from URL with package name
            (
                "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz",
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                        ),
                        "raw_package": "cnr_server",
                        "url": "https://github.com/quay/appr/archive/58c88e49.tar.gz",
                    },
                ],
                [],
            ),
            # Dependency from URL with both egg and package names
            (
                "ignored @ https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server"
                        ),
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                        ),
                    },
                ],
                [],
            ),
            # Editable dependency from URL
            (
                "-e https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server"
                        ),
                        "options": ["-e"],
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                        ),
                    },
                ],
                [],
            ),
            # Dependency from URL with hashes
            (
                (
                    "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server "
                    "--hash=sh256:sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb32189d91"
                    "2c7f55ec2e6c70c8"
                ),
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server"
                        ),
                        "hashes": [
                            "sh256:sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb32189d912c7f55"
                            "ec2e6c70c8",
                        ],
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                        ),
                    },
                ],
                [],
            ),
            # Dependency from URL with a percent-escaped #cachito_hash
            (
                (
                    "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                    "&cachito_hash=sha256%3A4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb3218"
                    "9d912c7f55ec2e6c70c8"
                ),
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server&cachito_hash=sha256%3A4fd9429bfbb796a48c0bde6bd30"
                            "1ff5b3cc02adb32189d912c7f55ec2e6c70c8"
                        ),
                        "qualifiers": {
                            "egg": "cnr_server",
                            "cachito_hash": (
                                "sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb32189d912c7f55"
                                "ec2e6c70c8"
                            ),
                        },
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                            "&cachito_hash=sha256%3A4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb3218"
                            "9d912c7f55ec2e6c70c8"
                        ),
                    },
                ],
                [],
            ),
            # Dependency from URL with environment markers
            (
                (
                    "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server; "
                    'python_version < "2.7"'
                ),
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server"
                            ' ; python_version < "2.7"'
                        ),
                        "qualifiers": {"egg": "cnr_server"},
                        "environment_marker": 'python_version < "2.7"',
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                        ),
                    },
                ],
                [],
            ),
            # Dependency from URL with multiple qualifiers
            (
                (
                    "https://github.com/quay/appr/archive/58c88e49.tar.gz"
                    "#egg=cnr_server&spam=maps&bacon=nocab"
                ),
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server&spam=maps&bacon=nocab"
                        ),
                        "qualifiers": {"egg": "cnr_server", "spam": "maps", "bacon": "nocab"},
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server&spam=maps&bacon=nocab"
                        ),
                    },
                ],
                [],
            ),
            # Dependency from VCS with egg name
            (
                "git+https://github.com/quay/appr.git@58c88e49#egg=cnr_server",
                [
                    {
                        "package": "cnr-server",
                        "kind": "vcs",
                        "download_line": (
                            "cnr_server @ git+https://github.com/quay/appr.git@58c88e49"
                            "#egg=cnr_server"
                        ),
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": "git+https://github.com/quay/appr.git@58c88e49#egg=cnr_server",
                    },
                ],
                [],
            ),
            # Dependency from VCS with package name
            (
                "cnr_server @ git+https://github.com/quay/appr.git@58c88e49",
                [
                    {
                        "package": "cnr-server",
                        "kind": "vcs",
                        "download_line": (
                            "cnr_server @ git+https://github.com/quay/appr.git@58c88e49"
                        ),
                        "raw_package": "cnr_server",
                        "url": "git+https://github.com/quay/appr.git@58c88e49",
                    },
                ],
                [],
            ),
            # Dependency from VCS with both egg and package names
            (
                "ignored @ git+https://github.com/quay/appr.git@58c88e49#egg=cnr_server",
                [
                    {
                        "package": "cnr-server",
                        "kind": "vcs",
                        "download_line": (
                            "cnr_server @ git+https://github.com/quay/appr.git@58c88e49"
                            "#egg=cnr_server"
                        ),
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": "git+https://github.com/quay/appr.git@58c88e49#egg=cnr_server",
                    },
                ],
                [],
            ),
            # Editable dependency from VCS
            (
                "-e git+https://github.com/quay/appr.git@58c88e49#egg=cnr_server",
                [
                    {
                        "package": "cnr-server",
                        "kind": "vcs",
                        "download_line": (
                            "cnr_server @ git+https://github.com/quay/appr.git@58c88e49"
                            "#egg=cnr_server"
                        ),
                        "options": ["-e"],
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": "git+https://github.com/quay/appr.git@58c88e49#egg=cnr_server",
                    },
                ],
                [],
            ),
            # Dependency from VCS with multiple qualifiers
            (
                (
                    "git+https://github.com/quay/appr.git@58c88e49"
                    "#egg=cnr_server&spam=maps&bacon=nocab"
                ),
                [
                    {
                        "package": "cnr-server",
                        "kind": "vcs",
                        "download_line": (
                            "cnr_server @ git+https://github.com/quay/appr.git@58c88e49"
                            "#egg=cnr_server&spam=maps&bacon=nocab"
                        ),
                        "qualifiers": {"egg": "cnr_server", "spam": "maps", "bacon": "nocab"},
                        "raw_package": "cnr_server",
                        "url": (
                            "git+https://github.com/quay/appr.git@58c88e49"
                            "#egg=cnr_server&spam=maps&bacon=nocab"
                        ),
                    },
                ],
                [],
            ),
            # No dependencies
            ("", [], []),
            # Comments are ignored
            (
                dedent(
                    """\
                    aiowsgi==0.7 # inline comment
                    # Line comment
                    asn1crypto==1.3.0 # inline comment \
                    with line continuation
                    # Line comment \
                    with line continuation
                        # Line comment with multiple leading white spaces
                    """
                ),
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi==0.7",
                        "version_specs": [("==", "0.7")],
                        "raw_package": "aiowsgi",
                    },
                    {
                        "package": "asn1crypto",
                        "kind": "pypi",
                        "download_line": "asn1crypto==1.3.0",
                        "version_specs": [("==", "1.3.0")],
                        "raw_package": "asn1crypto",
                    },
                ],
                [],
            ),
            # Empty lines are ignored
            (
                dedent(
                    """\
                    aiowsgi==0.7
                            \

                    asn1crypto==1.3.0

                    """
                ),
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi==0.7",
                        "version_specs": [("==", "0.7")],
                        "raw_package": "aiowsgi",
                    },
                    {
                        "package": "asn1crypto",
                        "kind": "pypi",
                        "download_line": "asn1crypto==1.3.0",
                        "version_specs": [("==", "1.3.0")],
                        "raw_package": "asn1crypto",
                    },
                ],
                [],
            ),
            # Line continuation is honored
            (
                dedent(
                    """\
                    aiowsgi\\
                    \\
                    ==\\
                    \\
                    \\
                    \\
                    0.7\\
                    """
                ),
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi==0.7",
                        "version_specs": [("==", "0.7")],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Global options
            ("--only-binary :all:", [], ["--only-binary", ":all:"],),
            # Global options with a requirement
            (
                "aiowsgi==0.7 --only-binary :all:",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi==0.7",
                        "version_specs": [("==", "0.7")],
                        "raw_package": "aiowsgi",
                    },
                ],
                ["--only-binary", ":all:"],
            ),
        ),
    )
    def test_parsing_of_valid_cases(
        self, file_contents, expected_requirements, expected_global_options, tmpdir
    ):
        """Test the various valid use cases of requirements in a requirements file."""
        requirements_file = tmpdir.join("requirements.txt")
        requirements_file.write(file_contents)

        pip_requirements = pip.PipRequirementsFile(requirements_file.strpath)

        assert pip_requirements.options == expected_global_options
        assert len(pip_requirements.requirements) == len(expected_requirements)
        for pip_requirement, expected_requirement in zip(
            pip_requirements.requirements, expected_requirements
        ):
            self._assert_pip_requirement(pip_requirement, expected_requirement)

    @pytest.mark.parametrize(
        "file_contents, expected_error",
        (
            ("--spam", "Unknown requirements file option '--spam'"),
            (
                "--prefer-binary=spam",
                "Unexpected value for requirements file option '--prefer-binary=spam'",
            ),
            ("--only-binary", "Requirements file option '--only-binary' requires a value"),
            ("aiowsgi --hash", "Requirements file option '--hash' requires a value"),
            (
                "-e",
                re.escape(
                    "Requirements file option(s) ['-e'] can only be applied to a requirement"
                ),
            ),
            (
                "pip @ file:///localbuilds/pip-1.3.1.zip",
                "Direct references with 'file' scheme are not supported",
            ),
            (
                "file:///localbuilds/pip-1.3.1.zip",
                "Direct references with 'file' scheme are not supported",
            ),
            (
                "file:///localbuilds/pip-1.3.1.zip",
                "Direct references with 'file' scheme are not supported",
            ),
            (
                "aiowsgi==0.7 asn1crypto==1.3.0",
                "Unable to parse the requirement 'aiowsgi==0.7 asn1crypto==1.3.0'",
            ),
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz",
                "Egg name could not be determined from the requirement",
            ),
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=",
                "Egg name could not be determined from the requirement",
            ),
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg",
                "Egg name could not be determined from the requirement",
            ),
            (
                "cnr_server@foo@https://github.com/quay/appr/archive/58c88e49.tar.gz",
                "Unable to extract scheme from direct access requirement",
            ),
        ),
    )
    def test_parsing_of_invalid_cases(self, file_contents, expected_error, tmpdir):
        """Test the invalid use cases of requirements in a requirements file."""
        requirements_file = tmpdir.join("requirements.txt")
        requirements_file.write(file_contents)

        pip_requirements = pip.PipRequirementsFile(requirements_file.strpath)
        with pytest.raises(ValidationError, match=expected_error):
            pip_requirements.requirements

    def test_corner_cases_when_parsing_single_line(self):
        """Test scenarios in PipRequirement that cannot be triggered via PipRequirementsFile."""
        # Empty lines are ignored
        assert pip.PipRequirement.from_line("     ", []) is None

        with pytest.raises(
            ValidationError, match="Multiple requirements per line are not supported"
        ):
            pip.PipRequirement.from_line("aiowsgi==0.7 \nasn1crypto==1.3.0", [])

    def test_replace_requirements(self, tmpdir):
        """Test generating a new requirements file with replacements."""
        original_file_path = tmpdir.join("original-requirements.txt")
        new_file_path = tmpdir.join("new-requirements.txt")

        original_file_path.write(
            dedent(
                """\
                https://github.com/quay/appr/archive/58c88.tar.gz#egg=cnr_server --hash=sha256:123
                -e spam @ git+https://github.com/monty/spam.git@123456
                aiowsgi==0.7
                asn1crypto==1.3.0
                """
            )
        )

        # Mapping of the new URL value to be used in modified requirements
        new_urls = {
            "cnr_server": "https://cachito/nexus/58c88.tar.gz",
            "spam": "https://cachito/nexus/spam-123456.tar.gz",
            "asn1crypto": "https://cachito/nexus/asn1crypto-1.3.0.tar.gz",
        }

        # Mapping of the new hash values to be used in modified requirements
        new_hashes = {
            "spam": ["sha256:45678"],
            "aiowsgi": ["sha256:90123"],
            "asn1crypto": ["sha256:01234"],
        }

        expected_new_file = dedent(
            f"""\
            cnr_server @ https://cachito/nexus/58c88.tar.gz#egg=cnr_server --hash=sha256:123
            spam @ https://cachito/nexus/spam-123456.tar.gz --hash=sha256:45678
            aiowsgi==0.7 --hash=sha256:90123
            asn1crypto @ https://cachito/nexus/asn1crypto-1.3.0.tar.gz --hash=sha256:01234
            """
        )

        expected_attr_changes = {
            "cnr_server": {
                "download_line": "cnr_server @ https://cachito/nexus/58c88.tar.gz#egg=cnr_server",
                "url": "https://cachito/nexus/58c88.tar.gz#egg=cnr_server",
            },
            "spam": {
                "hashes": ["sha256:45678"],
                "options": [],
                "kind": "url",
                "download_line": "spam @ https://cachito/nexus/spam-123456.tar.gz",
                "url": "https://cachito/nexus/spam-123456.tar.gz",
            },
            "aiowsgi": {"hashes": ["sha256:90123"]},
            "asn1crypto": {
                "download_line": "asn1crypto @ https://cachito/nexus/asn1crypto-1.3.0.tar.gz",
                "hashes": ["sha256:01234"],
                "kind": "url",
                "version_specs": [],
                "url": "https://cachito/nexus/asn1crypto-1.3.0.tar.gz",
            },
        }

        pip_requirements = pip.PipRequirementsFile(original_file_path.strpath)

        new_requirements = []
        for pip_requirement in pip_requirements.requirements:
            url = new_urls.get(pip_requirement.raw_package)
            hashes = new_hashes.get(pip_requirement.raw_package)
            new_requirements.append(pip_requirement.copy(url=url, hashes=hashes))

        # Verify a new PipRequirementsFile can be loaded in memory and written correctly to disk.
        pip.PipRequirementsFile.from_requirements_and_options(
            new_requirements, pip_requirements.options
        ).write(new_file_path.strpath)
        assert open(new_file_path.strpath).read() == expected_new_file

        # Parse the newly generated requirements file to ensure it's parsed correctly.
        new_pip_requirements = pip.PipRequirementsFile(new_file_path.strpath)

        assert new_pip_requirements.options == pip_requirements.options
        for new_pip_requirement, pip_requirement in zip(
            new_pip_requirements.requirements, pip_requirements.requirements
        ):
            for attr in self.PIP_REQUIREMENT_ATTRS:
                expected_value = expected_attr_changes.get(pip_requirement.raw_package, {}).get(
                    attr, getattr(pip_requirement, attr)
                )
                assert (
                    getattr(new_pip_requirement, attr) == expected_value
                ), f"unexpected {attr!r} value for package {pip_requirement.raw_package!r}"

    def test_write_requirements_file(self, tmpdir):
        """Test PipRequirementsFile.write method."""
        original_file_path = tmpdir.join("original-requirements.txt")
        new_file_path = tmpdir.join("test-requirements.txt")

        content = dedent(
            """\
            --only-binary :all:
            aiowsgi==0.7
            asn1crypto==1.3.0
            """
        )

        original_file_path.write(content)
        assert original_file_path.exists()
        pip_requirements = pip.PipRequirementsFile(original_file_path.strpath)
        assert pip_requirements.requirements
        assert pip_requirements.options

        # Verify file can be written to self.file_path
        original_file_path.remove()
        assert not original_file_path.exists()
        pip_requirements.write()
        assert original_file_path.exists()
        assert open(original_file_path.strpath).read() == content

        # Verify file can be written to an alternative location
        original_file_path.remove()
        assert not original_file_path.exists()
        pip_requirements.write(new_file_path.strpath)
        assert not original_file_path.exists()
        assert new_file_path.exists()
        assert open(new_file_path.strpath).read() == content

    def test_write_requirements_file_unspecified_path(self):
        """Test PipRequirementsFile.write method validation error."""
        with pytest.raises(RuntimeError, match="Unspecified 'file_path' for the requirements file"):
            pip.PipRequirementsFile(None).write()

    @pytest.mark.parametrize(
        "requirement_line, requirement_options, expected_str_line",
        (
            ("aiowsgi==1.2.3", [], "aiowsgi==1.2.3"),
            ("aiowsgi>=0.7", [], "aiowsgi>=0.7"),
            ('aiowsgi; python_version < "2.7"', [], 'aiowsgi; python_version < "2.7"'),
            (
                "amqp==2.5.2",
                [
                    "--hash",
                    "sha256:6e649ca13a7df3faacdc8bbb280aa9a6602d22fd9d545",
                    "--hash",
                    "sha256:77f1aef9410698d20eaeac5b73a87817365f457a507d8",
                ],
                (
                    "amqp==2.5.2 --hash=sha256:6e649ca13a7df3faacdc8bbb280aa9a6602d22fd9d545 "
                    "--hash=sha256:77f1aef9410698d20eaeac5b73a87817365f457a507d8"
                ),
            ),
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                [],
                "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
            ),
            (
                "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz",
                [],
                "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz",
            ),
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                ["-e"],
                (
                    "-e cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                    "#egg=cnr_server"
                ),
            ),
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                ["--hash", "sh256:sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb32189d912c7f"],
                (
                    "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz#"
                    "egg=cnr_server --hash=sh256:sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02ad"
                    "b32189d912c7f"
                ),
            ),
            (
                "git+https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                [],
                (
                    "cnr_server @ git+https://github.com/quay/appr/archive/58c88e49.tar.gz#"
                    "egg=cnr_server"
                ),
            ),
            (
                "cnr_server @ git+https://github.com/quay/appr/archive/58c88e49.tar.gz",
                [],
                "cnr_server @ git+https://github.com/quay/appr/archive/58c88e49.tar.gz",
            ),
            (
                "git+https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                ["-e"],
                (
                    "-e cnr_server @ git+https://github.com/quay/appr/archive/58c88e49.tar.gz"
                    "#egg=cnr_server"
                ),
            ),
            (
                "git+https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                ["--hash", "sh256:sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb32189d912c7f"],
                (
                    "cnr_server @ git+https://github.com/quay/appr/archive/58c88e49.tar.gz#"
                    "egg=cnr_server --hash=sh256:sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02ad"
                    "b32189d912c7f"
                ),
            ),
        ),
    )
    def test_pip_requirement_to_str(self, requirement_line, requirement_options, expected_str_line):
        """Test PipRequirement.__str__ method."""
        assert (
            str(pip.PipRequirement.from_line(requirement_line, requirement_options))
            == expected_str_line
        )

    @pytest.mark.parametrize(
        "requirement_line, requirement_options, new_values, expected_changes",
        (
            # Existing hashes are retained
            ("spam", ["--hash", "sha256:123"], {}, {}),
            # Existing hashes are replaced
            (
                "spam",
                ["--hash", "sha256:123"],
                {"hashes": ["sha256:234"]},
                {"hashes": ["sha256:234"]},
            ),
            # Hashes are added
            ("spam", [], {"hashes": ["sha256:234"]}, {"hashes": ["sha256:234"]}),
            # pypi is modified to url
            (
                "spam",
                [],
                {"url": "https://cachito.example.com/nexus/spam-1.2.3.tar.gz"},
                {
                    "download_line": "spam @ https://cachito.example.com/nexus/spam-1.2.3.tar.gz",
                    "kind": "url",
                    "url": "https://cachito.example.com/nexus/spam-1.2.3.tar.gz",
                },
            ),
            # url is modified to another url
            (
                "https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam",
                [],
                {"url": "https://cachito.example.com/nexus/spam-58c88.tar.gz"},
                {
                    "download_line": (
                        "spam @ https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam"
                    ),
                    "kind": "url",
                    "url": "https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam",
                },
            ),
            # vcs is modified to URL
            (
                "git+https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam",
                [],
                {"url": "https://cachito.example.com/nexus/spam-58c88.tar.gz"},
                {
                    "download_line": (
                        "spam @ https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam"
                    ),
                    "kind": "url",
                    "url": "https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam",
                },
            ),
            # Editable option, "-e", is dropped when setting url
            (
                "git+https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam",
                ["-e"],
                {"url": "https://cachito.example.com/nexus/spam-58c88.tar.gz"},
                {
                    "download_line": (
                        "spam @ https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam"
                    ),
                    "kind": "url",
                    "options": [],
                    "url": "https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam",
                },
            ),
            # Editable option, "--e", is not dropped when url is not set
            ("git+https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam", ["-e"], {}, {},),
            # Editable option, "--editable", is dropped when setting url
            (
                "git+https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam",
                ["--editable"],
                {"url": "https://cachito.example.com/nexus/spam-58c88.tar.gz"},
                {
                    "download_line": (
                        "spam @ https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam"
                    ),
                    "kind": "url",
                    "options": [],
                    "url": "https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam",
                },
            ),
            # Editable option, "--editable", is not dropped when url is not set
            (
                "git+https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam",
                ["--editable"],
                {},
                {},
            ),
            # Environment markers persist
            (
                (
                    "git+https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam"
                    '; python_version < "2.7"'
                ),
                [],
                {"url": "https://cachito.example.com/nexus/spam-58c88.tar.gz"},
                {
                    "download_line": (
                        "spam @ https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam "
                        '; python_version < "2.7"'
                    ),
                    "kind": "url",
                    "url": "https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam",
                },
            ),
            # Extras are cleared when setting a new URL
            (
                "spam[SALTY]",
                [],
                {"url": "https://cachito.example.com/nexus/spam-1.2.3.tar.gz"},
                {
                    "download_line": "spam @ https://cachito.example.com/nexus/spam-1.2.3.tar.gz",
                    "kind": "url",
                    "extras": [],
                    "url": "https://cachito.example.com/nexus/spam-1.2.3.tar.gz",
                },
            ),
            # Extras are NOT cleared when a new URL is not set
            ("spam[SALTY]", [], {}, {},),
            # Version specs are cleared when setting a new URL
            (
                "spam==1.2.3",
                [],
                {"url": "https://cachito.example.com/nexus/spam-1.2.3.tar.gz"},
                {
                    "download_line": "spam @ https://cachito.example.com/nexus/spam-1.2.3.tar.gz",
                    "kind": "url",
                    "version_specs": [],
                    "url": "https://cachito.example.com/nexus/spam-1.2.3.tar.gz",
                },
            ),
            # Version specs are NOT cleared when a new URL is not set
            ("spam==1.2.3", [], {}, {},),
            # Qualifiers persists
            (
                "https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam&spam=maps",
                [],
                {"url": "https://cachito.example.com/nexus/spam-58c88.tar.gz"},
                {
                    "download_line": (
                        "spam @ https://cachito.example.com/nexus/spam-58c88.tar.gz#"
                        "egg=spam&spam=maps"
                    ),
                    "url": "https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam&spam=maps",
                },
            ),
        ),
    )
    def test_pip_requirement_copy(
        self, requirement_line, requirement_options, new_values, expected_changes,
    ):
        """Test PipRequirement.copy method."""
        original_requirement = pip.PipRequirement.from_line(requirement_line, requirement_options)
        new_requirement = original_requirement.copy(**new_values)

        for attr in self.PIP_REQUIREMENT_ATTRS:
            expected_changes.setdefault(attr, getattr(original_requirement, attr))

        self._assert_pip_requirement(new_requirement, expected_changes)

    def test_invalid_kind_for_url(self):
        """Test extracting URL from a requirement that does not have one."""
        requirement = pip.PipRequirement()
        requirement.download_line = "aiowsgi==0.7"
        requirement.kind = "pypi"

        with pytest.raises(ValueError, match="Cannot extract URL from pypi requirement"):
            _ = requirement.url

    def _assert_pip_requirement(self, pip_requirement, expected_requirement):
        for attr, default_value in self.PIP_REQUIREMENT_ATTRS.items():
            expected_requirement.setdefault(attr, default_value)

        for attr, expected_value in expected_requirement.items():
            if attr in ("version_specs", "extras"):
                # Account for differences in order
                assert set(getattr(pip_requirement, attr)) == set(
                    expected_value
                ), f"unexpected value for {attr!r}"
            else:
                assert (
                    getattr(pip_requirement, attr) == expected_value
                ), f"unexpected value for {attr!r}"


class TestNexus:
    """Nexus related tests."""

    @mock.patch("cachito.workers.pkg_managers.pip.nexus.execute_script")
    def test_prepare_nexus_for_pip_request(self, mock_exec_script):
        """Check whether groovy srcript is called with proper args."""
        pip.prepare_nexus_for_pip_request("cachito-pip-hosted-1", "cachito-pip-raw-1")

        mock_exec_script.assert_called_once_with(
            "pip_before_content_staged",
            {
                "pip_repository_name": "cachito-pip-hosted-1",
                "raw_repository_name": "cachito-pip-raw-1",
            },
        )

    @mock.patch("cachito.workers.pkg_managers.pip.nexus.execute_script")
    def test_prepare_nexus_for_pip_request_failed(self, mock_exec_script):
        """Check whether proper error is raised on groovy srcript failures."""
        mock_exec_script.side_effect = NexusScriptError()

        expected = "Failed to prepare Nexus for Cachito to stage Python content"
        with pytest.raises(CachitoError, match=expected):
            pip.prepare_nexus_for_pip_request(1, 1)

    @mock.patch("secrets.token_hex")
    @mock.patch("cachito.workers.pkg_managers.pip.nexus.execute_script")
    def test_finalize_nexus_for_pip_request(self, mock_exec_script, mock_secret):
        """Check whether groovy srcript is called with proper args."""
        mock_secret.return_value = "password"
        password = pip.finalize_nexus_for_pip_request(
            "cachito-pip-hosted-1", "cachito-pip-raw-1", "user-1"
        )

        mock_exec_script.assert_called_once_with(
            "pip_after_content_staged",
            {
                "pip_repository_name": "cachito-pip-hosted-1",
                "raw_repository_name": "cachito-pip-raw-1",
                "username": "user-1",
                "password": "password",
            },
        )

        assert password == "password"

    @mock.patch("cachito.workers.pkg_managers.pip.nexus.execute_script")
    def test_finalize_nexus_for_pip_request_failed(self, mock_exec_script):
        """Check whether proper error is raised on groovy srcript failures."""
        mock_exec_script.side_effect = NexusScriptError()
        expected = "Failed to configure Nexus Python repositories for final consumption"
        with pytest.raises(CachitoError, match=expected):
            pip.finalize_nexus_for_pip_request(1, 1, 1)


class MockBundleDir(type(Path())):
    """Mocked RequestBundleDir."""

    def __new__(cls, *args, **kwargs):
        """Make a new MockBundleDir."""
        self = super().__new__(cls, *args, **kwargs)
        self.pip_deps_dir = self / "deps" / "pip"
        return self


class TestDownload:
    """Tests for dependency downloading."""

    def mock_pypi_response(self, sdist_exists, sdist_not_yanked):
        """Mock a PyPI HTML response from the /simple/<project> endpoint."""
        egg_filename = "aiowsgi-0.7.egg"
        tar_filename = "aiowsgi-0.7.tar.gz"

        egg = f'<a href="../../package/{egg_filename}">{egg_filename}</a>'
        if sdist_not_yanked:
            sdist = f'<a href="../../packages/{tar_filename}">{tar_filename}</a>'
        else:
            sdist = f'<a href="../../packages/{tar_filename}" data-yanked="">{tar_filename}</a>'

        html = dedent(
            f"""
            <html>
              <body>
                {egg}
                {sdist if sdist_exists else ""}
              </body>
            </html>
            """
        )

        return html

    def mock_requirements_file(self, requirements=None, options=None):
        """Mock a requirements.txt file."""
        return mock.Mock(requirements=requirements or [], options=options or [])

    def mock_requirement(
        self,
        package,
        kind,
        version_specs=None,
        download_line=None,
        hashes=None,
        qualifiers=None,
        url=None,
    ):
        """Mock a requirements.txt item. By default should pass validation."""
        if url is None and kind == "vcs":
            url = f"git+https://github.com/example@{GIT_REF}"
        elif url is None and kind == "url":
            url = "https://example.org/file.tar.gz"

        if hashes is None and qualifiers is None and kind == "url":
            qualifiers = {"cachito_hash": "sha256:abcdef"}

        return mock.Mock(
            package=package,
            kind=kind,
            version_specs=version_specs if version_specs is not None else [("==", "1")],
            download_line=download_line or package,
            hashes=hashes or [],
            qualifiers=qualifiers or {},
            url=url,
        )

    @pytest.mark.parametrize(
        "pypi_query_success, sdist_exists, sdist_not_yanked",
        [
            (True, True, True),
            (True, True, True),
            (True, True, False),
            (True, False, False),
            (False, False, False),
        ],
    )
    # Package name should be normalized before querying PyPI
    @pytest.mark.parametrize("package_name", ["AioWSGI", "aiowsgi"])
    @mock.patch.object(requests_session, "get")
    @mock.patch("cachito.workers.pkg_managers.pip.download_binary_file")
    def test_download_pypi_package(
        self,
        mock_download_file,
        mock_get,
        pypi_query_success,
        sdist_exists,
        sdist_not_yanked,
        package_name,
        tmp_path,
    ):
        """Test downloading of a single PyPI package."""
        mock_requirement = self.mock_requirement(
            package_name, "pypi", version_specs=[("==", "0.7")]
        )

        pypi_resp = self.mock_pypi_response(sdist_exists, sdist_not_yanked)
        pypi_success = mock.Mock(text=pypi_resp)
        pypi_fail = requests.RequestException("Something went wrong")

        mock_get.side_effect = [
            pypi_success if pypi_query_success else pypi_fail,
        ]

        if not pypi_query_success:
            expect_error = "PyPI query failed: Something went wrong"
        elif not sdist_exists:
            # The error message should show the package name unchanged, not normalized
            expect_error = f"No sdists found for package {package_name}==0.7"
        elif not sdist_not_yanked:
            expect_error = f"All sdists for package {package_name}==0.7 are yanked"
        else:
            expect_error = None

        if expect_error is None:
            download_info = pip._download_pypi_package(
                mock_requirement, tmp_path, "https://pypi-proxy.org/", ("user", "password")
            )
            assert download_info == {
                "package": "aiowsgi",
                "version": "0.7",
                "path": tmp_path / "aiowsgi" / "aiowsgi-0.7.tar.gz",
            }

            proxied_file_url = (
                "https://pypi-proxy.org/simple/aiowsgi/../../packages/aiowsgi-0.7.tar.gz"
            )
            mock_download_file.assert_called_once_with(
                proxied_file_url, download_info["path"], auth=("user", "password")
            )
        else:
            with pytest.raises(CachitoError) as exc_info:
                pip._download_pypi_package(
                    mock_requirement, tmp_path, "https://pypi-proxy.org", ("user", "password")
                )
            assert str(exc_info.value) == expect_error

        mock_get.assert_called_once_with(
            "https://pypi-proxy.org/simple/aiowsgi/", auth=("user", "password")
        )

    def test_process_package_links(self):
        """Test processing of package links."""
        links = [
            ElementTree.fromstring('<a href="../foo-1.0.tar.gz">foo-1.0.tar.gz</a>'),
            ElementTree.fromstring('<a href="../foo-1.0.zip" data-yanked="">foo-1.0.zip</a>'),
        ]
        assert pip._process_package_links(links, "foo", "1.0") == [
            {
                "name": "foo",
                "version": "1.0",
                "url": "../foo-1.0.tar.gz",
                "filename": "foo-1.0.tar.gz",
                "yanked": False,
            },
            {
                "name": "foo",
                "version": "1.0",
                "url": "../foo-1.0.zip",
                "filename": "foo-1.0.zip",
                "yanked": True,
            },
        ]

    @pytest.mark.parametrize(
        "noncanonical_name, canonical_name",
        [
            ("Django", "django"),
            ("ruamel.yaml.clib", "ruamel-yaml-clib"),
            ("requests_kerberos", "requests-kerberos"),
            ("Requests_._-_Kerberos", "requests-kerberos"),
        ],
    )
    @pytest.mark.parametrize("requested_name_is_canonical", [True, False])
    @pytest.mark.parametrize("actual_name_is_canonical", [True, False])
    def test_process_package_links_noncanonical_name(
        self,
        canonical_name,
        noncanonical_name,
        requested_name_is_canonical,
        actual_name_is_canonical,
    ):
        """Test that canonical names match non-canonical names."""
        if requested_name_is_canonical:
            requested_name = canonical_name
        else:
            requested_name = noncanonical_name

        if actual_name_is_canonical:
            actual_name = canonical_name
        else:
            actual_name = noncanonical_name

        links = [
            ElementTree.fromstring(
                f'<a href="../{actual_name}-1.0.tar.gz">{actual_name}-1.0.tar.gz</a>'
            ),
        ]

        assert pip._process_package_links(links, requested_name, "1.0") == [
            {
                "name": actual_name,
                "version": "1.0",
                "url": f"../{actual_name}-1.0.tar.gz",
                "filename": f"{actual_name}-1.0.tar.gz",
                "yanked": False,
            }
        ]

    @pytest.mark.parametrize(
        "noncanonical_version, canonical_version",
        [
            ("1.0", "1"),
            ("1.0.0", "1"),
            ("1.0.alpha1", "1a1"),
            ("1.1.0", "1.1"),
            ("1.1.alpha1", "1.1a1"),
            ("1.0-1", "1.post1"),
            ("1.1.0-1", "1.1.post1"),
        ],
    )
    @pytest.mark.parametrize("requested_version_is_canonical", [True, False])
    @pytest.mark.parametrize("actual_version_is_canonical", [True, False])
    def test_process_package_links_noncanonical_version(
        self,
        canonical_version,
        noncanonical_version,
        requested_version_is_canonical,
        actual_version_is_canonical,
    ):
        """Test that canonical names match non-canonical names."""
        if requested_version_is_canonical:
            requested_version = canonical_version
        else:
            requested_version = noncanonical_version

        if actual_version_is_canonical:
            actual_version = canonical_version
        else:
            actual_version = noncanonical_version

        links = [
            ElementTree.fromstring(
                f'<a href="../foo-{actual_version}.tar.gz">foo-{actual_version}.tar.gz</a>'
            ),
        ]

        assert pip._process_package_links(links, "foo", requested_version) == [
            {
                "name": "foo",
                "version": actual_version,
                "url": f"../foo-{actual_version}.tar.gz",
                "filename": f"foo-{actual_version}.tar.gz",
                "yanked": False,
            }
        ]

    def test_process_package_links_not_sdist(self):
        """Test that links for files that are not sdists are ignored."""
        links = [
            ElementTree.fromstring('<a href="../foo-1.0.whl">foo-1.0.whl</a>'),
            ElementTree.fromstring('<a href="../foo-1.0.egg">foo-1.0.egg</a>'),
        ]
        assert pip._process_package_links(links, "foo", "1.0") == []

    @pytest.mark.parametrize("requested_version", ["2.0", "1.0.a1", "1.0.post1", "1.0.dev1"])
    def test_process_package_links_wrong_version(self, requested_version):
        """Test that links for files with different version are ignored."""
        links = [
            ElementTree.fromstring('<a href="../foo-1.0.tar.gz">foo-1.0.tar.gz</a>'),
        ]
        assert pip._process_package_links(links, "foo", requested_version) == []

    def test_sdist_sorting(self):
        """Test that sdist preference key can be used for sorting in the expected order."""
        # Original order is descending by preference
        sdists = [
            {"id": "unyanked-tar.gz", "yanked": False, "filename": "foo.tar.gz"},
            {"id": "unyanked-zip", "yanked": False, "filename": "foo.zip"},
            {"id": "unyanked-tar.bz2", "yanked": False, "filename": "foo.tar.bz2"},
            {"id": "yanked-tar.gz", "yanked": True, "filename": "foo.tar.gz"},
            {"id": "yanked-zip", "yanked": True, "filename": "foo.zip"},
            {"id": "yanked-tar.bz2", "yanked": True, "filename": "foo.tar.bz2"},
        ]
        # Expected order is ascending by preference
        expect_order = [
            "yanked-tar.bz2",
            "yanked-zip",
            "yanked-tar.gz",
            "unyanked-tar.bz2",
            "unyanked-zip",
            "unyanked-tar.gz",
        ]
        sdists.sort(key=pip._sdist_preference)
        assert [s["id"] for s in sdists] == expect_order

    @pytest.mark.parametrize("have_raw_component", [True, False])
    @mock.patch("cachito.workers.pkg_managers.pip.nexus.get_raw_component_asset_url")
    @mock.patch("cachito.workers.pkg_managers.pip.download_binary_file")
    @mock.patch("cachito.workers.pkg_managers.pip.Git")
    @mock.patch("shutil.copy")
    def test_download_vcs_package(
        self,
        mock_shutil_copy,
        mock_git,
        mock_download_file,
        mock_get_component_url,
        have_raw_component,
        tmp_path,
        caplog,
    ):
        """Test downloading of a single VCS package."""
        vcs_url = f"git+https://github.com/spam/eggs@{GIT_REF}"
        raw_url = f"https://nexus:8081/repository/cachito-pip-raw/eggs.tar.gz"

        mock_requirement = self.mock_requirement(
            "eggs", "vcs", url=vcs_url, download_line=f"eggs @ {vcs_url}"
        )

        git_archive_path = tmp_path / "eggs.tar.gz"

        mock_get_component_url.return_value = raw_url if have_raw_component else None
        mock_git.return_value = mock.Mock()
        mock_git.return_value.sources_dir.archive_path = git_archive_path

        download_info = pip._download_vcs_package(
            mock_requirement, tmp_path, "cachito-pip-raw", ("username", "password")
        )

        raw_component = f"eggs/eggs-external-gitcommit-{GIT_REF}.tar.gz"

        assert download_info == {
            "package": "eggs",
            "path": tmp_path.joinpath(
                "github.com", "spam", "eggs", f"eggs-external-gitcommit-{GIT_REF}.tar.gz"
            ),
            "url": "https://github.com/spam/eggs",
            "ref": GIT_REF,
            "namespace": "spam",
            "repo": "eggs",
            "host": "github.com",
            "raw_component_name": raw_component,
            "have_raw_component": have_raw_component,
        }

        download_path = download_info["path"]

        assert f"Looking for raw component '{raw_component}' in 'cachito-pip-raw'" in caplog.text

        if have_raw_component:
            assert f"Found raw component, will download from '{raw_url}'" in caplog.text
            mock_download_file.assert_called_once_with(
                raw_url, download_path, auth=("username", "password")
            )
            mock_git.assert_not_called()
            mock_shutil_copy.assert_not_called()
        else:
            assert "Raw component not found, will fetch from git" in caplog.text
            mock_download_file.assert_not_called()
            mock_git.assert_called_once_with(f"https://github.com/spam/eggs", GIT_REF)
            mock_git.return_value.fetch_source.assert_called_once_with(gitsubmodule=False)
            mock_shutil_copy.assert_called_once_with(git_archive_path, download_path)

    @pytest.mark.parametrize(
        "url, nonstandard_info",  # See body of function for what is standard info
        [
            (
                # Standard case
                f"git+https://github.com/monty/python@{GIT_REF}",
                None,
            ),
            (
                # Ref should be converted to lowercase
                f"git+https://github.com/monty/python@{GIT_REF.upper()}",
                {"ref": GIT_REF},  # Standard but be explicit about it
            ),
            (
                # Repo ends with .git (that is okay)
                f"git+https://github.com/monty/python.git@{GIT_REF}",
                {"url": "https://github.com/monty/python.git"},
            ),
            (
                # git://
                f"git://github.com/monty/python@{GIT_REF}",
                {"url": "git://github.com/monty/python"},
            ),
            (
                # git+git://
                f"git+git://github.com/monty/python@{GIT_REF}",
                {"url": "git://github.com/monty/python"},
            ),
            (
                # No namespace
                f"git+https://github.com/python@{GIT_REF}",
                {"url": "https://github.com/python", "namespace": ""},
            ),
            (
                # Namespace with more parts
                f"git+https://github.com/monty/python/and/the/holy/grail@{GIT_REF}",
                {
                    "url": "https://github.com/monty/python/and/the/holy/grail",
                    "namespace": "monty/python/and/the/holy",
                    "repo": "grail",
                },
            ),
            (
                # Port should be part of host
                f"git+https://github.com:443/monty/python@{GIT_REF}",
                {"url": "https://github.com:443/monty/python", "host": "github.com:443"},
            ),
            (
                # Authentication should not be part of host
                f"git+https://user:password@github.com/monty/python@{GIT_REF}",
                {
                    "url": "https://user:password@github.com/monty/python",
                    "host": "github.com",  # Standard but be explicit about it
                },
            ),
            (
                # Params, query and fragment should be stripped
                f"git+https://github.com/monty/python@{GIT_REF};foo=bar?bar=baz#egg=spam",
                {
                    # Standard but be explicit about it
                    "url": "https://github.com/monty/python",
                },
            ),
        ],
    )
    def test_extract_git_info(self, url, nonstandard_info):
        """Test extraction of git info from VCS URL."""
        info = {
            "url": "https://github.com/monty/python",
            "ref": GIT_REF,
            "namespace": "monty",
            "repo": "python",
            "host": "github.com",
        }
        info.update(nonstandard_info or {})
        assert pip._extract_git_info(url) == info

    @pytest.mark.parametrize("have_raw_component", [True, False])
    @pytest.mark.parametrize("hash_as_qualifier", [True, False])
    @pytest.mark.parametrize(
        "host_in_url, trusted_hosts, host_is_trusted",
        [
            ("example.org", [], False),
            ("example.org", ["example.org"], True),
            ("example.org:443", ["example.org:443"], True),
            # 'host' in URL does not match 'host:port' in trusted hosts
            ("example.org", ["example.org:443"], False),
            # 'host:port' in URL *does* match 'host' in trusted hosts
            ("example.org:443", ["example.org"], True),
        ],
    )
    @mock.patch("cachito.workers.pkg_managers.pip.nexus.get_raw_component_asset_url")
    @mock.patch("cachito.workers.pkg_managers.pip.download_binary_file")
    def test_download_url_package(
        self,
        mock_download_file,
        mock_get_component_url,
        have_raw_component,
        hash_as_qualifier,
        host_in_url,
        trusted_hosts,
        host_is_trusted,
        tmp_path,
        caplog,
    ):
        """Test downloading of a single URL package."""
        # Add the #cachito_package fragment to make sure the .tar.gz extension
        # will be found even if the URL does not end with it
        original_url = f"https://{host_in_url}/foo.tar.gz#cachito_package=foo"
        url_with_hash = f"{original_url}&cachito_hash=sha256:abcdef"
        if hash_as_qualifier:
            original_url = url_with_hash

        raw_url = f"https://nexus:8081/repository/cachito-pip-raw/foo.tar.gz"

        mock_requirement = self.mock_requirement(
            "foo",
            "url",
            url=original_url,
            download_line=f"foo @ {original_url}",
            hashes=["sha256:abcdef"] if not hash_as_qualifier else [],
            qualifiers={"cachito_hash": "sha256:abcdef"} if hash_as_qualifier else {},
        )

        mock_get_component_url.return_value = raw_url if have_raw_component else None

        download_info = pip._download_url_package(
            mock_requirement,
            tmp_path,
            "cachito-pip-raw",
            ("username", "password"),
            set(trusted_hosts),
        )

        raw_component = f"foo/foo-external-sha256-abcdef.tar.gz"

        assert download_info == {
            "package": "foo",
            "path": tmp_path / "external-foo" / "foo-external-sha256-abcdef.tar.gz",
            "original_url": original_url,
            "url_with_hash": url_with_hash,
            "raw_component_name": raw_component,
            "have_raw_component": have_raw_component,
        }

        download_path = download_info["path"]

        assert f"Looking for raw component '{raw_component}' in 'cachito-pip-raw'" in caplog.text

        if have_raw_component:
            assert f"Found raw component, will download from '{raw_url}'" in caplog.text
            mock_download_file.assert_called_once_with(
                raw_url, download_path, auth=("username", "password")
            )
        else:
            assert f"Raw component not found, will download from '{original_url}'" in caplog.text
            mock_download_file.assert_called_once_with(
                original_url, download_path, insecure=host_is_trusted
            )

    @pytest.mark.parametrize(
        "original_url, url_with_hash",
        [
            (
                "http://example.org/file.zip",
                "http://example.org/file.zip#cachito_hash=sha256:abcdef",
            ),
            (
                "http://example.org/file.zip#egg=spam",
                "http://example.org/file.zip#egg=spam&cachito_hash=sha256:abcdef",
            ),
        ],
    )
    def test_add_cachito_hash_to_url(self, original_url, url_with_hash):
        """Test adding the #cachito_hash fragment to URLs."""
        hsh = "sha256:abcdef"
        assert pip._add_cachito_hash_to_url(urlparse(original_url), hsh) == url_with_hash

    def test_ignored_and_rejected_options(self, caplog):
        """
        Test ignored and rejected options.

        All ignored options should be logged, all rejected options should be in error message.
        """
        all_rejected = [
            "-i",
            "--index-url",
            "--extra-index-url",
            "--no-index",
            "-f",
            "--find-links",
            "--only-binary",
        ]
        options = all_rejected + ["-c", "constraints.txt", "--use-feature", "some_feature", "--foo"]
        req_file = self.mock_requirements_file(options=options)
        with pytest.raises(ValidationError) as exc_info:
            pip.download_dependencies(1, req_file)

        err_msg = (
            "Cachito does not support the following options: -i, --index-url, --extra-index-url, "
            "--no-index, -f, --find-links, --only-binary"
        )
        assert str(exc_info.value) == err_msg

        log_msg = "Cachito will ignore the following options: -c, --use-feature, --foo"
        assert log_msg in caplog.text

    @pytest.mark.parametrize(
        "version_specs",
        [
            [],
            [("<", "1")],
            [("==", "1"), ("<", "2")],
            [("==", "1"), ("==", "1")],  # Probably no reason to handle this?
        ],
    )
    def test_pypi_dep_not_pinned(self, version_specs):
        """Test that unpinned PyPI deps cause a ValidationError."""
        req = self.mock_requirement("foo", "pypi", version_specs=version_specs)
        req_file = self.mock_requirements_file(requirements=[req])
        with pytest.raises(ValidationError) as exc_info:
            pip.download_dependencies(1, req_file)
        msg = f"Requirement must be pinned to an exact version: {req.download_line}"
        assert str(exc_info.value) == msg

    @pytest.mark.parametrize(
        "url",
        [
            # there is no ref
            "git+https://github.com/spam/eggs",
            "git+https://github.com/spam/eggs@",
            # ref is too short
            "git+https://github.com/spam/eggs@abcdef",
            # ref is in the wrong place
            f"git+https://github.com@{GIT_REF}/spam/eggs",
            f"git+https://github.com/spam/eggs#@{GIT_REF}",
        ],
    )
    def test_vcs_dep_no_git_ref(self, url):
        """Test that VCS deps with no git ref cause a ValidationError."""
        req = self.mock_requirement("eggs", "vcs", url=url, download_line=f"eggs @ {url}")
        req_file = self.mock_requirements_file(requirements=[req])

        with pytest.raises(ValidationError) as exc_info:
            pip.download_dependencies(1, req_file)

        msg = f"No git ref in {req.download_line} (expected 40 hexadecimal characters)"
        assert str(exc_info.value) == msg

    @pytest.mark.parametrize("scheme", ["svn", "svn+https"])
    def test_vcs_dep_not_git(self, scheme):
        """Test that VCS deps not from git cause a ValidationError."""
        url = f"{scheme}://example.org/spam/eggs"
        req = self.mock_requirement("eggs", "vcs", url=url, download_line=f"eggs @ {url}")
        req_file = self.mock_requirements_file(requirements=[req])

        with pytest.raises(ValidationError) as exc_info:
            pip.download_dependencies(1, req_file)

        msg = f"Unsupported VCS for {req.download_line}: {scheme}"
        assert str(exc_info.value) == msg

    @pytest.mark.parametrize(
        "hashes, cachito_hash, total",
        [
            ([], None, 0),  # No --hash, no #cachito_hash
            (["sha256:123456", "sha256:abcdef"], None, 2),  # 2x --hash
            (["sha256:123456"], "sha256:abcdef", 2),  # 1x --hash, #cachito_hash
        ],
    )
    def test_url_dep_invalid_hash_count(self, hashes, cachito_hash, total):
        """Test that if URL requirement specifies 0 or more than 1 hash, validation fails."""
        if cachito_hash:
            qualifiers = {"cachito_hash": cachito_hash}
        else:
            qualifiers = {}

        url = "http://example.org/foo.tar.gz"
        req = self.mock_requirement(
            "foo", "url", hashes=hashes, qualifiers=qualifiers, download_line=f"foo @ {url}"
        )
        req_file = self.mock_requirements_file(requirements=[req])

        with pytest.raises(ValidationError) as exc_info:
            pip.download_dependencies(1, req_file)

        assert str(exc_info.value) == (
            f"URL requirement must specify exactly one hash, but specifies {total}: foo @ {url}. "
            "Use the --hash option or the #cachito_hash URL fragment, but not both (or more than "
            "one --hash)."
        )

    @pytest.mark.parametrize(
        "url",
        [
            # .rar is not a valid sdist extension
            "http://example.org/file.rar",
            # extension is in the wrong place
            "http://example.tar.gz/file",
            "http://example.org/file?filename=file.tar.gz",
        ],
    )
    def test_url_dep_unknown_file_ext(self, url):
        """Test that missing / unknown file extension in URL causes a validation error."""
        req = self.mock_requirement("foo", "url", url=url, download_line=f"foo @ {url}")
        req_file = self.mock_requirements_file(requirements=[req])

        with pytest.raises(ValidationError) as exc_info:
            pip.download_dependencies(1, req_file)

        assert str(exc_info.value) == (
            f"URL for requirement does not contain any recognized file extension: "
            f"{req.download_line} (expected one of .zip, .tar.gz, .tar.bz2, .tar.xz, .tar.Z, .tar)"
        )

    @pytest.mark.parametrize(
        "global_require_hash, local_hash", [(True, False), (False, True), (True, True)]
    )
    @pytest.mark.parametrize("requirement_kind", ["pypi", "vcs"])
    def test_requirement_missing_hash(
        self, global_require_hash, local_hash, requirement_kind, caplog
    ):
        """Test that missing hashes cause a validation error."""
        if global_require_hash:
            options = ["--require-hashes"]
        else:
            options = []

        if local_hash:
            req_1 = self.mock_requirement("foo", requirement_kind, hashes=["sha256:abcdef"])
        else:
            req_1 = self.mock_requirement("foo", requirement_kind)

        req_2 = self.mock_requirement("bar", requirement_kind)
        req_file = self.mock_requirements_file(requirements=[req_1, req_2], options=options)

        with pytest.raises(ValidationError) as exc_info:
            pip.download_dependencies(1, req_file)

        if global_require_hash:
            assert "Global --require-hashes option used, will require hashes" in caplog.text
            bad_req = req_2 if local_hash else req_1
        else:
            msg = "At least one dependency uses the --hash option, will require hashes"
            assert msg in caplog.text
            bad_req = req_2

        msg = f"Hash is required, dependency does not specify any: {bad_req.download_line}"
        assert str(exc_info.value) == msg

    @pytest.mark.parametrize(
        "requirement_kind, hash_in_url",
        [("pypi", False), ("vcs", False), ("url", True), ("url", False)],
    )
    def test_malformed_hash(self, requirement_kind, hash_in_url):
        """Test that invalid hash specifiers cause a validation error."""
        if hash_in_url:
            hashes = []
            qualifiers = {"cachito_hash": "malformed"}
        else:
            hashes = ["malformed"]
            qualifiers = {}

        req = self.mock_requirement("foo", requirement_kind, hashes=hashes, qualifiers=qualifiers)
        req_file = self.mock_requirements_file(requirements=[req])

        with pytest.raises(ValidationError) as exc_info:
            pip.download_dependencies(1, req_file)

        msg = "Not a valid hash specifier: 'malformed' (expected algorithm:digest)"
        assert str(exc_info.value) == msg

    @pytest.mark.parametrize("use_hashes", [True, False])
    @pytest.mark.parametrize("have_vcs_raw_component", [True, False])
    @pytest.mark.parametrize("have_url_raw_component", [True, False])
    @pytest.mark.parametrize("trusted_hosts", [[], ["example.org"]])
    @mock.patch("cachito.workers.pkg_managers.pip.RequestBundleDir")
    @mock.patch("cachito.workers.pkg_managers.pip.get_worker_config")
    @mock.patch("cachito.workers.pkg_managers.pip.nexus.get_nexus_hoster_credentials")
    @mock.patch("cachito.workers.pkg_managers.pip._download_pypi_package")
    @mock.patch("cachito.workers.pkg_managers.pip._download_vcs_package")
    @mock.patch("cachito.workers.pkg_managers.pip._download_url_package")
    @mock.patch("cachito.workers.pkg_managers.pip.verify_checksum")
    @mock.patch("cachito.workers.pkg_managers.pip.upload_raw_package")
    def test_download_dependencies(
        self,
        mock_upload_raw_package,
        mock_verify_checksum,
        mock_url_download,
        mock_vcs_download,
        mock_pypi_download,
        mock_get_nexus_creds,
        mock_get_config,
        mock_request_bundle_dir,
        use_hashes,
        have_vcs_raw_component,
        have_url_raw_component,
        trusted_hosts,
        tmp_path,
        caplog,
    ):
        """
        Test dependency downloading.

        Mock the helper functions used for downloading here, test them properly elsewhere.
        """
        # <setup>
        git_url = f"https://github.com/spam/eggs@{GIT_REF}"
        plain_url = "https://example.org/bar.tar.gz#cachito_hash=sha256:654321"

        pypi_req = self.mock_requirement(
            "foo", "pypi", download_line="foo==1.0", version_specs=[("==", "1.0")]
        )
        vcs_req = self.mock_requirement(
            "eggs", "vcs", download_line=f"eggs @ git+{git_url}", url=f"git+{git_url}"
        )
        url_req = self.mock_requirement(
            "bar",
            "url",
            download_line=f"bar @ {plain_url}",
            url=plain_url,
            qualifiers={"cachito_hash": "sha256:654321"},
        )

        if use_hashes:
            pypi_req.hashes = ["sha256:abcdef"]
            vcs_req.hashes = ["sha256:123456"]

        options = []
        for host in trusted_hosts:
            options.append("--trusted-host")
            options.append(host)

        req_file = self.mock_requirements_file(
            requirements=[pypi_req, vcs_req, url_req], options=options,
        )

        proxy_url = "https://pypi-proxy.example.org"
        nexus_auth = requests.auth.HTTPBasicAuth("username", "password")
        proxy_auth = nexus_auth

        raw_repo = "cachito-pip-raw"

        mock_bundle_dir = MockBundleDir(tmp_path)
        pip_deps = mock_bundle_dir.pip_deps_dir

        pypi_download = pip_deps / "foo" / "foo-1.0.tar.gz"
        vcs_download = pip_deps.joinpath(
            "github.com", "spam", "eggs", f"eggs-external-gitcommit-{GIT_REF}.tar.gz",
        )
        url_download = pip_deps / "external-bar" / "bar-external-sha256-654321.tar.gz"

        pypi_info = {"package": "foo", "version": "1.0", "path": pypi_download}
        vcs_info = {
            "package": "eggs",
            "path": vcs_download,
            "repo": "eggs",
            "raw_component_name": f"eggs/eggs-external-gitcommit-{GIT_REF}.tar.gz",
            "have_raw_component": have_vcs_raw_component,
            # etc., not important for this test
        }
        url_info = {
            "package": "bar",
            "original_url": plain_url,
            "url_with_hash": plain_url,
            "path": url_download,
            "raw_component_name": "bar/bar-external-sha256-654321.tar.gz",
            "have_raw_component": have_url_raw_component,
        }

        mock_request_bundle_dir.return_value = mock_bundle_dir
        mock_get_config.return_value = mock.Mock(
            cachito_nexus_pypi_proxy_url=proxy_url, cachito_nexus_pip_raw_repo_name=raw_repo
        )
        mock_get_nexus_creds.return_value = ("username", "password")
        mock_pypi_download.return_value = pypi_info
        mock_vcs_download.return_value = vcs_info
        mock_url_download.return_value = url_info
        # </setup>

        # <call>
        downloads = pip.download_dependencies(1, req_file)
        assert downloads == [
            {**pypi_info, "kind": "pypi"},
            {**vcs_info, "kind": "vcs"},
            {**url_info, "kind": "url"},
        ]
        assert pip_deps.is_dir()
        # </call>

        # <check calls that must always be made>
        mock_request_bundle_dir.assert_called_once_with(1)
        mock_get_config.assert_called_once()
        mock_pypi_download.assert_called_once_with(pypi_req, pip_deps, proxy_url, proxy_auth)
        mock_vcs_download.assert_called_once_with(vcs_req, pip_deps, raw_repo, nexus_auth)
        mock_url_download.assert_called_once_with(
            url_req, pip_deps, raw_repo, nexus_auth, set(trusted_hosts)
        )
        # </check calls that must always be made>

        # <check calls to checksum verification method>
        verify_url_checksum_call = mock.call(
            str(url_download), general.ChecksumInfo("sha256", "654321")
        )
        if use_hashes:
            msg = "At least one dependency uses the --hash option, will require hashes"
            assert msg in caplog.text

            verify_checksum_calls = [
                mock.call(str(pypi_download), general.ChecksumInfo("sha256", "abcdef")),
                mock.call(str(vcs_download), general.ChecksumInfo("sha256", "123456")),
                verify_url_checksum_call,
            ]
        else:
            msg = (
                "No hash options used, will not require hashes for non-HTTP(S) dependencies. "
                "HTTP(S) dependencies always require hashes (use the #cachito_hash URL qualifier)."
            )
            assert msg in caplog.text
            # Hashes for URL dependencies should be verified no matter what
            verify_checksum_calls = [verify_url_checksum_call]

        mock_verify_checksum.assert_has_calls(verify_checksum_calls)
        assert mock_verify_checksum.call_count == len(verify_checksum_calls)

        if use_hashes:
            assert f"Verifying checksum of {pypi_download.name}" in caplog.text
            assert f"Checksum of {pypi_download.name} matches: sha256:abcdef" in caplog.text

            assert f"Verifying checksum of {vcs_download.name}" in caplog.text
            assert f"Checksum of {vcs_download.name} matches: sha256:123456" in caplog.text

        assert f"Verifying checksum of {url_download.name}" in caplog.text
        assert f"Checksum of {url_download.name} matches: sha256:654321" in caplog.text
        # </check calls to checksum verification method>

        # <check calls to raw package upload method>
        if not have_vcs_raw_component:
            assert (
                f"Uploading '{vcs_download.name}' to '{raw_repo}' "
                f"as '{vcs_info['raw_component_name']}'"
            ) in caplog.text
            mock_upload_raw_package.assert_any_call(
                raw_repo,
                vcs_download,
                vcs_info["repo"],
                vcs_download.name,
                is_request_repository=False,
            )
        if not have_url_raw_component:
            assert (
                f"Uploading '{url_download.name}' to '{raw_repo}' "
                f"as '{url_info['raw_component_name']}'"
            ) in caplog.text
            mock_upload_raw_package.assert_any_call(
                raw_repo,
                url_download,
                url_info["package"],
                url_download.name,
                is_request_repository=False,
            )
        assert mock_upload_raw_package.call_count == (
            (0 if have_vcs_raw_component else 1) + (0 if have_url_raw_component else 1)
        )
        # </check calls to raw package upload method>

        # <check basic logging output>
        assert f"Downloading {pypi_req.download_line}" in caplog.text
        assert (
            f"Successfully downloaded {pypi_req.download_line} to deps/pip/foo/foo-1.0.tar.gz"
        ) in caplog.text

        assert f"Downloading {vcs_req.download_line}" in caplog.text
        assert (
            f"Successfully downloaded {vcs_req.download_line} to deps/pip/github.com/spam/eggs/"
            f"eggs-external-gitcommit-{GIT_REF}.tar.gz"
        ) in caplog.text

        assert f"Downloading {url_req.download_line}" in caplog.text
        assert (
            f"Successfully downloaded {url_req.download_line} to deps/pip/external-bar/"
            f"bar-external-sha256-654321.tar.gz"
        ) in caplog.text
        # </check basic logging output>

    @pytest.mark.parametrize(
        "hashes, success",
        [
            (["sha256:good"], True),
            (["sha256:good", "sha256:bad"], True),
            (["sha256:bad", "sha256:good"], True),
            (["sha256:bad"], False),
        ],
    )
    @mock.patch("cachito.workers.pkg_managers.pip.verify_checksum")
    def test_checksum_verification(self, mock_verify_checksum, hashes, success, caplog):
        """Test helper function for checksum verification."""
        path = Path("/foo/bar.tar.gz")

        mock_verify_checksum.side_effect = [
            None if hash_spec == "sha256:good" else CachitoError("Something went wrong")
            for hash_spec in hashes
        ]

        if success:
            pip._verify_hash(path, hashes)
            assert "Checksum of bar.tar.gz matches: sha256:good" in caplog.text

            # Should return on first success
            num_calls = hashes.index("sha256:good") + 1
            num_fails = num_calls - 1
        else:
            with pytest.raises(CachitoError) as exc_info:
                pip._verify_hash(path, hashes)

            msg = "Failed to verify checksum of bar.tar.gz against any of the provided hashes"
            assert str(exc_info.value) == msg

            num_calls = num_fails = len(hashes)

        calls = [
            mock.call(str(path), general.ChecksumInfo(*hash_spec.split(":", 1)))
            for hash_spec in hashes[:num_calls]
        ]
        mock_verify_checksum.assert_has_calls(calls)
        assert mock_verify_checksum.call_count == num_calls

        assert caplog.text.count("Something went wrong") == num_fails
        assert "Verifying checksum of bar.tar.gz" in caplog.text

    @mock.patch("cachito.workers.pkg_managers.pip.nexus.upload_asset_only_component")
    def test_upload_package(self, mock_upload, caplog):
        """Check Nexus upload calls."""
        name = "name"
        path = "fakepath"

        pip.upload_pypi_package(name, path)
        log_msg = f"Uploading {path!r} as a PyPI package to the {name!r} Nexus repository"
        assert log_msg in caplog.text
        mock_upload.assert_called_once_with(name, "pypi", path, to_nexus_hoster=False)

    @mock.patch("cachito.workers.pkg_managers.pip.nexus.upload_raw_component")
    @pytest.mark.parametrize("is_request_repo", [True, False])
    def test_upload_raw_package(self, mock_upload, caplog, is_request_repo):
        """Check Nexus upload calls."""
        name = "name"
        path = "fakepath"
        dest_dir = "name/varsion"
        filename = "name.tar.gz"

        pip.upload_raw_package(name, path, dest_dir, filename, is_request_repo)

        components = [{"path": path, "filename": filename}]
        log_msg = f"Uploading {path!r} as a raw package to the {name!r} Nexus repository"
        assert log_msg in caplog.text
        mock_upload.assert_called_once_with(name, dest_dir, components, not is_request_repo)

    @mock.patch("cachito.workers.pkg_managers.pip.RequestBundleDir")
    @mock.patch("cachito.workers.pkg_managers.pip.nexus.get_nexus_hoster_credentials")
    @mock.patch("cachito.workers.pkg_managers.pip._download_pypi_package")
    def test_download_from_requirement_files(
        self, mock_pypi_download, mock_get_nexus_creds, mock_request_bundle_dir, tmp_path,
    ):
        """Test downloading dependencies from a requirement file list."""
        req_file1 = tmp_path / "requirements.txt"
        req_file1.write_text("foo==1.0.0")
        req_file2 = tmp_path / "requirements-alt.txt"
        req_file2.write_text("bar==0.0.1")

        mock_bundle_dir = MockBundleDir(tmp_path)
        pip_deps = mock_bundle_dir.pip_deps_dir

        pypi_download1 = pip_deps / "foo" / "foo-1.0.0.tar.gz"
        pypi_download2 = pip_deps / "bar" / "bar-0.0.1.tar.gz"

        pypi_info1 = {"package": "foo", "version": "1.0.0", "path": pypi_download1}
        pypi_info2 = {"package": "bar", "version": "0.0.1", "path": pypi_download2}

        mock_request_bundle_dir.return_value = mock_bundle_dir
        mock_get_nexus_creds.return_value = ("username", "password")
        mock_pypi_download.side_effect = [pypi_info1, pypi_info2]

        downloads = pip._download_from_requirement_files(1, [req_file1, req_file2])
        assert downloads == [{**pypi_info1, "kind": "pypi"}, {**pypi_info2, "kind": "pypi"}]


def test_get_pypi_hosted_repo_name():
    assert pip.get_pypi_hosted_repo_name(42) == "cachito-pip-hosted-42"


def test_get_raw_hosted_repo_name():
    assert pip.get_raw_hosted_repo_name(42) == "cachito-pip-raw-42"


def test_get_pypi_hosted_repo_url():
    assert pip.get_pypi_hosted_repo_url(42).endswith("/repository/cachito-pip-hosted-42/")


def test_get_raw_hosted_repo_url():
    assert pip.get_raw_hosted_repo_url(42).endswith("/repository/cachito-pip-raw-42/")


def test_get_hosted_repositories_username():
    assert pip.get_hosted_repositories_username(42) == "cachito-pip-42"


def test_get_index_url():
    index_url = pip.get_index_url("https://repository/cachito-pip-hosted-5/", "admin", "admin123")

    expected = "https://admin:admin123@repository/cachito-pip-hosted-5/simple"

    assert index_url == expected


def test_get_index_url_invalid_url():
    expected = "Nexus PyPI hosted repo URL: repository/cachito-pip-hosted-5/ is not a valid URL"
    with pytest.raises(CachitoError, match=expected):
        pip.get_index_url(
            "repository/cachito-pip-hosted-5/", "admin", "admin123",
        )


@pytest.mark.parametrize("exists", [True, False])
@pytest.mark.parametrize("devel", [True, False])
def test_default_requirement_file_list(tmp_path, exists, devel):
    req_file = None
    requirements = pip.DEFAULT_REQUIREMENTS_FILE
    build_requirements = pip.DEFAULT_BUILD_REQUIREMENTS_FILE
    if exists:
        filename = build_requirements if devel else requirements
        req_file = tmp_path / filename
        req_file.write_text("nothing to see here\n")

    req_files = pip._default_requirement_file_list(tmp_path, devel)
    expected = [str(req_file)] if req_file else []
    assert req_files == expected


@pytest.mark.parametrize("dev", [True, False])
@mock.patch("cachito.workers.pkg_managers.pip.upload_pypi_package")
def test_push_downloaded_requirement_from_pypi(mock_upload, dev):
    pip_repo_name = "test-pip-hosted"
    raw_repo_name = "test-pip-raw"
    name = "foo"
    version = "1"
    path = "some/path"
    req = {"package": name, "version": version, "path": path, "kind": "pypi", "dev": dev}
    expected_dependency = {"name": name, "version": version, "type": "pip", "dev": dev}
    dependency = pip._push_downloaded_requirement(req, pip_repo_name, raw_repo_name)
    mock_upload.assert_called_once_with(pip_repo_name, path)
    assert dependency == expected_dependency


@pytest.mark.parametrize("uploaded", [True, False])
@mock.patch("cachito.workers.pkg_managers.pip.upload_pypi_package")
@mock.patch("cachito.workers.pkg_managers.pip.nexus.get_component_info_from_nexus")
def test_push_downloaded_requirement_from_pypi_duplicated(mock_get_info, mock_upload, uploaded):
    mock_upload.side_effect = CachitoError("stub")
    mock_get_info.return_value = uploaded
    pip_repo_name = "test-pip-hosted"
    raw_repo_name = "test-pip-raw"
    name = "foo"
    version = "1"
    path = "some/path"
    req = {"package": name, "version": version, "path": path, "kind": "pypi", "dev": False}
    expected_dependency = {"name": name, "version": version, "type": "pip", "dev": False}
    if uploaded:
        dependency = pip._push_downloaded_requirement(req, pip_repo_name, raw_repo_name)
        mock_upload.assert_called_once_with(pip_repo_name, path)
        assert dependency == expected_dependency
    else:
        with pytest.raises(CachitoError, match="stub"):
            pip._push_downloaded_requirement(req, pip_repo_name, raw_repo_name)


@pytest.mark.parametrize("dev", [True, False])
@pytest.mark.parametrize("kind", ["url", "vcs"])
@mock.patch("cachito.workers.pkg_managers.pip.upload_raw_package")
def test_push_downloaded_requirement_non_pypi(mock_upload, dev, kind):
    pip_repo_name = "test-pip-hosted"
    raw_repo_name = "test-pip-raw"
    name = "eggs"
    path = "some/path"
    if kind == "vcs":
        version = f"git+https://github.com/spam/eggs@{GIT_REF}"
        raw_component = f"eggs/eggs-external-gitcommit-{GIT_REF}.tar.gz"
    elif kind == "url":
        url = "https://example.org/eggs.tar.gz"
        url_with_hash = f"{url}#cachito_hash=sha256:abcdef"
        version = url_with_hash
        raw_component = f"eggs/eggs.tar.gz"

    dest_dir, filename = raw_component.rsplit("/", 1)
    req = {
        "package": name,
        "raw_component_name": raw_component,
        "path": path,
        "kind": kind,
        "dev": dev,
    }
    if kind == "vcs":
        additional_keys = {
            "url": "https://github.com/spam/eggs",
            "host": "github.com",
            "namespace": "spam",
            "repo": "eggs",
            "ref": GIT_REF,
        }
    elif kind == "url":
        additional_keys = {"original_url": url, "url_with_hash": url_with_hash}

    req.update(additional_keys)

    expected_dependency = {"name": name, "version": version, "type": "pip", "dev": dev}
    dependency = pip._push_downloaded_requirement(req, pip_repo_name, raw_repo_name)
    mock_upload.assert_called_once_with(raw_repo_name, path, dest_dir, filename, True)
    assert dependency == expected_dependency


@pytest.mark.parametrize("kind", ["url", "vcs"])
@pytest.mark.parametrize("uploaded", [True, False])
@mock.patch("cachito.workers.pkg_managers.pip.upload_raw_package")
@mock.patch("cachito.workers.pkg_managers.pip.nexus.get_component_info_from_nexus")
def test_push_downloaded_requirement_non_pypi_duplicated(
    mock_get_info, mock_upload, kind, uploaded
):
    mock_upload.side_effect = CachitoError("stub")
    mock_get_info.return_value = uploaded
    pip_repo_name = "test-pip-hosted"
    raw_repo_name = "test-pip-raw"
    name = "eggs"
    path = "some/path"
    if kind == "vcs":
        version = f"git+https://github.com/spam/eggs@{GIT_REF}"
        raw_component = f"eggs/eggs-external-gitcommit-{GIT_REF}.tar.gz"
    elif kind == "url":
        url = "https://example.org/eggs.tar.gz"
        url_with_hash = f"{url}#cachito_hash=sha256:abcdef"
        version = url_with_hash
        raw_component = f"eggs/eggs.tar.gz"

    dest_dir, filename = raw_component.rsplit("/", 1)
    req = {
        "package": name,
        "raw_component_name": raw_component,
        "path": path,
        "kind": kind,
        "dev": False,
    }
    if kind == "vcs":
        additional_keys = {
            "url": "https://github.com/spam/eggs",
            "host": "github.com",
            "namespace": "spam",
            "repo": "eggs",
            "ref": GIT_REF,
        }
    elif kind == "url":
        additional_keys = {"original_url": url, "url_with_hash": url_with_hash}

    req.update(additional_keys)

    expected_dependency = {"name": name, "version": version, "type": "pip", "dev": False}
    if uploaded:
        dependency = pip._push_downloaded_requirement(req, pip_repo_name, raw_repo_name)
        mock_upload.assert_called_once_with(raw_repo_name, path, dest_dir, filename, True)
        assert dependency == expected_dependency
    else:
        with pytest.raises(CachitoError, match="stub"):
            pip._push_downloaded_requirement(req, pip_repo_name, raw_repo_name)


@mock.patch("cachito.workers.pkg_managers.pip.get_pip_metadata")
def test_resolve_pip_no_deps(mock_metadata, tmp_path):
    mock_metadata.return_value = ("foo", "1.0")
    request = {"id": 1}
    pkg_info = pip.resolve_pip(tmp_path, request)
    expected = {
        "package": {"name": "foo", "version": "1.0", "type": "pip"},
        "dependencies": [],
        "requirements": [],
    }
    assert pkg_info == expected


@mock.patch("cachito.workers.pkg_managers.pip.get_pip_metadata")
def test_resolve_pip_incompatible(mock_metadata, tmp_path):
    expected_error = "Could not resolve package metadata: name"
    mock_metadata.side_effect = CachitoError(expected_error)
    request = {"id": 1}
    with pytest.raises(CachitoError, match=expected_error):
        pip.resolve_pip(tmp_path, request)


@pytest.mark.parametrize("custom_requirements", [True, False])
@mock.patch("cachito.workers.pkg_managers.pip.upload_pypi_package")
@mock.patch("cachito.workers.pkg_managers.pip.get_pip_metadata")
@mock.patch("cachito.workers.pkg_managers.pip.download_dependencies")
def test_resolve_pip(mock_download, mock_metadata, mock_upload, tmp_path, custom_requirements):
    relative_req_file_path = "req.txt"
    relative_build_req_file_path = "breq.txt"
    req_file = tmp_path / pip.DEFAULT_REQUIREMENTS_FILE
    build_req_file = tmp_path / pip.DEFAULT_BUILD_REQUIREMENTS_FILE
    if custom_requirements:
        req_file = tmp_path / relative_req_file_path
        build_req_file = tmp_path / relative_build_req_file_path

    req_file.write_text("bar==2.1")
    build_req_file.write_text("baz==0.0.5")
    mock_metadata.return_value = ("foo", "1.0")
    mock_download.side_effect = [
        [{"kind": "pypi", "path": "some/path", "package": "bar", "version": "2.1"}],
        [{"kind": "pypi", "path": "another/path", "package": "baz", "version": "0.0.5"}],
    ]
    request = {"id": 1}
    if custom_requirements:
        pkg_info = pip.resolve_pip(
            tmp_path,
            request,
            requirement_files=[relative_req_file_path],
            build_requirement_files=[relative_build_req_file_path],
        )
    else:
        pkg_info = pip.resolve_pip(tmp_path, request)

    mock_upload.assert_has_calls(
        [
            mock.call("cachito-pip-hosted-1", "some/path"),
            mock.call("cachito-pip-hosted-1", "another/path"),
        ]
    )
    assert mock_upload.call_count == 2
    expected = {
        "package": {"name": "foo", "version": "1.0", "type": "pip"},
        "dependencies": [
            {"name": "bar", "version": "2.1", "type": "pip", "dev": False},
            {"name": "baz", "version": "0.0.5", "type": "pip", "dev": True},
        ],
        "requirements": [str(req_file), str(build_req_file)],
    }
    assert pkg_info == expected


def test_get_absolute_pkg_file_paths(tmp_path):
    paths = ["foo", "foo/bar", "bar"]
    expected_paths = [str(tmp_path / p) for p in paths]
    assert pip._get_absolute_pkg_file_paths(tmp_path, paths) == expected_paths
    assert pip._get_absolute_pkg_file_paths(tmp_path, []) == []


@pytest.mark.parametrize(
    "component_kind, url",
    (
        ["vcs", f"git+https://www.github.com/cachito/mypkg.git@{'f'*40}?egg=mypkg"],
        ["url", "https://files.cachito.rocks/mypkg.tar.gz"],
        ["invalid", "https://files.cachito.rocks/package.tar.gz"],
    ),
)
def test_get_raw_component_name(component_kind, url):
    requirement = mock.Mock(
        kind=component_kind, url=url, package="package", hashes=["sha256:noRealHash"]
    )
    raw_component = pip.get_raw_component_name(requirement)
    if component_kind == "url":
        assert raw_component == "package/package-external-sha256-noRealHash.tar.gz"
    elif component_kind == "vcs":
        assert raw_component == f"mypkg/mypkg-external-gitcommit-{'f'*40}.tar.gz"
    else:
        assert not raw_component
