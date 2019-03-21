# SPDX-License-Identifier: GPL-3.0-or-later
from setuptools import setup, find_packages

setup(
    name='cachito',
    version='1.0',
    long_description=__doc__,
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
    install_requires=[
        'flask',
    ],
)
