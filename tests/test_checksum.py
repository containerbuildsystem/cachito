# SPDX-License-Identifier: GPL-3.0-or-later

import hashlib
import pytest

from cachito.common.checksum import compute_file_checksum, get_hasher
from cachito.errors import UnknownHashAlgorithm


@pytest.mark.parametrize(
    "name,expected_error",
    [
        ["sha256", None],
        ["sha512", None],
        ["md5", None],
        ["xxx", pytest.raises(UnknownHashAlgorithm)],
    ],
)
def test_get_hasher(name, expected_error):
    if expected_error is None:
        hasher = get_hasher(name)
        assert hasattr(hasher, "hexdigest")
    else:
        with expected_error:
            get_hasher(name)


@pytest.mark.parametrize("file_content", ["", "abc123" * 100])
def test_compute_file_checksum(file_content, tmpdir):
    data_file = tmpdir.join("file.data")
    data_file.write(file_content)
    expected_checksum = hashlib.sha256(file_content.encode()).hexdigest()
    hasher = get_hasher("sha256")
    assert expected_checksum == compute_file_checksum(hasher, str(data_file))
