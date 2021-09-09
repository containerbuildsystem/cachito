# SPDX-License-Identifier: GPL-3.0-or-later

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
            },
        ],
    ],
)
def test_deep_sort_icm(orig_items):
    expected = [
        {
            "image_contents": [
                {
                    "dependencies": [],
                    "purl": "0sample-URL",
                    "sources": [{"purl": "0sample-URL"}, {"purl": "1sample-URL"}],
                },
                {
                    "dependencies": [
                        {"purl": "0sample-URL"},
                        {"purl": "1sample-URL"},
                        {"purl": "2sample-URL"},
                        {"purl": "3sample-URL"},
                        {"purl": "4sample-URL"},
                        {"purl": "5sample-URL"},
                    ],
                    "purl": "1sample-URL",
                    "sources": [],
                },
            ],
            "metadata": {"icm_spec": "sample-URL", "icm_version": 1, "image_layer_index": -1},
        }
    ]
    deep_sort_icm(orig_items)
    assert orig_items == expected


@pytest.mark.parametrize(
    "error_icm",
    [
        "image content manifest",
        {
            "image_contents": [
                {
                    "dependencies": [],
                    "purl": "0sample-URL",
                    "sources": [("purl", "0sample-URL"), ("purl", "1sample-URL")],
                },
            ],
            "metadata": {"icm_spec": "sample-URL", "icm_version": 1, "image_layer_index": -1},
        },
    ],
)
def test_deep_sort_icm_raises_error_when_unknown_type_included(error_icm):
    with pytest.raises(TypeError, match="Unknown type is included in the content manifest"):
        deep_sort_icm(error_icm)
