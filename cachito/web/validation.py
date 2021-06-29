# SPDX-License-Identifier: GPL-3.0-or-later

from typing import Any, Dict, List

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
