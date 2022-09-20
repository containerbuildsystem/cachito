# SPDX-License-Identifier: GPL-3.0-or-later
from setuptools import find_packages, setup

setup(
    name="cachi2",
    long_description=__doc__,
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
    classifiers=[
        "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
    ],
    license="GPLv3+",
    python_requires=">=3.9",
    setup_requires=['setuptools-git-versioning'],
    setuptools_git_versioning={
        "enabled": True,
        "dev_template": "{tag}.post{ccount}+git.{sha}",
    },
)
