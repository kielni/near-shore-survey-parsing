"""Logging setup shared by every comparison script."""

import logging


def configure_logging() -> None:
    """Send INFO and above to stderr, prefixed with a timestamp and the logger name."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
