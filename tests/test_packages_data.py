# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os

import pytest

from cachito.common.packages_data import PackagesData, unique_packages
from cachito.errors import InvalidRequestData


@pytest.mark.parametrize(
    "packages,expected",
    [
        [[], []],
        [
            [{"name": "p1", "type": "gomod", "version": "1"}],
            [{"name": "p1", "type": "gomod", "version": "1"}],
        ],
        [
            # Unsorted packages
            [
                {"name": "p3", "type": "npm", "version": "3"},
                {"name": "p1", "type": "gomod", "version": "1"},
                {"name": "p2", "type": "go-package", "version": "2"},
                {"name": "p3", "type": "npm", "version": "3"},
            ],
            [
                {"name": "p3", "type": "npm", "version": "3"},
                {"name": "p1", "type": "gomod", "version": "1"},
                {"name": "p2", "type": "go-package", "version": "2"},
                {"name": "p3", "type": "npm", "version": "3"},
            ],
        ],
        [
            # Sorted packages
            [
                {"name": "p1", "type": "gomod", "version": "1"},
                {"name": "p1", "type": "gomod", "version": "1"},
                {"name": "p2", "type": "go-package", "version": "2"},
                {"name": "p4", "type": "yarn", "version": "4", "dev": True},
                {"name": "p4", "type": "yarn", "version": "4"},
                {"name": "p3", "type": "npm", "version": "3"},
                {"name": "p3", "type": "npm", "version": "3"},
            ],
            [
                {"name": "p1", "type": "gomod", "version": "1"},
                {"name": "p2", "type": "go-package", "version": "2"},
                {"name": "p4", "type": "yarn", "version": "4", "dev": True},
                {"name": "p4", "type": "yarn", "version": "4"},
                {"name": "p3", "type": "npm", "version": "3"},
            ],
        ],
    ],
)
def test_unique_packages(packages, expected):
    assert expected == list(unique_packages(packages))


def test_sort_packages_and_deps_in_place():
    # using different package managers to test sorting by type
    packages = [
        {"name": "pkg6", "type": "pip", "version": "1.0.0"},
        {"name": "pkg5", "type": "pip", "version": "1.0.0"},
        # test sorting by name
        {"name": "pkg3", "type": "npm", "version": "1.0.0"},
        {"name": "pkg2", "type": "npm", "version": "1.2.3"},
        # test sorting by version
        {"name": "pkg4", "type": "npm", "version": "1.2.5"},
        {"name": "pkg4", "type": "npm", "version": "1.2.0"},
        {
            "name": "pkg1",
            "type": "gomod",
            "version": "1.0.0",
            "dependencies": [
                # test sorting of dependencies
                {"name": "pkg1-dep0", "type": "gomod", "version": "1.0.0", "dev": True},
                {"name": "pkg1-dep2", "type": "gomod", "version": "1.0.0"},
                {"name": "pkg1-dep1", "type": "gomod", "version": "1.0.0"},
            ],
        },
    ]

    sorted_packages = [
        {
            "name": "pkg1",
            "type": "gomod",
            "version": "1.0.0",
            "dependencies": [
                {"name": "pkg1-dep1", "type": "gomod", "version": "1.0.0"},
                {"name": "pkg1-dep2", "type": "gomod", "version": "1.0.0"},
                {"name": "pkg1-dep0", "type": "gomod", "version": "1.0.0", "dev": True},
            ],
        },
        {"name": "pkg2", "type": "npm", "version": "1.2.3", "dependencies": []},
        {"name": "pkg3", "type": "npm", "version": "1.0.0", "dependencies": []},
        {"name": "pkg4", "type": "npm", "version": "1.2.0", "dependencies": []},
        {"name": "pkg4", "type": "npm", "version": "1.2.5", "dependencies": []},
        {"name": "pkg5", "type": "pip", "version": "1.0.0", "dependencies": []},
        {"name": "pkg6", "type": "pip", "version": "1.0.0", "dependencies": []},
    ]

    packages_data = PackagesData()
    for package in packages:
        packages_data.add_package(package, os.curdir, deps=package.get("dependencies", []))

    packages_data.sort()

    assert packages_data.packages == sorted_packages


@pytest.mark.parametrize(
    "params,expected",
    [
        [
            [[{"name": "pkg1", "type": "gomod", "version": "1.0.0"}, "path1", []]],
            [
                {
                    "name": "pkg1",
                    "type": "gomod",
                    "version": "1.0.0",
                    "path": "path1",
                    "dependencies": [],
                },
            ],
        ],
        [
            [
                [{"name": "pkg1", "type": "gomod", "version": "1.0.0"}, "path1", []],
                [{"name": "pkg2", "type": "yarn", "version": "2.3.1"}, os.curdir, []],
                [
                    {"name": "pkg3", "type": "npm", "version": "1.2.3"},
                    os.curdir,
                    [{"name": "async@15.0.0"}],
                ],
            ],
            [
                {
                    "name": "pkg1",
                    "type": "gomod",
                    "version": "1.0.0",
                    "path": "path1",
                    "dependencies": [],
                },
                {"name": "pkg2", "type": "yarn", "version": "2.3.1", "dependencies": []},
                {
                    "name": "pkg3",
                    "type": "npm",
                    "version": "1.2.3",
                    "dependencies": [{"name": "async@15.0.0"}],
                },
            ],
        ],
        [
            [
                [{"name": "pkg1", "type": "gomod", "version": "1.0.0"}, "path1", []],
                [
                    {"name": "pkg1", "type": "gomod", "version": "1.0.0"},
                    "somewhere/",
                    [{"name": "golang.org/x/text/internal/tag"}],
                ],
            ],
            pytest.raises(InvalidRequestData, match="Duplicate package"),
        ],
    ],
)
def test_add_package(params, expected):
    pd = PackagesData()
    if isinstance(expected, list):
        for pkg_info, path, deps in params:
            pd.add_package(pkg_info, path, deps)
        assert expected == pd._packages
    else:
        with expected:
            for pkg_info, path, deps in params:
                pd.add_package(pkg_info, path, deps)


