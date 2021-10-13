# SPDX-License-Identifier: GPL-3.0-or-later
import functools
import itertools
import os
import re
from collections import OrderedDict
from copy import deepcopy
from enum import Enum
from typing import Any, Dict, List

import flask
import sqlalchemy.sql
from flask_login import UserMixin, current_user
from werkzeug.exceptions import Forbidden

from cachito.common.packages_data import PackagesData
from cachito.common.paths import RequestBundleDir
from cachito.errors import ValidationError
from cachito.web import content_manifest, db
from cachito.web.validation import validate_dependency_replacements


def is_request_ref_valid(ref: str) -> bool:
    """Check if a string is a valid git ref in the expected format."""
    return re.match(r"^[a-f0-9]{40}$", ref) is not None


request_pkg_manager_table = db.Table(
    "request_pkg_manager",
    db.Column("request_id", db.Integer, db.ForeignKey("request.id"), index=True, nullable=False),
    db.Column(
        "pkg_manager_id",
        db.Integer,
        db.ForeignKey("package_manager.id"),
        index=True,
        nullable=False,
    ),
    db.UniqueConstraint("request_id", "pkg_manager_id"),
)

request_environment_variable_table = db.Table(
    "request_environment_variable",
    db.Column("request_id", db.Integer, db.ForeignKey("request.id"), index=True, nullable=False),
    db.Column(
        "env_var_id",
        db.Integer,
        db.ForeignKey("environment_variable.id"),
        index=True,
        nullable=False,
    ),
    db.UniqueConstraint("request_id", "env_var_id"),
)

request_flag_table = db.Table(
    "request_flag",
    db.Column("request_id", db.Integer, db.ForeignKey("request.id"), index=True, nullable=False),
    db.Column("flag_id", db.Integer, db.ForeignKey("flag.id"), index=True, nullable=False),
    db.UniqueConstraint("request_id", "flag_id"),
)


request_config_file_base64_table = db.Table(
    "request_config_file_base64",
    db.Column("request_id", db.Integer, db.ForeignKey("request.id"), index=True, nullable=False),
    db.Column(
        "config_file_base64_id",
        db.Integer,
        db.ForeignKey("config_file_base64.id"),
        index=True,
        nullable=False,
    ),
    db.UniqueConstraint("request_id", "config_file_base64_id"),
)


class RequestStateMapping(Enum):
    """An Enum that represents the request states."""

    in_progress = 1
    complete = 2
    failed = 3
    stale = 4

    @classmethod
    def get_state_names(cls):
        """
        Get a sorted list of valid state names.

        :return: a sorted list of valid state names
        :rtype: list
        """
        return sorted([state.name for state in cls])

    @staticmethod
    def get_final_states():
        """
        Get the states that are considered final for a request.

        :return: a list of states
        :rtype: list<str>
        """
        return ["complete", "failed"]

    @staticmethod
    def allow_transition(from_state, to_state) -> bool:
        """Check if the state transition is allowed.

        :param from_state: transition from this state.
        :type from_state: RequestStateMapping
        :param to_state: transition to this state.
        :type to_state: RequestStateMapping
        :return: True if the transition is allowed, otherwise False is returned.
        :rtype: bool
        """
        allowed_from_states = ALLOWED_REQUEST_STATE_TRANSITION.get(to_state)
        if allowed_from_states is None:
            return False
        return from_state in allowed_from_states


# to_state: from_states
ALLOWED_REQUEST_STATE_TRANSITION: Dict[RequestStateMapping, List[RequestStateMapping]] = {
    RequestStateMapping.in_progress: [RequestStateMapping.in_progress],
    RequestStateMapping.complete: [RequestStateMapping.in_progress],
    RequestStateMapping.failed: [RequestStateMapping.in_progress],
    RequestStateMapping.stale: [
        RequestStateMapping.complete,
        RequestStateMapping.failed,
        RequestStateMapping.in_progress,
    ],
}


