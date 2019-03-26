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
        if not dep.startswith('#') and not dep.startswith('-r'):
            dependencies.append(dep)
    return dependencies


setup(
    name='cachito',
    version='1.0',
    long_description=__doc__,
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
    extras_require={
        'web': get_requirements('requirements.txt'),
        'workers': get_requirements('requirements-workers.txt'),
    },
)
