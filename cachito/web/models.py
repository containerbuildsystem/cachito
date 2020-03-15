# SPDX-License-Identifier: GPL-3.0-or-later
from collections import OrderedDict
from copy import deepcopy
from enum import Enum
import re

import flask
from flask_login import UserMixin, current_user
import sqlalchemy
from werkzeug.exceptions import Forbidden

from cachito.errors import ValidationError
from cachito.web import db


request_pkg_manager_table = db.Table(
    'request_pkg_manager',
    db.Column('request_id', db.Integer, db.ForeignKey('request.id'), index=True, nullable=False),
    db.Column(
        'pkg_manager_id',
        db.Integer,
        db.ForeignKey('package_manager.id'),
        index=True,
        nullable=False,
    ),
    db.UniqueConstraint('request_id', 'pkg_manager_id'),
)

request_environment_variable_table = db.Table(
    'request_environment_variable',
    db.Column('request_id', db.Integer, db.ForeignKey('request.id'), index=True, nullable=False),
    db.Column(
        'env_var_id',
        db.Integer,
        db.ForeignKey('environment_variable.id'),
        index=True,
        nullable=False,
    ),
    db.UniqueConstraint('request_id', 'env_var_id'),
)

request_flag_table = db.Table(
    'request_flag',
    db.Column('request_id', db.Integer, db.ForeignKey('request.id'), index=True, nullable=False),
    db.Column('flag_id', db.Integer, db.ForeignKey('flag.id'), index=True, nullable=False),
    db.UniqueConstraint('request_id', 'flag_id'),
)


class RequestStateMapping(Enum):
    """
    An Enum that represents the request states.
    """
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


class Package(db.Model):
    """A package associated with the request."""
    # Statically set the table name so that the inherited classes uses this value instead of one
    # derived from the class name
    __tablename__ = 'package'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, index=True, nullable=False)
    type = db.Column(db.String, index=True, nullable=False)
    version = db.Column(db.String, index=True, nullable=False)
    __table_args__ = (
        db.UniqueConstraint('name', 'type', 'version'),
    )

    def __repr__(self):
        return (
            f'<{self.__class__.__name__} id={self.id}, name={self.name} type={self.type} '
            f'version={self.version}>'
        )

    @classmethod
    def validate_json(cls, package):
        """
        Validate the JSON representation of a package.

        :param dict package: the JSON representation of a package
        :raise ValidationError: if the JSON does not match the required schema
        """
        required = {'name', 'type', 'version'}
        if not isinstance(package, dict) or package.keys() != required:
            raise ValidationError(
                'A package must be a JSON object with the following '
                f'keys: {", ".join(sorted(required))}.'
            )

        for key in package.keys():
            if not isinstance(package[key], str):
                raise ValidationError('The "{}" key of the package must be a string'.format(key))

    @classmethod
    def from_json(cls, package):
        cls.validate_json(package)
        return cls(**package)

    def to_json(self):
        """
        Generate the JSON representation of the package.

        :return: the JSON form of the Package object
        :rtype: dict
        """
        return {
            'name': self.name,
            'type': self.type,
            'version': self.version,
        }

    @classmethod
    def get_or_create(cls, package):
        """
        Get the package from the database and create it if it doesn't exist.

        :param dict package: the JSON representation of a package
        :return: an object based on the input dictionary; the object will be added to the database
            session, but not committed, if it was created
        :rtype: Package
        """
        package_object = cls.query.filter_by(**package).first()
        if not package_object:
            package_object = cls.from_json(package)
            db.session.add(package_object)

        return package_object


