# SPDX-License-Identifier: GPL-3.0-or-later

import datetime
from typing import Any, Dict, List

import jsonschema
from connexion import decorators
from connexion.exceptions import BadRequestProblem, ExtraParameterProblem

from cachito.errors import ValidationError


def validate_replacement(replacement: Dict[str, Any]) -> None:
    """
    Validate the JSON representation of a dependency replacement.

    :param replacement: the JSON representation of a dependency replacement
    :type replacement: dict[str, any]
    :raise ValidationError: if the JSON does not match the required schema
    """
    required = {"name", "type", "version"}
    optional = {"new_name"}

    if not isinstance(replacement, dict) or (replacement.keys() - required - optional):
        raise ValidationError(
            "A dependency replacement must be a JSON object with the following "
            f'keys: {", ".join(sorted(required))}. It may also contain the following optional '
            f'keys: {", ".join(sorted(optional))}.'
        )

    for key in required | optional:
        # Skip the validation of optional keys that are not set
        if key not in replacement and key in optional:
            continue

        if not isinstance(replacement[key], str):
            raise ValidationError(
                'The "{}" key of the dependency replacement must be a string'.format(key)
            )


def validate_dependency_replacements(replacements: List[Dict[str, Any]]) -> None:
    """
    Validate the JSON representation of dependency replacements.

    :param replacement: a list of JSON representation of dependency replacements.
    :type replacement: list[dict[str, any]]
    :raise ValidationError: if the JSON does not match the required schema.
    """
    if not isinstance(replacements, list):
        raise ValidationError('"dependency_replacements" must be an array')
    for replacement in replacements:
        validate_replacement(replacement)


class RequestBodyValidator(decorators.validation.RequestBodyValidator):
    """
    Changes the default Connexion exception to Cachito's ValidationError.

    For more information about custom validation error handling:
        - https://github.com/zalando/connexion/issues/558
        - https://connexion.readthedocs.io/en/latest/request.html
    """

    def validate_schema(self, data, url):
        """Raise cachito.ValidationError."""
        if self.is_null_value_valid and jsonschema.is_null(data):
            return None
        try:
            self.validator.validate(data)
        except jsonschema.ValidationError as exception:
            raise ValidationError(exception.message)

        return None


class ParameterValidator(decorators.validation.ParameterValidator):
    """
    Changes the default Connexion exception to Cachito's ValidationError.

    For more information about custom validation error handling:
        - https://github.com/zalando/connexion/issues/558
        - https://connexion.readthedocs.io/en/latest/request.html
    """

    def __call__(self, function):
        """Throw cachito.ValidationError."""
        wrapper = super().__call__(function)

        def handle_wrapper(request):
            """Handle original wrapper."""
            try:
                return wrapper(request)
            except (BadRequestProblem, ExtraParameterProblem) as exception:
                raise ValidationError(exception.detail)

        return handle_wrapper


@jsonschema.draft4_format_checker.checks("datetime")
def datetime_validator(val: Any) -> bool:
    """Validate that datetime fields have the correct format."""
    if not isinstance(val, str):
        raise ValidationError(
            f"'{val}' is not string type to be evaluated as datetime(ISO 8601 format)."
        )

    try:
        datetime.datetime.fromisoformat(val)
    except ValueError:
        raise ValidationError(f"'{val}' is not a valid datetime(ISO 8601 format).")

    return True
