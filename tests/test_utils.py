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
                            {"purl": "sample-URL"},
                            {"apurl": "sample-URL"},
                            {"bpurl": "asample-URL"},
                            {"bpurl": "0sample-URL"},
                            {"apurl": "sample-URL"},
                            {"purl": "asample-URL"},
                        ],
                        "purl": "sample-URL",
                        "sources": [],
                    },
                    {
                        "purl": "sample-URL",
                        "sources": [{"purl": "sample-URL"}, {"apurl": "sample-URL"}],
                        "adependencies": [],
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
                            "adependencies": [],
                            "purl": "sample-URL",
                            "sources": [
                                OrderedDict({"apurl": "sample-URL"}),
                                OrderedDict({"purl": "sample-URL"}),
                            ],
                        }
                    ),
                    OrderedDict(
                        {
                            "dependencies": [
                                OrderedDict({"apurl": "sample-URL"}),
                                OrderedDict({"apurl": "sample-URL"}),
                                OrderedDict({"bpurl": "0sample-URL"}),
                                OrderedDict({"bpurl": "asample-URL"}),
                                OrderedDict({"purl": "asample-URL"}),
                                OrderedDict({"purl": "sample-URL"}),
                            ],
                            "purl": "sample-URL",
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
