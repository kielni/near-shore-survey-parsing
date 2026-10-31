"""Service-neutral grid of OCR text, and its conversion to the sheet schema.

Textract, Document AI and Handwriting OCR each convert their response into a
Grid: per page, the header field texts, the table as rows of cell texts, and
the text lines outside the table. grid_to_sheet then applies the conventions
in one place, so the services stay comparable.
"""

import re
from datetime import date
from difflib import get_close_matches

from pydantic import BaseModel

from comparison.config import KNOWN_SITES
from comparison.schema import (
    Cell,
    DepthUnit,
    Header,
    Marginalia,
    Page,
    Row,
    SheetDocument,
    Source,
    tag_color_name,
)

# Factor applied to a cell's confidence when the mapping had to guess, e.g. a
# decimal point inserted or a broken mark read from "x" or "+".
GUESS_FACTOR = 0.5

# Words that may appear in a value cell without making the row free text.
UNIT_WORDS = {"m", "ft", "feet", "min", "mins"}

# A run of two or more letters, used to spot free text in a row.
WORD = re.compile(r"[A-Za-z]{2,}")


class Box(BaseModel):
    """A bounding box in page coordinates, each from 0 to 1."""

    x: float
    y: float
    width: float
    height: float


class Text(BaseModel):
    """Text as read by a service, with its confidence (0-1) and position."""

    text: str = ""
    confidence: float | None = None
    box: Box | None = None


class GridPage(BaseModel):
    """One page: header field texts, the table, and text outside it.

    header is keyed by Header field name ("observers", "site",
    "survey_date", "depth_units"); a field the service didn't find is
    missing. columns is the table's first row; each of rows (8 per page)
    has one Text per column.
    """

    page: int
    header: dict[str, Text] = {}
    columns: list[Text] = []
    rows: list[list[Text]] = []
    table_box: Box | None = None
    lines: list[Text] = []


class Grid(BaseModel):
    """Everything a service read from one sheet."""

    source: Source
    pages: list[GridPage] = []


def scaled(confidence: float | None, factor: float) -> float | None:
    """Return confidence multiplied by factor, keeping None as None."""
    return None if confidence is None else confidence * factor


def parse_number(raw: str) -> tuple[float | None, bool]:
    """Read a number using the sheet conventions.

    Returns the value and whether it was a guess. "–", "-", "Ø" and "0" are
    0, and OCR reads Ø as "Φ" or "O"; "E" is 11, alone or as the integer
    part ("E.3" = 11.3). "E3" is read as 11.3, a guess, since Textract drops
    decimal points after E.
    """
    text = raw.strip().replace(",", ".").replace("..", ".")
    if not text:
        return None, False
    if text in {"-", "–", "—", "Ø", "ø", "Φ", "φ", "O", "o"}:
        return 0.0, False
    if text.upper() == "E":
        return 11.0, False
    # OCR reads 0 as O inside a number ("E.O" for E.0).
    guess = False
    if re.fullmatch(r"[\dEeOo.]+", text) and re.search(r"[Oo]", text):
        text = re.sub(r"[Oo]", "0", text)
        guess = True
    match = re.fullmatch(r"[Ee]\.?(\d)", text)
    if match:
        return 11 + int(match.group(1)) / 10, guess or "." not in text
    try:
        return float(text), guess
    except ValueError:
        return None, False


def parse_count(text: Text) -> Cell[int]:
    """Read a whole-number cell such as rep, #stipes or a tag count."""
    value, guess = parse_number(text.text)
    whole = int(value) if value is not None and value.is_integer() else None
    factor = GUESS_FACTOR if guess else 1.0
    return Cell(value=whole, raw=text.text, confidence=scaled(text.confidence, factor))


def parse_length(text: Text) -> tuple[Cell[float], Cell[bool]]:
    """Read a frond length in meters and whether its stipe is broken.

    "*" anywhere marks a broken stipe. A leading or trailing "x" or "+" is
    probably a misread "*", so it also counts, with lower confidence. A
    length with no decimal point over 31 m lost its decimal point in OCR
    ("150" -> 15.0), so one is inserted before the last digit.
    """
    raw = text.text.strip()
    if not raw:
        return Cell(raw=text.text), Cell(raw=text.text)
    guess = False
    broken = "*" in raw
    number = raw.replace("*", "").strip()
    if re.fullmatch(r"[xX+].+|.+[xX+]", number):
        broken = True
        guess = True
        number = number.strip("xX+ ")
    value, number_guess = parse_number(number)
    guess = guess or number_guess
    if value is not None and "." not in number and value > 31:
        value = float(f"{number[:-1]}.{number[-1]}")
        guess = True
    confidence = scaled(text.confidence, GUESS_FACTOR if guess else 1.0)
    length: Cell[float] = Cell(value=value, raw=text.text, confidence=confidence)
    # With no length read ("N/A"), "not broken" means nothing; a "*" still counts.
    broken_value = broken if value is not None or broken else None
    is_broken: Cell[bool] = Cell(
        value=broken_value, raw=text.text, confidence=confidence
    )
    return length, is_broken


