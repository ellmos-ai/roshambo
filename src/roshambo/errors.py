"""Exception hierarchy for Roshambo.

Note what is *not* here: a "resource already claimed" exception. A denied claim is a
normal, informative result (`ClaimDenied`), not an error — it tells the caller who is
working on the resource and what they intend to do with it.
"""

from __future__ import annotations


class RoshamboError(Exception):
    """Base class for every error raised by Roshambo."""


class ConfigError(RoshamboError):
    """Configuration is missing or malformed."""


class SchemaError(RoshamboError):
    """The schema file could not be located, read, or applied."""


class EmbeddingError(RoshamboError):
    """An embedding could not be produced, or has the wrong dimensionality."""


class StorageError(RoshamboError):
    """A database operation failed in a way the caller cannot be expected to retry."""