@pytest.mark.parametrize(
    "params,expected",
    [
        [[], {"packages": []}],
        [
            [
                [{"name": "pkg1", "type": "gomod", "version": "1.0.0"}, "path1", []],
                [
                    {"name": "pkg3", "type": "npm", "version": "1.2.3"},
                    os.curdir,
                    [{"name": "async", "type": "npm", "version": "15.0.0"}],
                ],
            ],
            {
                "packages": [
                    {
                        "name": "pkg1",
                        "type": "gomod",
                        "version": "1.0.0",
                        "path": "path1",
                        "dependencies": [],
                    },
                    {
                        "name": "pkg3",
                        "type": "npm",
                        "version": "1.2.3",
                        "dependencies": [{"name": "async", "type": "npm", "version": "15.0.0"}],
                    },
                ],
            },
        ],
    ],
)
def test_write_to_file(params, expected, tmpdir):
    pd = PackagesData()
    for pkg_info, path, deps in params:
        pd.add_package(pkg_info, path, deps)
    filename = os.path.join(tmpdir, "data.json")
    pd.write_to_file(filename)
    with open(filename, "r") as f:
        assert expected == json.load(f)


@pytest.mark.parametrize(
    "packages_data,expected",
    [
        [None, []],
        [{}, []],
        [{"data": []}, []],
        [
            {
                "packages": [
                    {
                        "name": "pkg1",
                        "type": "gomod",
                        "version": "1.0.0",
                        "path": "path1",
                        "dependencies": [],
                    },
                    {
                        "name": "pkg3",
                        "type": "npm",
                        "version": "1.2.3",
                        "dependencies": [{"name": "async@15.0.0"}],
                    },
                ],
            },
            [
                {
                    "name": "pkg1",
                    "type": "gomod",
                    "version": "1.0.0",
                    "path": "path1",
                    "dependencies": [],
                },
                {
                    "name": "pkg3",
                    "type": "npm",
                    "version": "1.2.3",
                    "dependencies": [{"name": "async@15.0.0"}],
                },
            ],
        ],
    ],
)
def test_load_from_file(packages_data, expected, tmpdir):
    filename = os.path.join(tmpdir, "data.json")
    if packages_data is not None:
        with open(filename, "w") as f:
            f.write(json.dumps(packages_data))
    pd = PackagesData()
    pd.load(filename)
    assert expected == pd._packages


@pytest.mark.parametrize(
    "packages_data,expected_dependencies",
    [
        [
            {
                "packages": [
                    {"name": "n2", "type": "go-package", "version": "v2", "dependencies": []},
                    {"name": "n1", "type": "gomod", "version": "v1", "dependencies": []},
                ],
            },
            [],
        ],
        [
            {
                "packages": [
                    {
                        "name": "n2",
                        "type": "go-package",
                        "version": "v2",
                        "dependencies": [
                            {"name": "d1", "type": "go-package", "version": "1"},
                            {"name": "d2", "replaces": None, "type": "go-package", "version": "2"},
                        ],
                    },
                    {
                        "name": "n1",
                        "type": "gomod",
                        "version": "v1",
                        "dependencies": [
                            {"name": "d1", "type": "gomod", "version": "1"},
                            {"name": "d2", "replaces": None, "type": "gomod", "version": "2"},
                        ],
                    },
                    {
                        "name": "p1",
                        "type": "npm",
                        "version": "v2",
                        "dependencies": [{"name": "async", "type": "npm", "version": "1.2.0"}],
                    },
                    {
                        "name": "p2",
                        "type": "npm",
                        "version": "20210621",
                        "dependencies": [
                            {"name": "async", "type": "npm", "version": "1.2.0"},
                            {"name": "underscore", "type": "npm", "version": "1.13.0"},
                        ],
                    },
                ],
            },
            [
                {"name": "d1", "type": "go-package", "version": "1"},
                {"name": "d2", "replaces": None, "type": "go-package", "version": "2"},
                {"name": "d1", "type": "gomod", "version": "1"},
                {"name": "d2", "replaces": None, "type": "gomod", "version": "2"},
                # Only one async in the final dependencies list
                {"name": "async", "type": "npm", "version": "1.2.0"},
                {"name": "underscore", "type": "npm", "version": "1.13.0"},
            ],
        ],
    ],
)
def test_all_dependencies(packages_data, expected_dependencies, tmpdir):
    filename = os.path.join(tmpdir, "data.json")
    with open(filename, "w") as f:
        f.write(json.dumps(packages_data))
    pd = PackagesData()
    pd.load(filename)
    assert expected_dependencies == pd.all_dependencies