def _validate_configuration_path_value(pkg_manager, config_name, config_path):
    """
    Validate path representing strings in the "packages" parameter of a request.

    :param str pkg_manager: the name of the package manager the configuration is for
    :param str config_name: the name of the configuration setting the path
    :param str config_path: the path to be validated
    :raises ValidationError: if the "config_path" parameter is invalid
    """
    if not (
        isinstance(config_path, str)
        and config_path
        and not os.path.isabs(config_path)
        and os.pardir not in os.path.normpath(config_path)
    ):
        raise ValidationError(
            f'The "{config_name}" values in the "packages.{pkg_manager}" value must be to a '
            "relative path in the source repository"
        )


def _validate_request_package_configs(request_kwargs, pkg_managers_names):
    """
    Validate the "packages" parameter in a new request.

    :param dict request_kwargs: the JSON parameters of the new request
    :param list pkg_managers_names: the list of valid package manager names for the request
    :raises ValidationError: if the "packages" parameter is invalid
    """
    # Validate the custom packages configuration. For example:
    # {"packages": {"npm": [{"path": "client"}]}}
    packages_configs = request_kwargs.get("packages", {})
    if not isinstance(packages_configs, dict):
        raise ValidationError('The "packages" parameter must be an object')

    invalid_package_managers = packages_configs.keys() - set(pkg_managers_names)
    if invalid_package_managers:
        raise ValidationError(
            'The following package managers in the "packages" object do not apply: '
            + ", ".join(invalid_package_managers)
        )

    supported_packages_configs = {"npm", "pip", "gomod", "yarn"}
    unsupported_packages_managers = packages_configs.keys() - supported_packages_configs
    if unsupported_packages_managers:
        raise ValidationError(
            'The following package managers in the "packages" object are unsupported: '
            + ", ".join(unsupported_packages_managers)
        )

    # Validate the values for each package manager configuration (e.g. packages.npm)
    valid_package_config_keys = {
        "npm": {"path"},
        "pip": {"path", "requirements_build_files", "requirements_files"},
        "gomod": {"path"},
        "yarn": {"path"},
    }
    for pkg_manager, packages_config in packages_configs.items():
        invalid_format_error = (
            f'The value of "packages.{pkg_manager}" must be an array of objects with the following '
            f'keys: {", ".join(valid_package_config_keys[pkg_manager])}'
        )
        if not isinstance(packages_config, list):
            raise ValidationError(invalid_format_error)

        for package_config in packages_config:
            if not isinstance(package_config, dict) or not package_config:
                raise ValidationError(invalid_format_error)

            invalid_keys = package_config.keys() - valid_package_config_keys[pkg_manager]
            if invalid_keys:
                raise ValidationError(invalid_format_error)

            if package_config.get("path") is not None:
                _validate_configuration_path_value(pkg_manager, "path", package_config["path"])
            for path in package_config.get("requirements_files", []):
                _validate_configuration_path_value(pkg_manager, "requirements_files", path)
            for path in package_config.get("requirements_build_files", []):
                _validate_configuration_path_value(pkg_manager, "requirements_build_files", path)

    _validate_package_manager_exclusivity(
        pkg_managers_names,
        packages_configs,
        flask.current_app.config["CACHITO_MUTUALLY_EXCLUSIVE_PACKAGE_MANAGERS"],
    )


