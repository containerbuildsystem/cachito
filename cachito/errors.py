# SPDX-License-Identifier: GPL-3.0-or-later


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