def parse_depth(text: Text) -> tuple[Cell[float], DepthUnit | None]:
    """Read a depth and the unit written with it, if any ("29ft")."""
    raw = text.text.strip()
    unit: DepthUnit | None = None
    if re.search(r"f(ee)?t", raw, re.IGNORECASE):
        unit = "Feet"
    elif re.search(r"\d\s*m$", raw, re.IGNORECASE):
        unit = "Meters"
    number = re.sub(r"[A-Za-z]+$", "", raw).strip()
    value, guess = parse_number(number)
    confidence = scaled(text.confidence, GUESS_FACTOR if guess else 1.0)
    return Cell(value=value, raw=text.text, confidence=confidence), unit


def parse_time(raw: str) -> str | None:
    """Read a 24-hour time ("1045", "945", "12:21") as HH:MM.

    Elapsed times ("27 min") and anything else unreadable give None.
    """
    match = re.fullmatch(r"(\d{1,2}):?(\d{2})", raw.strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def parse_date(raw: str) -> date | None:
    """Read a date written as "260729", "20260729", "8/26/26" or "7/20/2026".

    OCR can add dots or spaces inside a digits-only date ("2610.02").
    """
    text = raw.strip()
    if re.fullmatch(r"[\d.\s]+", text):
        text = re.sub(r"[.\s]", "", text)
    try:
        if re.fullmatch(r"\d{6}", text):
            return date(2000 + int(text[:2]), int(text[2:4]), int(text[4:]))
        if re.fullmatch(r"\d{8}", text):
            return date(int(text[:4]), int(text[4:6]), int(text[6:]))
        match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})", text)
        if match:
            month, day, year = (int(part) for part in match.groups())
            return date(year if year > 99 else 2000 + year, month, day)
    except ValueError:
        pass
    return None


def parse_site(raw: str) -> tuple[str | None, bool]:
    """Read a site code such as SQPT01, KF01S or BR01D.

    Returns the code and whether it was a guess. OCR reads the digits 0 and
    1 as O and I or l, and 2 as Z ("BROID" -> "BR01D"), which are
    corrected; then the code is coerced to the closest known site
    ("SQRT02" -> "SQPT02"). A well-formed code close to no known site is
    kept, since new sites can be added.
    """
    text = re.sub(r"\s", "", raw).upper()
    if not text:
        return None, False
    guess = False
    match = re.fullmatch(r"([A-Z]{2,4}?)([0-9OILZ]{2})([SD]?)", text)
    if match:
        letters, digits, zone = match.groups()
        fixed = digits.translate(str.maketrans("OILZ", "0112"))
        text = f"{letters}{fixed}{zone}"
        guess = fixed != digits
    if text in KNOWN_SITES:
        return text, guess
    # A cutoff of 0.6 accepts about two wrong characters in a 5-6 letter code.
    close = get_close_matches(text, KNOWN_SITES, n=1, cutoff=0.6)
    if close:
        return close[0], True
    return (text, guess) if match else (None, False)


def column_field(header: str) -> str | None:
    """Return the Row field or tag color for a column header.

    Columns are matched by header text, never position: OCR can add an
    empty column or merge handwriting into a header ("feet time"). Any other
    header is a tag color. An empty header gives None.
    """
    name = header.strip().lower().replace(" ", "")
    if not name:
        return None
    frond = re.fullmatch(r"f(\d{1,2})", name)
    if frond:
        return f"F{int(frond.group(1))}"
    for field in ("rep", "depth", "time", "stipes"):
        if field in name:
            return field
    return f"tag:{tag_color_name(header)}"


