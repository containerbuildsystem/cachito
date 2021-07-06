# SPDX-License-Identifier: GPL-3.0-or-later

import hashlib
from cachito.errors import UnknownHashAlgorithm


def get_hasher(algorithm: str = "sha256"):
    """Get a hasher by a specific algorithm.

    :param str algorithm: the algorithm name used to get a hasher from the hashlib module.
    :return: the hasher got from the hashlib module.
    :raise UnknownHashAlgorithm: if the algorithm cannot be found.
    """
    try:
        return hashlib.new(algorithm)
    except ValueError:
        raise UnknownHashAlgorithm(f"Hash algorithm {algorithm} is unknown.")


def compute_file_checksum(hasher, file_path: str, chunk_size: int = 10240) -> str:
    """Compute checksum of a file.

    :param hasher: a hasher got from the hashlib module. Use :func:`get_hasher` to get a hasher,
        or just call hashlib method to get one, e.g. ``hashlib.sha256``.
    :param str file_path: compute checksum for this file.
    :param chunk_size: the optional chunk size passed to fileobject.read method.
    :type chunk_size: int or None
    :return: the computed checksum.
    :rtype: str
    """
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()
