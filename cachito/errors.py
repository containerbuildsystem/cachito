# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess  # nosec
from enum import Enum

import kombu.exceptions
import requests
import sqlalchemy.exc


class RequestErrorOrigin(Enum):
    """An Enum that represents the request error origin."""

    client = 1
    server = 2


class CachitoError(RuntimeError):
    """An error was encountered in Cachito."""


class ValidationError(CachitoError, ValueError):
    """An error was encountered during validation."""


class ConfigError(CachitoError):
    """An error was encountered during configuration validation."""


class ContentManifestError(CachitoError, ValueError):
    """An error was encountered during content manifest generation."""


class CachitoNotImplementedError(CachitoError, ValueError):
    """An error was encountered during request validation."""


class UnknownHashAlgorithm(CachitoError):
    """The hash algorithm is unknown by Cachito."""


# Request error classifiers
class ClientError(Exception):
    """Client Error."""

    origin = RequestErrorOrigin.client


class ServerError(Exception):
    """Server Error."""

    origin = RequestErrorOrigin.server


# Web errors
class InvalidQueryParameters(ClientError):
    """Invalid query parameters."""

    pass


class InvalidRequestData(ClientError):
    """Invalid request data."""

    pass


# Repository errors
class InvalidRepoStructure(ClientError):
    """Invalid repository structure. The provided repository has a missing file or directory."""

    pass


class InvalidFileFormat(ClientError):
    """Invalid file format."""

    pass


class InvalidChecksum(ClientError):
    """Checksum verification failed."""

    pass


class UnsupportedFeature(ClientError):
    """Unsupported feature."""

    pass


# Deployment errors
class WebConfigError(ServerError):
    """Invalid API configuration."""

    pass


class WorkerConfigError(ServerError):
    """Invalid worker configuration."""

    pass


class NexusConfigError(ServerError):
    """Invalid Nexus configuration."""

    pass


class NoWorkers(ServerError):
    """No available workers found."""

    pass


# Low-level errors
class FileAccessError(ServerError):
    """File not found."""

    pass


class FilePermissionError(ServerError):
    """No permissions to open file."""

    pass


class SubprocessCallError(ServerError):
    """Error calling subprocess."""

    pass


class NetworkError(ServerError):
    """Network connection error."""

    pass


class DatabaseError(ServerError):
    """DB connection error."""

    pass


class MessageBrokerError(ServerError):
    """Message broker connection error."""

    pass


# Third-party service errors
class RepositoryAccessError(ServerError):
    """Repository is not accessible and can't be cloned."""

    pass


class GoModError(ServerError):
    """Go mod related error. A module can't be downloaded by go mod download command."""

    pass


class NexusError(ServerError):
    """Nexus related error."""

    pass


# Recommended: map unexpected errors to some documented error type
ERROR_MAPPING = {
    FileNotFoundError: FileAccessError,
    FileExistsError: FileAccessError,
    IsADirectoryError: FileAccessError,
    PermissionError: FilePermissionError,
    subprocess.CalledProcessError: SubprocessCallError,
    subprocess.TimeoutExpired: SubprocessCallError,
    requests.RequestException: NetworkError,
    sqlalchemy.exc.SQLAlchemyError: DatabaseError,
    kombu.exceptions.KombuError: MessageBrokerError,
}
