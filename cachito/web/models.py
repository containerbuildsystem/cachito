# SPDX-License-Identifier: GPL-3.0-or-later
from copy import deepcopy

from cachito.errors import ValidationError
from cachito.web import db


request_pkg_manager_table = db.Table(
    'request_pkg_manager',
    db.Column('request_id', db.Integer, db.ForeignKey('request.id'), nullable=False),
    db.Column('pkg_manager_id', db.Integer, db.ForeignKey('package_manager.id'), nullable=False)
)


class Request(db.Model):
    """A Cachito user request."""
    id = db.Column(db.Integer, primary_key=True)
    repo = db.Column(db.String, nullable=False)
    ref = db.Column(db.String, nullable=False)
    pkg_managers = db.relationship('PackageManager', secondary=request_pkg_manager_table,
                                   backref='requests')

    def __repr__(self):
        return '<Request {0!r}>'.format(self.id)

    def to_json(self):
        pkg_managers = [pkg_manager.to_json() for pkg_manager in self.pkg_managers]
        return {
            'id': self.id,
            'repo': self.repo,
            'ref': self.ref,
            'pkg_managers': pkg_managers,
        }

    @classmethod
    def from_json(cls, kwargs):
        # Validate all required parameters are present
        required_params = {'repo', 'ref', 'pkg_managers'}
        missing_params = required_params - set(kwargs.keys())
        if missing_params:
            raise ValidationError('Missing required parameter(s): {}'
                                  .format(', '.join(missing_params)))
        kwargs = deepcopy(kwargs)

        # Validate package managers are correctly provided
        pkg_managers_names = kwargs.pop('pkg_managers', None)
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

        kwargs['pkg_managers'] = found_pkg_managers

        try:
            request = cls(**kwargs)
        except TypeError as e:
            # Handle extraneous parameters.
            raise ValidationError(str(e))
        return request


class PackageManager(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)

    def to_json(self):
        return self.name

    @classmethod
    def from_json(cls, name):
        return cls(name=name)