def convert_header(page: GridPage, source: Source) -> Header:
    """Build a page header, falling back to the filename for site and date.

    The site is sometimes written in the OBS/BUD box, so a site code found
    there is used when the SITE box is blank, and removed from observers.
    """
    found = page.header
    observers_text = found.get("observers", Text())
    tokens = [token for token in re.split(r"[/,;\s]+", observers_text.text) if token]
    site_text = found.get("site", Text())
    site, site_guess = parse_site(site_text.text)
    if site is None:
        for token in tokens:
            token_site, token_guess = parse_site(token)
            # Names in the box can look like a misread code ("Wedell"), so a
            # code found there needs at least one real digit.
            if token_site is not None and re.search(r"\d", token):
                site, site_guess = token_site, token_guess
                site_text = observers_text
                tokens.remove(token)
                break
    if site_text.text.strip():
        factor = GUESS_FACTOR if site_guess else 1.0
        site_cell: Cell[str] = Cell(
            value=site,
            raw=site_text.text,
            confidence=scaled(site_text.confidence, factor),
        )
    else:
        site_cell = Cell(value=source.filename_site)
    date_text = found.get("survey_date", Text())
    if date_text.text.strip():
        date_cell: Cell[date] = Cell(
            value=parse_date(date_text.text),
            raw=date_text.text,
            confidence=date_text.confidence,
        )
    else:
        date_cell = Cell(value=source.filename_date)
    units_text = found.get("depth_units", Text())
    units = units_text.text.strip().lower()
    unit: DepthUnit | None = None
    if units in {"meters", "m"}:
        unit = "Meters"
    elif units in {"feet", "ft"}:
        unit = "Feet"
    observers: Cell[list[str]] = Cell(
        value=tokens or None,
        raw=observers_text.text or None,
        confidence=observers_text.confidence,
    )
    tag_colors = [
        field.removeprefix("tag:")
        for column in page.columns
        if (field := column_field(column.text)) and field.startswith("tag:")
    ]
    return Header(
        observers=observers,
        site=site_cell,
        survey_date=date_cell,
        depth_units=Cell(
            value=unit, raw=units_text.text or None, confidence=units_text.confidence
        ),
        tag_colors=tag_colors,
    )


def is_free_text(text: str) -> bool:
    """Return True if a cell holds words rather than a value ("DEAD")."""
    return any(word.lower() not in UNIT_WORDS for word in WORD.findall(text))


def convert_row(
    sheet_row: int, columns: list[Text], cells: list[Text], header: Header
) -> Row:
    """Build one table row, applying the conventions to each cell.

    Cells holding words go to row_text instead of being read as values, so
    values written before the text are still read.
    """
    row = Row(sheet_row=sheet_row)
    words: list[Text] = []
    depth_unit: DepthUnit | None = None
    for column, cell in zip(columns, cells):
        field = column_field(column.text)
        if field is None and not cell.text.strip():
            continue
        if is_free_text(cell.text):
            words.append(cell)
            continue
        if field is None:
            field = "tag:unlabeled"
        if field == "rep":
            row.rep = parse_count(cell)
        elif field == "depth":
            row.depth, depth_unit = parse_depth(cell)
        elif field == "time":
            row.time = Cell(
                value=parse_time(cell.text), raw=cell.text, confidence=cell.confidence
            )
        elif field == "stipes":
            row.stipes = parse_count(cell)
        elif field.startswith("tag:"):
            row.tags[field.removeprefix("tag:")] = parse_count(cell)
        else:
            length, broken = parse_length(cell)
            setattr(row, field, length)
            setattr(row, f"{field}_broken", broken)
    if words:
        confidences = [w.confidence for w in words if w.confidence is not None]
        row.row_text = Cell(
            value=" ".join(w.text.strip() for w in words),
            raw=" ".join(w.text for w in words),
            confidence=min(confidences) if confidences else None,
        )
    # A unit written in the row wins; otherwise the unit circled on the sheet.
    if depth_unit is not None:
        row.depth_unit = Cell(
            value=depth_unit, raw=row.depth.raw, confidence=row.depth.confidence
        )
    elif row.depth.value is not None:
        row.depth_unit = header.depth_units
    return row


def location(line: Text, table: Box | None) -> str:
    """Describe where a line sits relative to the table."""
    if line.box is None or table is None:
        return "outside table"
    if line.box.y + line.box.height <= table.y:
        return "above table"
    if line.box.y >= table.y + table.height:
        return "below table"
    return "left of table" if line.box.x < table.x else "right of table"


def convert_marginalia(page: GridPage) -> list[Marginalia]:
    """Keep the text found outside the table, noting any rep it names."""
    marginalia: list[Marginalia] = []
    for line in page.lines:
        if not line.text.strip():
            continue
        rep = re.search(r"(?:rep|plant)\s*#?\s*(\d+)", line.text, re.IGNORECASE)
        marginalia.append(
            Marginalia(
                location=f"page {page.page}, {location(line, page.table_box)}",
                text=line.text,
                rep=int(rep.group(1)) if rep else None,
            )
        )
    return marginalia


def grid_to_sheet(grid: Grid) -> SheetDocument:
    """Convert a service's grid into the sheet schema."""
    document = SheetDocument(source=grid.source)
    for grid_page in grid.pages:
        header = convert_header(grid_page, grid.source)
        rows = [
            convert_row(number, grid_page.columns, cells, header)
            for number, cells in enumerate(grid_page.rows, start=1)
        ]
        document.pages.append(Page(page=grid_page.page, header=header, rows=rows))
        document.marginalia.extend(convert_marginalia(grid_page))
    return document
