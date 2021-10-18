# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import date, datetime, time
from operator import itemgetter
from typing import Union

from flask import request, url_for

CONTAINER_TYPES = (dict, list)
SORT_KEY_BY_PURL = itemgetter("purl")


def deep_sort_icm(orig_item):
    """
    Return a new element recursively sorted in ascending order.

    The function for sorting image content manifests

    All lists of dicts with a "purl" key will be sorted alphabetically by the
    "purl" value. Any other objects will be left as is.

    :param orig_item: Original content manifest to be sorted
    :return: Recursively sorted dict or list according to orig_item
    :rtype: Any
    """
    if orig_item and isinstance(orig_item, dict):
        keys = orig_item.keys()
        for key in keys:
            val = orig_item[key]
            if val and isinstance(val, CONTAINER_TYPES):
                deep_sort_icm(val)
    elif orig_item and isinstance(orig_item, list):
        for item in orig_item:
            deep_sort_icm(item)
        # If item is a list of dicts with the "purl" key, sort by the "purl" value
        if isinstance(orig_item[0], dict) and "purl" in orig_item[0]:
            orig_item.sort(key=SORT_KEY_BY_PURL)
    else:
        raise TypeError("Unknown type is included in the content manifest.")


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


def normalize_end_date(value: Union[datetime, date, None]):
    """
    Convert date value to the end of the day datetime.

    The function doesn't touch values of any other input types.
    Example:
        Input value: date(2021, 10, 21)
        Output value: datetime(2021, 10, 21, 23, 59, 59, 999999)
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time.max)
    return value