def _validate_package_manager_exclusivity(pkg_manager_names, package_configs, mutually_exclusive):
    """
    Ensure that no package gets processed by two or more mutually exclusive package managers.

    Note: git-submodule is a special case, because we always fetch all submodules. Therefore
    we do not know which subpaths are actually submodules prior to processing the request, and
    we have to assume that any non-root path is a submodule.

    :param list pkg_manager_names: the list of package manager names for the request
    :param dict package_configs: the "packages" parameter in a request
    :param list mutually_exclusive: list of pairs of mutually exclusive package managers
    :raises ValidationError: if the package configuration has conflicting paths (even implicitly)
    """
    mutually_exclusive = set((a, b) for a, b in mutually_exclusive)

    pkg_manager_paths = {
        pkg_manager: set(
            os.path.normpath(pkg_cfg.get("path", "."))
            for pkg_cfg in package_configs.get(pkg_manager, [{}])
        )
        for pkg_manager in pkg_manager_names
        if pkg_manager != "git-submodule"
    }

    if "git-submodule" in pkg_manager_names:
        _validate_gitsubmodule_exclusivity(pkg_manager_paths, mutually_exclusive)

    # Check all package manager pairs
    for a, b in itertools.combinations(pkg_manager_paths, 2):
        if not ((a, b) in mutually_exclusive or (b, a) in mutually_exclusive):
            continue

        conflicting_paths = pkg_manager_paths[a] & pkg_manager_paths[b]
        if conflicting_paths:
            msg = (
                f"The following paths cannot be processed by both '{a}' and '{b}': "
                f"{', '.join(sorted(conflicting_paths))}"
            )
            raise ValidationError(msg)


def _validate_gitsubmodule_exclusivity(pkg_manager_paths, mutually_exclusive):
    """
    Validate exclusivity of git-submodule with other package managers.

    :param dict pkg_manager_paths: mapping of package managers and their paths in a request
    :param set mutually_exclusive: set of pairs of mutually exclusive package managers
    :raises ValidationError: if any package manager conflicts with git-submodule
    """
    for pkg_manager, paths in pkg_manager_paths.items():
        a, b = pkg_manager, "git-submodule"
        if not ((a, b) in mutually_exclusive or (b, a) in mutually_exclusive):
            continue

        if any(path != "." for path in paths):
            msg = (
                f"Cannot process non-root packages with '{pkg_manager}' "
                "when 'git-submodule' is also set.\nSolution: remove the git submodules "
                "and instead process the upstream repos (corresponding to the submodules) "
                "directly. Each repo should be processed in a separate request."
            )
            raise ValidationError(msg)


