# SPDX-License-Identifier: GPL-3.0-or-later


class ValidationError(ValueError):
    """An error was encountered during validation."""


class CachitoError(RuntimeError):
    """An error was encountered in Cachito."""


class ConfigError(CachitoError):
    """An error was encountered during configuration validation."""
