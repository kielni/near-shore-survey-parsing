"""Settings shared by every comparison script: logging, known sites, paths."""

import logging
from pathlib import Path

log = logging.getLogger("results")

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

    Other JSON in the folder, such as scores.json, is left out, and so is a
    raw.json that hasn't been mapped yet (logged, so it isn't missed).
    """
    mapped: list[Path] = []
    for raw in sorted(results_dir.glob("*.raw.json")):
        path = raw.with_name(raw.name.removesuffix(".raw.json") + ".json")
        if path.exists():
            mapped.append(path)
        else:
            log.warning(f"{raw}: not mapped, skipped (run make map)")
    return mapped


def results_dirs(service: str) -> list[Path]:
    """Return the folders holding a service's results.

    A service keeps its results in data/results/<service>/, or in one
    subfolder per variant (data/results/llm_api/<model>/); every folder with
    a raw.json is returned.
    """
    root = Path("data/results", service)
    folders = [root, *sorted(p for p in root.iterdir() if p.is_dir())]
    return [folder for folder in folders if any(folder.glob("*.raw.json"))]