class Dependency(Package):
    """
    A dependency (e.g. gomod dependency) associated with the request.

    This uses the same table as Package, but has different methods.
    """
    @classmethod
    def validate_json(cls, dependency, for_update=False):
        """
        Validate the JSON representation of a dependency.

        :param dict dependency: the JSON representation of a dependency
        :param bool for_update: a bool that determines if the schema validation should be for an
            update (e.g. input from the PATCH API)
        :raise ValidationError: if the JSON does not match the required schema
        """
        required = {'name', 'type', 'version'}
        optional = set()
        if for_update:
            optional.add('replaces')

        if not isinstance(dependency, dict) or (dependency.keys() - optional) != required:
            msg = (
                'A dependency must be a JSON object with the following '
                f'keys: {", ".join(sorted(required))}.'
            )
            if for_update:
                msg += (
                    ' It may also contain the following optional '
                    f'keys: {", ".join(sorted(optional))}.'
                )
            raise ValidationError(msg)

        for key in dependency.keys():
            if key == 'replaces':
                if dependency[key]:
                    cls.validate_json(dependency[key])
            elif not isinstance(dependency[key], str):
                raise ValidationError('The "{}" key of the dependency must be a string'.format(key))

    @staticmethod
    def validate_replacement_json(dependency_replacement):
        """
        Validate the JSON representation of a dependency replacement.

        :param dict dependency_replacement: the JSON representation of a dependency replacement
        :raise ValidationError: if the JSON does not match the required schema
        """
        required = {'name', 'type', 'version'}
        optional = {'new_name'}
        if (
            not isinstance(dependency_replacement, dict) or
            (dependency_replacement.keys() - required - optional)
        ):
            raise ValidationError(
                'A dependency replacement must be a JSON object with the following '
                f'keys: {", ".join(sorted(required))}. It may also contain the following optional '
                f'keys: {", ".join(sorted(optional))}.'
            )

        for key in required | optional:
            # Skip the validation of optional keys that are not set
            if key not in dependency_replacement and key in optional:
                continue

            if not isinstance(dependency_replacement[key], str):
                raise ValidationError(
                    'The "{}" key of the dependency replacement must be a string'
                    .format(key)
                )

    def to_json(self, replaces=None, force_replaces=False):
        """
        Generate the JSON representation of the dependency.

        :param Dependency replaces: the dependency that is being replaced by this dependency in the
            context of the request
        :param bool force_replaces: a bool that determines if the ``replaces`` key should be set
            even when ``replaces` is ``None``
        :return: the JSON form of the Dependency object
        :rtype: dict
        """
        rv = super().to_json()

        if replaces or force_replaces:
            rv['replaces'] = replaces

        return rv


class RequestPackage(db.Model):
    """An association table between requests and the packages they contain."""
    # A primary key is required by SQLAlchemy when using declaritive style tables, so a composite
    # primary key is used on the two required columns
    request_id = db.Column(
        db.Integer,
        db.ForeignKey('request.id'),
        autoincrement=False,
        index=True,
        primary_key=True,
    )
    package_id = db.Column(
        db.Integer,
        db.ForeignKey('package.id'),
        autoincrement=False,
        index=True,
        primary_key=True,
    )

    __table_args__ = (
        db.UniqueConstraint('request_id', 'package_id'),
    )


class RequestDependency(db.Model):
    """An association table between requests and dependencies."""
    # A primary key is required by SQLAlchemy when using declaritive style tables, so a composite
    # primary key is used on the two required columns
    request_id = db.Column(
        db.Integer,
        db.ForeignKey('request.id'),
        autoincrement=False,
        index=True,
        primary_key=True,
    )
    dependency_id = db.Column(
        db.Integer,
        db.ForeignKey('package.id'),
        autoincrement=False,
        index=True,
        primary_key=True,
    )
    replaced_dependency_id = db.Column(db.Integer, db.ForeignKey('package.id'), index=True)

    __table_args__ = (
        db.UniqueConstraint('request_id', 'dependency_id'),
    )


