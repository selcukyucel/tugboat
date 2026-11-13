"""Errors and exit codes shared by every command."""

from __future__ import annotations

EXIT_OK = 0
EXIT_ERROR = 1
# Exit code 2 is what agent tools read as "deny" from a hook. A usage error
# also exits with 2, so a hook that is called wrongly denies instead of allowing.
EXIT_DENY = 2
EXIT_NOT_BUILT = 3


class TugError(Exception):
    """An error with a message that is safe to show to the developer."""

    exit_code = EXIT_ERROR


class DataError(TugError, ValueError):
    """Raised when data read from a file or another program is not acceptable."""
