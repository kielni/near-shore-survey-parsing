"""Settings shared by every comparison script: logging, known sites, paths."""

import logging
from pathlib import Path

# Every site in data/fronds_answers.tsv. Site codes read from a sheet are
# coerced to the closest of these.
KNOWN_SITES = [
    "BR01D",
    "KF01DC",
    "KF01S",
    "KF02D",
    "KF02S",
    "KF03S",
    "NABR01",
    "NABR02",
    "SQPT01",
    "SQPT02",
]


def configure_logging() -> None:
    """Send INFO and above to stderr, prefixed with a timestamp and the logger name."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    # Textractor logs a line at INFO for every empty table cell.
    logging.getLogger("textractor").setLevel(logging.WARNING)


def mapped_paths(results_dir: Path) -> list[Path]:
    """Return the mapped <file>.json of every <file>.raw.json in a folder.

    Other JSON in the folder, such as scores.json, is left out.
    """
    raws = sorted(results_dir.glob("*.raw.json"))
    return [raw.with_name(raw.name.removesuffix(".raw.json") + ".json") for raw in raws]