class Request(db.Model):
    """A Cachito user request."""

    id = db.Column(db.Integer, primary_key=True)
    created = db.Column(db.DateTime(), nullable=True, index=True, default=sqlalchemy.func.now())
    repo = db.Column(db.String, nullable=False, index=True)
    ref = db.Column(db.String, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    submitted_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    request_state_id = db.Column(
        db.Integer, db.ForeignKey("request_state.id"), index=True, unique=True
    )
    packages_count = db.Column(db.Integer)
    dependencies_count = db.Column(db.Integer)

    state = db.relationship("RequestState", foreign_keys=[request_state_id])
    pkg_managers = db.relationship(
        "PackageManager", secondary=request_pkg_manager_table, backref="requests"
    )
    states = db.relationship(
        "RequestState",
        foreign_keys="RequestState.request_id",
        back_populates="request",
        order_by="RequestState.updated",
    )
    environment_variables = db.relationship(
        "EnvironmentVariable",
        secondary=request_environment_variable_table,
        backref="requests",
        order_by="EnvironmentVariable.name",
    )
    submitted_by = db.relationship("User", foreign_keys=[submitted_by_id])
    user = db.relationship("User", foreign_keys=[user_id], back_populates="requests")
    flags = db.relationship(
        "Flag", secondary=request_flag_table, backref="requests", order_by="Flag.name"
    )
    config_files_base64 = db.relationship(
        "ConfigFileBase64", secondary=request_config_file_base64_table, backref="requests"
    )

    def __repr__(self):
        return "<Request {0!r}>".format(self.id)

    @property
    def content_manifest(self):
        """
        Get the Image Content Manifest for a request.

        :return: the ContentManifest object for the request
        :rtype: ContentManifest
        """
        packages_data = self._get_packages_data()
        packages = [
            content_manifest.Package.from_json(package) for package in packages_data.packages
        ]

        return content_manifest.ContentManifest(self, packages)

    def _is_complete(self):
        if len(self.states) > 0:
            latest_state = self.states[-1]
            return latest_state.state_name == RequestStateMapping.complete.name

        return False

    def _get_packages_data(self):
        packages_data = PackagesData()

        if self._is_complete():
            bundle_dir = RequestBundleDir(
                self.id, root=flask.current_app.config["CACHITO_BUNDLES_DIR"]
            )
            packages_data.load(bundle_dir.packages_data)

        return packages_data

    def to_json(self, verbose=True):
        """
        Generate the JSON representation of the request.

        :param bool verbose: determines if the JSON should have verbose details
        :return: the JSON representation of the request
        :rtype: dict
        """
        pkg_managers = [pkg_manager.to_json() for pkg_manager in self.pkg_managers]
        user = None
        # If auth is disabled, there will not be a user associated with this request
        if self.user:
            user = self.user.username

        env_vars_json = OrderedDict()
        for env_var in self.environment_variables:
            env_vars_json[env_var.name] = env_var.value
        rv = {
            "id": self.id,
            "created": self.created.isoformat(),
            "repo": self.repo,
            "ref": self.ref,
            "pkg_managers": pkg_managers,
            "user": user,
            "environment_variables": env_vars_json,
            "flags": [flag.to_json() for flag in self.flags],
        }
        if self.submitted_by:
            rv["submitted_by"] = self.submitted_by.username
        else:
            rv["submitted_by"] = None

        def _state_to_json(state):
            return {
                "state": RequestStateMapping(state.state).name,
                "state_reason": state.state_reason,
                "updated": state.updated.isoformat(),
            }

        if verbose:
            rv["configuration_files"] = flask.url_for(
                "api_v1.get_request_config_files", request_id=self.id, _external=True
            )
            rv["content_manifest"] = flask.url_for(
                "api_v1.get_request_content_manifest", request_id=self.id, _external=True
            )
            rv["environment_variables_info"] = flask.url_for(
                "api_v1.get_request_environment_variables", request_id=self.id, _external=True
            )
            # Use this list comprehension instead of a RequestState.to_json method to avoid
            # including redundant information about the request itself
            states = [_state_to_json(state) for state in self.states]
            # Reverse the list since the latest states should be first
            states = list(reversed(states))
            latest_state = states[0]
            rv["state_history"] = states

            packages_data = self._get_packages_data()
            rv["packages"] = packages_data.packages
            rv["dependencies"] = packages_data.all_dependencies

            dep: Dict[str, Any]
            for dep in itertools.chain(
                rv["dependencies"],
                (pkg_dep for pkg in rv["packages"] for pkg_dep in pkg["dependencies"]),
            ):
                dep.setdefault("replaces", None)

            if flask.current_app.config["CACHITO_REQUEST_FILE_LOGS_DIR"]:
                rv["logs"] = {
                    "url": flask.url_for(
                        "api_v1.get_request_logs", request_id=self.id, _external=True
                    )
                }
        else:
            latest_state = _state_to_json(self.state)
            rv["packages"] = self.packages_count
            rv["dependencies"] = self.dependencies_count

        # Show the latest state information in the first level of the JSON
        rv.update(latest_state)
        return rv

    @classmethod
    def from_json(cls, kwargs):
        """
        Create a Request object from JSON.

        :param dict kwargs: the dictionary representing the request
        :return: the Request object
        :rtype: Request
        """
        # Validate all required parameters are present
        required_params = {"repo", "ref"}
        optional_params = {
            "created",
            "dependency_replacements",
            "flags",
            "packages",
            "pkg_managers",
            "user",
        }

        missing_params = required_params - set(kwargs.keys()) - optional_params
        if missing_params:
            raise ValidationError(
                "Missing required parameter(s): {}".format(", ".join(missing_params))
            )

        # Don't allow the user to set arbitrary columns or relationships
        invalid_params = set(kwargs.keys()) - required_params - optional_params
        if invalid_params:
            raise ValidationError(
                "The following parameters are invalid: {}".format(", ".join(invalid_params))
            )

        if not is_request_ref_valid(kwargs["ref"]):
            raise ValidationError('The "ref" parameter must be a 40 character hex string')

        request_kwargs = deepcopy(kwargs)

        # Validate package managers are correctly provided
        pkg_managers_names = request_kwargs.pop("pkg_managers", None)
        # Default to the default package managers
        if pkg_managers_names is None:
            flask.current_app.logger.debug(
                "Using the default package manager(s) (%s) on the request",
                ", ".join(flask.current_app.config["CACHITO_DEFAULT_PACKAGE_MANAGERS"]),
            )
            pkg_managers_names = flask.current_app.config["CACHITO_DEFAULT_PACKAGE_MANAGERS"]

        pkg_managers = PackageManager.get_pkg_managers(pkg_managers_names)
        request_kwargs["pkg_managers"] = pkg_managers

        _validate_request_package_configs(request_kwargs, pkg_managers_names or [])
        # Remove this from the request kwargs since it's not used as part of the creation of
        # the request object
        request_kwargs.pop("packages", None)

        flag_names = request_kwargs.pop("flags", None)
        if flag_names:
            flag_names = set(flag_names)
            found_flags = Flag.query.filter(Flag.name.in_(flag_names)).filter(Flag.active).all()

            if len(flag_names) != len(found_flags):
                found_flag_names = set(flag.name for flag in found_flags)
                invalid_flags = flag_names - found_flag_names
                raise ValidationError(
                    "Invalid/Inactive flag(s): {}".format(", ".join(invalid_flags))
                )

            request_kwargs["flags"] = found_flags

        dependency_replacements = request_kwargs.pop("dependency_replacements", [])
        validate_dependency_replacements(dependency_replacements)

        submitted_for_username = request_kwargs.pop("user", None)
        # current_user.is_authenticated is only ever False when auth is disabled
        if submitted_for_username and not current_user.is_authenticated:
            raise ValidationError('Cannot set "user" when authentication is disabled')
        if current_user.is_authenticated:
            if submitted_for_username:
                allowed_users = flask.current_app.config["CACHITO_USER_REPRESENTATIVES"]
                if current_user.username not in allowed_users:
                    flask.current_app.logger.error(
                        "The user %s tried to submit a request on behalf of another user, but is "
                        "not allowed",
                        current_user.username,
                    )
                    raise Forbidden(
                        "You are not authorized to create a request on behalf of another user"
                    )

                submitted_for = User.get_or_create(submitted_for_username)
                request_kwargs["user"] = submitted_for
                request_kwargs["submitted_by"] = current_user
            else:
                request_kwargs["user"] = current_user._get_current_object()
        request = cls(**request_kwargs)
        request.add_state("in_progress", "The request was initiated")
        return request

    def add_state(self, state, state_reason):
        """
        Add a RequestState associated with the current request.

        :param str state: the state name
        :param str state_reason: the reason explaining the state transition
        :raises ValidationError: if the state is invalid
        """
        try:
            new_state: RequestStateMapping = RequestStateMapping[state]
        except KeyError:
            raise ValidationError(
                'The state "{}" is invalid. It must be one of: {}.'.format(
                    state, ", ".join(RequestStateMapping.get_state_names())
                )
            )

        if self.state:
            from_state_name: str = self.state.state_name
            from_state = RequestStateMapping[from_state_name]
            if not RequestStateMapping.allow_transition(from_state, new_state):
                raise ValidationError(
                    f"State transition is not allowed from {from_state_name} to {state}."
                )

        request_state = RequestState(state=new_state.value, state_reason=state_reason)
        self.states.append(request_state)
        # Send the changes queued up in SQLAlchemy to the database's transaction buffer.
        # This will generate an ID that can be used below.
        db.session.add(request_state)
        db.session.flush()
        self.state = request_state


class PackageManager(db.Model):
    """A package manager that Cachito supports."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)

    def to_json(self):
        """
        Generate the JSON representation of the package manager.

        :return: the JSON representation of the package manager.
        :rtype: str
        """
        return self.name

    @classmethod
    def from_json(cls, name):
        """
        Create a PackageManager object from JSON.

        :param str name: the name of the package manager
        :return: the PackageManager object
        :rtype: PackageManager
        """
        return cls(name=name)

    @classmethod
    def get_pkg_managers(cls, pkg_managers):
        """
        Validate the input package managers and return their corresponding database objects.

        :param list pkg_managers: the list of package manager names to retrieve
        :return: a list of valid PackageManager objects
        :rtype: list
        :raise ValidationError: if one of the input package managers is invalid
        """
        if not isinstance(pkg_managers, list) or any(not isinstance(v, str) for v in pkg_managers):
            raise ValidationError('The "pkg_managers" value must be an array of strings')

        if not pkg_managers:
            return []

        pkg_managers = set(pkg_managers)
        found_pkg_managers = cls.query.filter(PackageManager.name.in_(pkg_managers)).all()
        if len(pkg_managers) != len(found_pkg_managers):
            found_pkg_managers_names = set(pkg_manager.name for pkg_manager in found_pkg_managers)
            invalid_pkg_managers = pkg_managers - found_pkg_managers_names
            raise ValidationError(
                "The following package managers are invalid: {}".format(
                    ", ".join(invalid_pkg_managers)
                )
            )

        return found_pkg_managers

    @classmethod
    @functools.lru_cache(maxsize=None)
    def get_by_name(cls, name: str):
        """Get a package manager by name."""
        return cls.query.filter(cls.name == name).scalar()


class RequestState(db.Model):
    """Represents a state (historical or present) of a request."""

    id = db.Column(db.Integer, primary_key=True)
    state = db.Column(db.Integer, nullable=False)
    state_reason = db.Column(db.String, nullable=False)
    updated = db.Column(db.DateTime(), nullable=False, default=sqlalchemy.func.now())
    request_id = db.Column(db.Integer, db.ForeignKey("request.id"), index=True, nullable=False)
    request = db.relationship("Request", foreign_keys=[request_id], back_populates="states")

    @property
    def state_name(self):
        """Get the state's display name."""
        if self.state:
            return RequestStateMapping(self.state).name

    def __repr__(self):
        return '<RequestState id={} state="{}" request_id={}>'.format(
            self.id, self.state_name, self.request_id
        )


class EnvironmentVariable(db.Model):
    """An environment variable that the consumer of the request should set."""

    VALID_KINDS = ("path", "literal")

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    value = db.Column(db.String, nullable=False)
    kind = db.Column(db.String, nullable=False)

    __table_args__ = (db.UniqueConstraint("name", "value", "kind"),)

    @classmethod
    def validate_json(cls, name, info):
        """
        Validate the input environment variable.

        :param str name: the name of the environment variable
        :param dict info: the description of the environment variable. Must include "value" and
            "kind" attributes
        :raises ValidationError: if the environment variable is invalid
        """
        if not isinstance(name, str):
            raise ValidationError("The name of environment variables must be a string")
        if not isinstance(info, dict):
            raise ValidationError("The info of environment variables must be an object")

        required_keys = {"value", "kind"}
        missing_keys = required_keys - info.keys()
        if missing_keys:
            raise ValidationError(
                "The following keys must be set in the info of the environment variables: "
                f"{', '.join(sorted(missing_keys))}"
            )

        invalid_keys = info.keys() - required_keys
        if invalid_keys:
            raise ValidationError(
                "The following keys are not allowed in the info of the environment "
                f"variables: {', '.join(sorted(invalid_keys))}"
            )

        if not isinstance(info["value"], str):
            raise ValidationError("The value of environment variables must be a string")
        kind = info.get("kind")
        if not isinstance(kind, str):
            raise ValidationError("The kind of environment variables must be a string")
        if kind not in cls.VALID_KINDS:
            raise ValidationError(f"The environment variable kind, {kind}, is not supported")

    @classmethod
    def from_json(cls, name, info):
        """
        Create an EnvironmentVariable object from JSON.

        :param str name: the name of the environment variable
        :param dict info: the description of the environment variable
        :return: the EnvironmentVariable object
        :rtype: EnvironmentVariable
        """
        cls.validate_json(name, info)
        return cls(name=name, **info)


class User(db.Model, UserMixin):
    """Represents an external user that owns a Cachito request."""

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String, index=True, unique=True, nullable=False)
    requests = db.relationship("Request", foreign_keys=[Request.user_id], back_populates="user")

    @classmethod
    def get_or_create(cls, username):
        """
        Get the user from the database and create it if it doesn't exist.

        :param str username: the username of the user
        :return: a User object based on the input username; the User object will be
            added to the database session, but not committed, if it was created
        :rtype: User
        """
        user = cls.query.filter_by(username=username).first()
        if not user:
            user = cls(username=username)
            db.session.add(user)

        return user


