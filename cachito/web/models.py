# SPDX-License-Identifier: GPL-3.0-or-later
from copy import deepcopy
from enum import Enum

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

request_dependency_table = db.Table(
    'request_dependency',
    db.Column('request_id', db.Integer, db.ForeignKey('request.id'), nullable=False),
    db.Column('dependency_id', db.Integer, db.ForeignKey('dependency.id'), nullable=False),
    db.UniqueConstraint('request_id', 'dependency_id'),
)


class RequestStateMapping(Enum):
    """
    An Enum that represents the request states.
    """
    in_progress = 1
    complete = 2
    failed = 3

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

    @staticmethod
    def validate_json(dependency):
        """
        Validate the JSON representation of a dependency.

        :param any dependency: the JSON representation of a dependency
        :raise ValidationError: if the JSON does not match the required schema
        """
        if not isinstance(dependency, dict) or dependency.keys() != {'name', 'type', 'version'}:
            raise ValidationError(
                'A dependency must be a JSON object with the keys name, type, and version')

        for key in ('name', 'type', 'version'):
            if not isinstance(dependency[key], str):
                raise ValidationError('The "{}" key of the dependency must be a string'.format(key))

    @classmethod
    def from_json(cls, dependency):
        cls.validate_json(dependency)
        return cls(**dependency)

    def to_json(self):
        return {
            'name': self.name,
            'type': self.type,
            'version': self.version,
        }


class Request(db.Model):
    """A Cachito user request."""
    id = db.Column(db.Integer, primary_key=True)
    repo = db.Column(db.String, nullable=False)
    ref = db.Column(db.String, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    dependencies = db.relationship(
        'Dependency', secondary=request_dependency_table, backref='requests')
    pkg_managers = db.relationship('PackageManager', secondary=request_pkg_manager_table,
                                   backref='requests')
    states = db.relationship(
        'RequestState', back_populates='request', order_by='RequestState.updated')
    user = db.relationship('User', back_populates='requests')

    def __repr__(self):
        return '<Request {0!r}>'.format(self.id)

    def to_json(self):
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

        rv = {
            'dependencies': [dep.to_json() for dep in self.dependencies],
            'id': self.id,
            'repo': self.repo,
            'ref': self.ref,
            'pkg_managers': pkg_managers,
            'state_history': states,
            'user': user,
        }
        # Show the latest state information in the first level of the JSON
        rv.update(latest_state)
        return rv

    @classmethod
    def from_json(cls, kwargs):
        # Validate all required parameters are present
        required_params = {'repo', 'ref', 'pkg_managers'}
        missing_params = required_params - set(kwargs.keys())
        if missing_params:
            raise ValidationError('Missing required parameter(s): {}'
                                  .format(', '.join(missing_params)))

        # Don't allow the user to set arbitrary columns or relationships
        invalid_params = kwargs.keys() - required_params
        if invalid_params:
            raise ValidationError(
                'The following parameters are invalid: {}'.format(', '.join(invalid_params)))

        request_kwargs = deepcopy(kwargs)

        # Validate package managers are correctly provided
        pkg_managers_names = request_kwargs.pop('pkg_managers', None)
        if not pkg_managers_names:
            raise ValidationError('At least one package manager is required')

        pkg_managers_names = set(pkg_managers_names)
        found_pkg_managers = (PackageManager.query
                              .filter(PackageManager.name.in_(pkg_managers_names))
                              .all())
        if len(pkg_managers_names) != len(found_pkg_managers):
            found_pkg_managers_names = set(pkg_manager.name for pkg_manager in found_pkg_managers)
            invalid_pkg_managers = pkg_managers_names - found_pkg_managers_names
            raise ValidationError('Invalid package manager(s): {}'
                                  .format(', '.join(invalid_pkg_managers)))

        request_kwargs['pkg_managers'] = found_pkg_managers
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


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String, unique=True, nullable=False)
    requests = db.relationship('Request', back_populates='user')