class Request(db.Model):
    """A Cachito user request."""
    id = db.Column(db.Integer, primary_key=True)
    repo = db.Column(db.String, nullable=False)
    ref = db.Column(db.String, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    submitted_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    request_state_id = db.Column(
        db.Integer,
        db.ForeignKey('request_state.id'),
        index=True,
        unique=True,
    )
    state = db.relationship('RequestState', foreign_keys=[request_state_id])
    dependencies = db.relationship(
        'Dependency',
        foreign_keys=[
            RequestDependency.request_id,
            RequestDependency.dependency_id,
        ],
        secondary=RequestDependency.__table__,
    )
    dependency_replacements = db.relationship(
        'Dependency',
        foreign_keys=[
            RequestDependency.request_id,
            RequestDependency.replaced_dependency_id,
        ],
        secondary=RequestDependency.__table__,
    )
    packages = db.relationship('Package', secondary=RequestPackage.__table__)
    pkg_managers = db.relationship('PackageManager', secondary=request_pkg_manager_table,
                                   backref='requests')
    states = db.relationship(
        'RequestState',
        foreign_keys='RequestState.request_id',
        back_populates='request',
        order_by='RequestState.updated',
    )
    environment_variables = db.relationship(
        'EnvironmentVariable', secondary=request_environment_variable_table, backref='requests',
        order_by='EnvironmentVariable.name')
    submitted_by = db.relationship('User', foreign_keys=[submitted_by_id])
    user = db.relationship('User', foreign_keys=[user_id], back_populates='requests')
    flags = db.relationship(
        'Flag', secondary=request_flag_table, backref='requests', order_by='Flag.name')

    def __repr__(self):
        return '<Request {0!r}>'.format(self.id)

    def add_dependency(self, dependency, replaced_dependency=None):
        """
        Associate a dependency with this request if the association doesn't exist.

        This replaces the use of ``request.dependencies.append`` to be able to associate
        a dependency that is being replaced using the ``replaced_dependency`` keyword argument.

        Note that the association is added to the database session but not committed.

        :param Dependency dependency: a Dependency object
        :param Dependency replaced_dependency: an optional Dependency object to mark as being
            replaced by the input dependency for this request
        :raises ValidationError: if the dependency is already associated with the request, but
            replaced_dependency is different than what is already associated
        """
        # If the ID is not set, then the dependency was just created and is not part of the
        # database's transaction buffer.
        if not dependency.id or (replaced_dependency and not replaced_dependency.id):
            # Send the changes queued up in SQLAlchemy to the database's transaction buffer. This
            # will genereate an ID that can be used for the mapping below.
            db.session.flush()

        mapping = RequestDependency.query.filter_by(
            request_id=self.id, dependency_id=dependency.id).first()

        if mapping:
            if mapping.replaced_dependency_id != getattr(replaced_dependency, 'id', None):
                raise ValidationError(
                    f'The dependency {dependency.to_json()} can\'t have a new replacement set')
            return

        mapping = RequestDependency(request_id=self.id, dependency_id=dependency.id)
        if replaced_dependency:
            mapping.replaced_dependency_id = replaced_dependency.id

        db.session.add(mapping)

    @property
    def dependencies_count(self):
        """
        Get the total number of dependencies for a request.

        :return: the number of dependencies
        :rtype: int
        """
        return db.session.query(sqlalchemy.func.count(RequestDependency.dependency_id)).filter(
            RequestDependency.request_id == self.id).scalar()

    @property
    def packages_count(self):
        """
        Get the total number of packages for a request.

        :return: the number of packages
        :rtype: int
        """
        return db.session.query(sqlalchemy.func.count(RequestPackage.package_id)).filter(
            RequestPackage.request_id == self.id).scalar()

    @property
    def replaced_dependency_mappings(self):
        """
        Get the RequestDependency objects for the current request which contain a replacement.

        :return: a list of RequestDependency
        :rtype: list
        """
        return (
            RequestDependency.query
                             .filter_by(request_id=self.id)
                             .filter(RequestDependency.replaced_dependency_id.isnot(None))
                             .all()
        )

    def to_json(self, verbose=True):
        pkg_managers = [pkg_manager.to_json() for pkg_manager in self.pkg_managers]
        user = None
        # If auth is disabled, there will not be a user associated with this request
        if self.user:
            user = self.user.username

        env_vars_json = OrderedDict(env_var.to_json() for env_var in self.environment_variables)
        rv = {
            'id': self.id,
            'repo': self.repo,
            'ref': self.ref,
            'pkg_managers': pkg_managers,
            'user': user,
            'environment_variables': env_vars_json,
            'flags': [flag.to_json() for flag in self.flags],
        }
        if self.submitted_by:
            rv['submitted_by'] = self.submitted_by.username
        else:
            rv['submitted_by'] = None

        def _state_to_json(state):
            return {
                'state': RequestStateMapping(state.state).name,
                'state_reason': state.state_reason,
                'updated': state.updated.isoformat(),
            }

        if verbose:
            # Use this list comprehension instead of a RequestState.to_json method to avoid
            # including redundant information about the request itself
            states = [_state_to_json(state) for state in self.states]
            # Reverse the list since the latest states should be first
            states = list(reversed(states))
            latest_state = states[0]
            rv['state_history'] = states
            replacement_id_to_replacement = {
                replacement.id: replacement.to_json()
                for replacement in self.dependency_replacements
            }
            dep_id_to_replacement = {
                mapping.dependency_id: replacement_id_to_replacement[mapping.replaced_dependency_id]
                for mapping in self.replaced_dependency_mappings
            }
            rv['dependencies'] = [
                dep.to_json(dep_id_to_replacement.get(dep.id), force_replaces=True)
                for dep in self.dependencies
            ]
            rv['packages'] = [package.to_json() for package in self.packages]
        else:
            latest_state = _state_to_json(self.state)
            rv['dependencies'] = self.dependencies_count
            rv['packages'] = self.packages_count

        # Show the latest state information in the first level of the JSON
        rv.update(latest_state)
        return rv

    @classmethod
    def from_json(cls, kwargs):
        # Validate all required parameters are present
        required_params = {'repo', 'ref'}
        optional_params = {'dependency_replacements', 'flags', 'pkg_managers', 'user'}

        missing_params = required_params - set(kwargs.keys()) - optional_params
        if missing_params:
            raise ValidationError('Missing required parameter(s): {}'
                                  .format(', '.join(missing_params)))

        # Don't allow the user to set arbitrary columns or relationships
        invalid_params = set(kwargs.keys()) - required_params - optional_params
        if invalid_params:
            raise ValidationError(
                'The following parameters are invalid: {}'.format(', '.join(invalid_params)))

        if not re.match(r'^[a-f0-9]{40}$', kwargs['ref']):
            raise ValidationError('The "ref" parameter must be a 40 character hex string')

        request_kwargs = deepcopy(kwargs)

        # Validate package managers are correctly provided
        pkg_managers_names = request_kwargs.pop('pkg_managers', None)
        # If no package managers are specified, then Cachito will detect them automatically
        if pkg_managers_names:
            pkg_managers = PackageManager.get_pkg_managers(pkg_managers_names)
            request_kwargs['pkg_managers'] = pkg_managers

        flag_names = request_kwargs.pop('flags', None)
        if flag_names:
            flag_names = set(flag_names)
            found_flags = (Flag.query
                           .filter(Flag.name.in_(flag_names))
                           .filter(Flag.active)
                           .all())

            if len(flag_names) != len(found_flags):
                found_flag_names = set(flag.name for flag in found_flags)
                invalid_flags = flag_names - found_flag_names
                raise ValidationError(
                    'Invalid/Inactive flag(s): {}'.format(', '.join(invalid_flags)))

            request_kwargs['flags'] = found_flags

        dependency_replacements = request_kwargs.pop('dependency_replacements', [])
        if not isinstance(dependency_replacements, list):
            raise ValidationError('"dependency_replacements" must be an array')

        for dependency_replacement in dependency_replacements:
            Dependency.validate_replacement_json(dependency_replacement)

        submitted_for_username = request_kwargs.pop('user', None)
        # current_user.is_authenticated is only ever False when auth is disabled
        if submitted_for_username and not current_user.is_authenticated:
            raise ValidationError('Cannot set "user" when authentication is disabled')
        if current_user.is_authenticated:
            if submitted_for_username:
                allowed_users = flask.current_app.config['CACHITO_USER_REPRESENTATIVES']
                if current_user.username not in allowed_users:
                    flask.current_app.logger.error(
                        'The user %s tried to submit a request on behalf of another user, but is '
                        'not allowed',
                        current_user.username,
                    )
                    raise Forbidden(
                        'You are not authorized to create a request on behalf of another user'
                    )

                submitted_for = User.get_or_create(submitted_for_username)
                if not submitted_for.id:
                    # Send the changes queued up in SQLAlchemy to the database's transaction buffer.
                    # This will generate an ID that can be used below.
                    db.session.flush()
                request_kwargs['user_id'] = submitted_for.id
                request_kwargs['submitted_by_id'] = current_user.id
            else:
                request_kwargs['user_id'] = current_user.id
        request = cls(**request_kwargs)
        request.add_state('in_progress', 'The request was initiated')
        return request

    def add_state(self, state, state_reason):
        """
        Add a RequestState associated with the current request.

        :param str state: the state name
        :param str state_reason: the reason explaining the state transition
        :raises ValidationError: if the state is invalid
        """
        if self.state and self.state.state_name == 'stale' and state != 'stale':
            raise ValidationError('A stale request cannot change states')

        try:
            state_int = RequestStateMapping.__members__[state].value
        except KeyError:
            raise ValidationError(
                'The state "{}" is invalid. It must be one of: {}.'
                .format(state, ', '.join(RequestStateMapping.get_state_names()))
            )

        request_state = RequestState(state=state_int, state_reason=state_reason)
        self.states.append(request_state)
        # Send the changes queued up in SQLAlchemy to the database's transaction buffer.
        # This will generate an ID that can be used below.
        db.session.add(request_state)
        db.session.flush()
        self.request_state_id = request_state.id


class PackageManager(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)

    def to_json(self):
        return self.name

    @classmethod
    def from_json(cls, name):
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
        pkg_managers = set(pkg_managers)
        found_pkg_managers = cls.query.filter(PackageManager.name.in_(pkg_managers)).all()
        if len(pkg_managers) != len(found_pkg_managers):
            found_pkg_managers_names = set(pkg_manager.name for pkg_manager in found_pkg_managers)
            invalid_pkg_managers = pkg_managers - found_pkg_managers_names
            raise ValidationError(
                'The following package managers are invalid: {}'
                .format(', '.join(invalid_pkg_managers))
            )

        return found_pkg_managers


class RequestState(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    state = db.Column(db.Integer, nullable=False)
    state_reason = db.Column(db.String, nullable=False)
    updated = db.Column(db.DateTime(), nullable=False, default=sqlalchemy.func.now())
    request_id = db.Column(db.Integer, db.ForeignKey('request.id'), index=True, nullable=False)
    request = db.relationship('Request', foreign_keys=[request_id], back_populates='states')

    @property
    def state_name(self):
        """Get the state's display name."""
        if self.state:
            return RequestStateMapping(self.state).name

    def __repr__(self):
        return '<RequestState id={} state="{}" request_id={}>'.format(
            self.id, self.state_name, self.request_id)


class EnvironmentVariable(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    value = db.Column(db.String, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('name', 'value'),
    )

    @classmethod
    def validate_json(cls, name, value):
        if not isinstance(value, str):
            raise ValidationError(
                'The value of environment variables must be a string')

    @classmethod
    def from_json(cls, name, value):
        cls.validate_json(name, value)
        return cls(name=name, value=value)

    def to_json(self):
        return self.name, self.value


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String, index=True, unique=True, nullable=False)
    requests = db.relationship('Request', foreign_keys=[Request.user_id], back_populates='user')

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
            user = User(username=username)
            db.session.add(user)

        return user


class Flag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)

    __table_args__ = (
        db.UniqueConstraint('id', 'name'),
    )

    @classmethod
    def from_json(cls, name):
        return cls(name=name)

    def to_json(self):
        return self.name