class Flag(db.Model):
    """A flag to enable a feature on the Cachito request."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, unique=True, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)

    @classmethod
    def from_json(cls, name):
        """
        Create a Flag object from JSON.

        :param str name: the flag name
        :return: the Flag object
        :rtype: Flag
        """
        return cls(name=name)

    def to_json(self):
        """
        Generate the JSON representation of the Flag.

        :return: the JSON representation of the Flag.
        :rtype: str
        """
        return self.name


class ConfigFileBase:
    """A base class with attributes common to all configuration file classes."""

    id = db.Column(db.Integer, primary_key=True)
    # This is the relative path of where the file should be in the extracted bundle
    path = db.Column(db.String, nullable=False, index=True)

    @classmethod
    def validate_json(cls, payload):
        """
        Validate the input configuration file.

        Note that the type of the "content" key's value is not validated. This is the responsibility
        of the child class since it can be any type.

        :param dict payload: the dictionary of the configuration file
        :raises ValidationError: if the configuration file is invalid
        """
        if not isinstance(payload, dict):
            raise ValidationError(f"The {cls.type_name} configuration file must be a JSON object")

        required_keys = {"content", "path", "type"}
        missing_keys = required_keys - payload.keys()
        if missing_keys:
            raise ValidationError(
                f"The following keys for the {cls.type_name} configuration file are "
                f"missing: {', '.join(missing_keys)}"
            )

        invalid_keys = payload.keys() - required_keys
        if invalid_keys:
            raise ValidationError(
                f"The following keys for the {cls.type_name} configuration file are "
                f"invalid: {', '.join(invalid_keys)}"
            )

        if payload["type"] != cls.type_name:
            raise ValidationError(f'The configuration type of "{payload["type"]}" is invalid')

        # The content key type is validated by the child class
        for key in required_keys - {"content"}:
            if not isinstance(payload[key], str):
                raise ValidationError(
                    f'The {cls.type_name} configuration file key of "{key}" must be a string'
                )


class ConfigFileBase64(ConfigFileBase, db.Model):
    """A configuration file that the consumer must set for the bundle to be usable."""

    content = db.Column(db.String, nullable=False)
    type_name = "base64"

    @classmethod
    def get_or_create(cls, path, content):
        """
        Get the configuration file from the database and create it if it doesn't exist.

        :param str path: the relative path of where the file should be in the bundle
        :param str content: the base64 string of the content
        :return: a ConfigFileBase64 object based on the input; the ConfigFileBase64 object will be
            added to the database session, but not committed if it was created
        :rtype: ConfigFileBase64
        """
        config_file = cls.query.filter_by(path=path, content=content).first()
        if not config_file:
            config_file = cls(path=path, content=content)
            db.session.add(config_file)

        return config_file

    def to_json(self):
        """
        Generate the JSON representation of the configuration file.

        :return: the JSON representation of the configuration file.
        :rtype: dict
        """
        return {"content": self.content, "path": self.path, "type": "base64"}

    @classmethod
    def validate_json(cls, payload):
        """
        Validate the input configuration file.

        :param dict payload: the dictionary of the configuration file
        :raises ValidationError: if the configuration file is invalid
        """
        super(ConfigFileBase64, cls).validate_json(payload)

        if not isinstance(payload["content"], str):
            raise ValidationError(
                f'The {cls.type_name} configuration file key of "content" must be a string'
            )
