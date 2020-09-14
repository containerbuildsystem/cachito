# SPDX-License-Identifier: GPL-3.0-or-later
from collections import OrderedDict

import pytest

from cachito.web.utils import deep_sort_icm


@pytest.mark.parametrize(
    "orig_items",
    [
        [
            {
                "metadata": {"image_layer_index": -1, "icm_spec": "sample-URL", "icm_version": 1},
                "image_contents": [
                    {
                        "dependencies": [
                            {"purl": "5sample-URL"},
                            {"purl": "4sample-URL"},
                            {"purl": "3sample-URL"},
                            {"purl": "2sample-URL"},
                            {"purl": "1sample-URL"},
                            {"purl": "0sample-URL"},
                        ],
                        "purl": "1sample-URL",
                        "sources": [],
                    },
                    {
                        "dependencies": [],
                        "purl": "0sample-URL",
                        "sources": [{"purl": "1sample-URL"}, {"purl": "0sample-URL"}],
                    },
                ],
            }
        ],
    ],
)
def test_deep_sort_icm(orig_items):
    expected = [
        OrderedDict(
            {
                "image_contents": [
                    OrderedDict(
                        {
                            "dependencies": [],
                            "purl": "0sample-URL",
                            "sources": [
                                OrderedDict({"purl": "0sample-URL"}),
                                OrderedDict({"purl": "1sample-URL"}),
                            ],
                        }
                    ),
                    OrderedDict(
                        {
                            "dependencies": [
                                OrderedDict({"purl": "0sample-URL"}),
                                OrderedDict({"purl": "1sample-URL"}),
                                OrderedDict({"purl": "2sample-URL"}),
                                OrderedDict({"purl": "3sample-URL"}),
                                OrderedDict({"purl": "4sample-URL"}),
                                OrderedDict({"purl": "5sample-URL"}),
                            ],
                            "purl": "1sample-URL",
                            "sources": [],
                        }
                    ),
                ],
                "metadata": OrderedDict(
                    {"icm_spec": "sample-URL", "icm_version": 1, "image_layer_index": -1}
                ),
            }
        ),
    ]
    assert deep_sort_icm(orig_items) == expected
