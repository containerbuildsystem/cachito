# SPDX-License-Identifier: GPL-3.0-or-later
from setuptools import setup, find_packages


def get_requirements(req_file):
    """
    Get the requirements listed in a requirements file.

    :param str req_file: the path to the requirements file, relative to this file
    :return: the list of requirements
    :rtype: list
    """
    with open(req_file) as fd:
        lines = fd.readlines()

    dependencies = []
    for line in lines:
        dep = line.strip()
        # Skip comments and inclusion of other requirements files
        if not dep.startswith("#") and not dep.startswith("-r"):
            dependencies.append(dep)
    return dependencies


setup(
    name="cachito",
    version="1.0",
    long_description=__doc__,
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
    install_requires=get_requirements("requirements.txt"),
    extras_require={"web": get_requirements("requirements-web.txt")},
    entry_points={
        "console_scripts": [
            "cachito=cachito.web.manage:cli",
            "cachito-cleanup=cachito.workers.cleanup_job:main",
            "cachito-update-nexus-scripts=cachito.workers.nexus:create_or_update_scripts",
        ]
    },
    classifiers=[
        "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
    ],
    license="GPLv3+",
    python_requires=">=3.6",
)
