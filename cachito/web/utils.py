# SPDX-License-Identifier: GPL-3.0-or-later
from collections import OrderedDict

from flask import request, url_for


def deep_sort_icm(orig_item):
    """
    Return a new element recursively sorted in ascending order.

    The function for sorting image content manifests

    :param orig_item: Original content manifest to be sorted
    :return: Recursively sorted dict or list according to orig_item
    :rtype: Any
    """
    if isinstance(orig_item, list):
        sorted_item = [deep_sort_icm(item) for item in orig_item]
        if len(sorted_item) and isinstance(sorted_item[0], OrderedDict):
            sorted_item = sorted(sorted_item, key=lambda ordered_dict: list(ordered_dict.items()))
    elif isinstance(orig_item, dict):
        sorted_item = OrderedDict(
            sorted({k: deep_sort_icm(v) for k, v in orig_item.items()}.items())
        )
    else:
        sorted_item = orig_item
    return sorted_item


def pagination_metadata(pagination_query, **kwargs):
    """
    Return a dictionary containing metadata about the paginated query.

    This must be run as part of a Flask request.

    :param pagination_query: flask_sqlalchemy.Pagination object
    :param dict kwargs: the query parameters to add to the URLs
    :return: a dictionary containing metadata about the paginated query
    """
    pagination_data = {
        "first": url_for(
            request.endpoint, page=1, per_page=pagination_query.per_page, _external=True, **kwargs
        ),
        "last": url_for(
            request.endpoint,
            page=pagination_query.pages,
            per_page=pagination_query.per_page,
            _external=True,
            **kwargs,
        ),
        "next": None,
        "page": pagination_query.page,
        "pages": pagination_query.pages,
        "per_page": pagination_query.per_page,
        "previous": None,
        "total": pagination_query.total,
    }

    if pagination_query.has_prev:
        pagination_data["previous"] = url_for(
            request.endpoint,
            page=pagination_query.prev_num,
            per_page=pagination_query.per_page,
            _external=True,
            **kwargs,
        )
    if pagination_query.has_next:
        pagination_data["next"] = url_for(
            request.endpoint,
            page=pagination_query.next_num,
            per_page=pagination_query.per_page,
            _external=True,
            **kwargs,
        )

    return pagination_data


def str_to_bool(item):
    """
    Convert a string to a boolean.

    :param str item: string to parse
    :return: a boolean equivalent
    :rtype: boolean
    """
    if isinstance(item, str):
        return item.lower() in ("true", "1")
    else:
        return False
