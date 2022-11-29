# SPDX-License-Identifier: GPL-3.0-or-later
from setuptools import find_packages, setup

GEMLOCK_PARSER_REPO_URL = "https://github.com/containerbuildsystem/gemlock-parser.git"
GEMLOCK_PARSER_PIP_REF = f"git+{GEMLOCK_PARSER_REPO_URL}@master#egg=gemlock_parser"

setup(
    name="cachito",
    long_description=__doc__,
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
    install_requires=[
        "backoff",
        "celery>=5",
        f"gemlock_parser @ {GEMLOCK_PARSER_PIP_REF}",
        "gitpython",
        "kombu>=5",  # A celery dependency but it's directly imported
        "packaging",
        "pyarn",
        "requests_kerberos",
        "requests",
        "semver",
        "setuptools",
    ],
    extras_require={
        "web": [
            "Flask",
            "flask-login",
            "Flask-Migrate",
            "Flask-SQLAlchemy",
            "psycopg2-binary",
            "prometheus-flask-exporter",
            "pydantic",
        ],
    },
    entry_points={
        "console_scripts": [
            "cachito=cachito.web.manage:cli",
            "cachito-cleanup=cachito.workers.cleanup_job:main",
            "cachito-update-nexus-scripts=cachito.workers.nexus:create_or_update_scripts",
        ]
    },
    classifiers=[
        "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.10",
    ],
    license="GPLv3+",
    python_requires=">=3.10",
    use_scm_version={
        "version_scheme": "post-release",
    },
    setup_requires=['setuptools_scm'],
    scripts=["bin/pip_find_builddeps.py"],
)
