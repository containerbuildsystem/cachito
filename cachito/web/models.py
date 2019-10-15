# SPDX-License-Identifier: GPL-3.0-or-later
from collections import OrderedDict
from copy import deepcopy
from enum import Enum
import os

import flask
from flask_login import UserMixin, current_user
import sqlalchemy

from cachito.errors import ValidationError
from cachito.web import db


request_pkg_manager_table = db.Table(
    'request_pkg_manager',
    db.Column('request_id', db.Integer, db.ForeignKey('request.id'), nullable=False),
    db.Column('pkg_manager_id', db.Integer, db.ForeignKey('package_manager.id'), nullable=False),
    db.UniqueConstraint('request_id', 'pkg_manager_id'),
)

request_environment_variable_table = db.Table(
    'request_environment_variable',
    db.Column('request_id', db.Integer, db.ForeignKey('request.id'), nullable=False),
    db.Column('env_var_id', db.Integer, db.ForeignKey('environment_variable.id'), nullable=False),
    db.UniqueConstraint('request_id', 'env_var_id'),
)

request_flag_table = db.Table(
    'request_flag',
    db.Column('request_id', db.Integer, db.ForeignKey('request.id'), nullable=False),
    db.Column('flag_id', db.Integer, db.ForeignKey('flag.id'), nullable=False),
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


class Dependency(db.Model):
    """A dependency (e.g. gomod dependency) associated with the request."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    type = db.Column(db.String, nullable=False)
    version = db.Column(db.String, nullable=False)
    __table_args__ = (
        db.UniqueConstraint('name', 'type', 'version'),
    )

    def __repr__(self):
        return (
            '<Dependency id={0!r}, name={1!r} type={2!r} version={3!r}>'
            .format(self.id, self.name, self.type, self.version)
        )

    @classmethod
    def get_or_create(cls, dependency):
        """
        Get the dependency from the database and create it if it doesn't exist.

        :param dict Dependency: the JSON representation of a dependency
        :return: a Dependency object based on the input dictionary; the Dependency object will be
            added to the database session, but not committed, if it was created
        :rtype: Dependency
        """
        dependency_object = Dependency.query.filter_by(**dependency).first()
        if not dependency_object:
            dependency_object = Dependency.from_json(dependency)
            db.session.add(dependency_object)

        return dependency_object

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

    @classmethod
    def from_json(cls, dependency):
        cls.validate_json(dependency)
        return cls(**dependency)

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
        rv = {
            'name': self.name,
            'type': self.type,
            'version': self.version,
        }

        if replaces or force_replaces:
            rv['replaces'] = replaces

        return rv


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
        db.ForeignKey('dependency.id'),
        autoincrement=False,
        index=True,
        primary_key=True,
    )
    replaced_dependency_id = db.Column(db.Integer, db.ForeignKey('dependency.id'), index=True)

    __table_args__ = (
        db.UniqueConstraint('request_id', 'dependency_id'),
    )


class Request(db.Model):
    """A Cachito user request."""
    id = db.Column(db.Integer, primary_key=True)
    repo = db.Column(db.String, nullable=False)
    ref = db.Column(db.String, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    dependencies = db.relationship(
        'Dependency',
        backref='requests',
        foreign_keys=[
            RequestDependency.request_id,
            RequestDependency.dependency_id,
        ],
        secondary=RequestDependency.__table__,
    )
    dependency_replacements = db.relationship(
        'Dependency',
        backref='replace_requests',
        foreign_keys=[
            RequestDependency.request_id,
            RequestDependency.replaced_dependency_id,
        ],
        secondary=RequestDependency.__table__,
    )
    pkg_managers = db.relationship('PackageManager', secondary=request_pkg_manager_table,
                                   backref='requests')
    states = db.relationship(
        'RequestState', back_populates='request', order_by='RequestState.updated')
    environment_variables = db.relationship(
        'EnvironmentVariable', secondary=request_environment_variable_table, backref='requests',
        order_by='EnvironmentVariable.name')
    user = db.relationship('User', back_populates='requests')
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
    def bundle_archive(self):
        """
        Get the path to the request's bundle archive.

        :return: the path to the request's bundle archive
        :rtype: str
        """
        cachito_bundles_dir = flask.current_app.config['CACHITO_BUNDLES_DIR']
        return os.path.join(cachito_bundles_dir, f'{self.id}.tar.gz')

    @property
    def bundle_temp_files(self):
        """
        Get the path to the request's temporary files used to create the bundle archive.

        :return: the path to the temporary files
        :rtype: str
        """
        cachito_bundles_dir = flask.current_app.config['CACHITO_BUNDLES_DIR']
        return os.path.join(cachito_bundles_dir, 'temp', str(self.id))

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
        # Use this list comprehension instead of a RequestState.to_json method to avoid including
        # redundant information about the request itself
        states = [
            {
                'state': RequestStateMapping(state.state).name,
                'state_reason': state.state_reason,
                'updated': state.updated.isoformat(),
            }
            for state in self.states
        ]
        # Reverse the list since the latest states should be first
        states = list(reversed(states))
        latest_state = states[0]
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
        # Show the latest state information in the first level of the JSON
        rv.update(latest_state)
        if verbose:
            rv.update({'state_history': states})
            replacement_id_to_replacement = {
                replacement.id: replacement.to_json()
                for replacement in self.dependency_replacements
            }
            dep_id_to_replacement = {
                mapping.dependency_id: replacement_id_to_replacement[mapping.replaced_dependency_id]
                for mapping in self.replaced_dependency_mappings
            }
            rv.update({'dependencies': [
                dep.to_json(dep_id_to_replacement.get(dep.id), force_replaces=True)
                for dep in self.dependencies
            ]})
        else:
            rv.update({'dependencies': self.dependencies_count})
        return rv

    @classmethod
    def from_json(cls, kwargs):
        # Validate all required parameters are present
        required_params = {'repo', 'ref'}
        optional_params = {'dependency_replacements', 'flags', 'pkg_managers'}
        missing_params = required_params - set(kwargs.keys()) - optional_params
        if missing_params:
            raise ValidationError('Missing required parameter(s): {}'
                                  .format(', '.join(missing_params)))

        # Don't allow the user to set arbitrary columns or relationships
        invalid_params = set(kwargs.keys()) - required_params - optional_params
        if invalid_params:
            raise ValidationError(
                'The following parameters are invalid: {}'.format(', '.join(invalid_params)))

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

        # current_user.is_authenticated is only ever False when auth is disabled
        if current_user.is_authenticated:
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
        if self.last_state and self.last_state.state_name == 'stale' and state != 'stale':
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

    @property
    def last_state(self):
        """
        Get the last RequestState associated with the current request.

        :return: the last RequestState
        :rtype: RequestState
        """
        return (
            RequestState.query
            .filter_by(request_id=self.id)
            .order_by(RequestState.updated.desc(), RequestState.id.desc())
            .first()
        )


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
    request_id = db.Column(db.Integer, db.ForeignKey('request.id'), nullable=False)
    request = db.relationship('Request', back_populates='states')

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
    username = db.Column(db.String, unique=True, nullable=False)
    requests = db.relationship('Request', back_populates='user')


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
