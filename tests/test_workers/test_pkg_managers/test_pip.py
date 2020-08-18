# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import re
from pathlib import Path
from textwrap import dedent
from unittest import mock

import pytest

from cachito.errors import CachitoError, ValidationError
from cachito.workers.errors import NexusScriptError
from cachito.workers.pkg_managers import pip


def setup_module():
    """Re-enable logging that was disabled at some point in previous tests."""
    pip.log.disabled = False
    pip.log.setLevel(logging.DEBUG)


def write_file_tree(tree_def, rooted_at):
    """
    Write a file tree to disk.

    :param dict tree_def: Definition of file tree, see usage for intuitive examples
    :param (str | Path) rooted_at: Root of file tree, must be an existing directory
    """
    root = Path(rooted_at)
    for entry, value in tree_def.items():
        entry_path = root / entry
        if isinstance(value, str):
            entry_path.write_text(value)
        else:
            entry_path.mkdir()
            write_file_tree(value, entry_path)


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

    @pytest.mark.parametrize(
        "file_contents, expected_requirements, expected_global_options",
        (
            # Dependency from pypi
            ("aiowsgi", [{"package": "aiowsgi", "kind": "pypi", "download_line": "aiowsgi"}], [],),
            # Dependency from pypi with pinned version
            (
                "aiowsgi==0.7",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi==0.7",
                        "version_specs": [("==", "0.7")],
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
                    },
                    {
                        "package": "asn1crypto",
                        "kind": "pypi",
                        "download_line": "asn1crypto==1.3.0",
                        "version_specs": [("==", "1.3.0")],
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
                    },
                    {
                        "package": "asn1crypto",
                        "kind": "pypi",
                        "download_line": "asn1crypto==1.3.0",
                        "version_specs": [("==", "1.3.0")],
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

    def _assert_pip_requirement(self, pip_requirement, expected_requirement):

        default_attributes = {
            "download_line": None,
            "environment_marker": None,
            "extras": [],
            "hashes": [],
            "kind": None,
            "options": [],
            "package": None,
            "qualifiers": {},
            "version_specs": [],
        }
        for attr, default_value in default_attributes.items():
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
