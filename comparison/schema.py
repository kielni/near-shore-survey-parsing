"""Pydantic models for one survey sheet, the shape every service is mapped into.

Every value read from the sheet is a Cell, holding the normalized value, the
text as read, and the service's confidence. Range checks are left to the
validation rules, since structured outputs don't support numeric constraints.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, computed_field

DepthUnit = Literal["Meters", "Feet"]

METERS_PER_FOOT = 0.3048

# Spellings of the same tag color, mapped to one name. A color missing from
# here still works, under its own spelling.
TAG_COLOR_ALIASES = {
    "green": "grn",
    "yellow": "ylw",
    "white": "wht",
    "black": "blk",
    "bk": "blk",
}


def tag_color_name(header: str) -> str:
    """Return the normalized color name for a tag column header.

    The header is lowercased, with `#` and spaces removed, then merged
    through TAG_COLOR_ALIASES (`#Blk` -> `blk`, `Black` -> `blk`).
    """
    name = header.lower().replace("#", "").replace(" ", "")
    return TAG_COLOR_ALIASES.get(name, name)


class Cell[T](BaseModel):
    """One value read from the sheet."""

    value: T | None = None
    raw: str | None = None
    confidence: float | None = None


class Source(BaseModel):
    """Where a sheet document came from."""

    file: str
    form: str
    form_version: str
    filename_date: date
    filename_site: str
    service: str

    @classmethod
    def from_filename(cls, file: str, service: str) -> Source:
        """Build a Source from a name like 20261016_SQPT01_FrondPull_1.8.pdf."""
        stamp, site, form, version = file.removesuffix(".pdf").split("_")
        return cls(
            file=file,
            form=form.lower(),
            form_version=version,
            filename_date=date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:])),
            filename_site=site,
            service=service,
        )


class Header(BaseModel):
    """The header of one page.

    A date or site filled in from the filename keeps raw None, so it is
    visible that the page left it blank.
    """

    observers: Cell[list[str]] = Field(default_factory=Cell)
    site: Cell[str] = Field(default_factory=Cell)
    survey_date: Cell[date] = Field(default_factory=Cell)
    depth_units: Cell[DepthUnit] = Field(default_factory=Cell)
    # Tag color columns in the order they appear on this page.
    tag_colors: list[str] = []


class Row(BaseModel):
    """One plant row of a page's table; lengths are in meters.

    Answers carry no position, so they have sheet_row None and entry_order
    instead: the plant's place in the order it was entered.
    """

    sheet_row: int | None = None
    entry_order: int | None = None
    rep: Cell[int] = Field(default_factory=Cell)
    depth: Cell[float] = Field(default_factory=Cell)
    depth_unit: Cell[DepthUnit] = Field(default_factory=Cell)
    # HH:MM; elapsed times such as "27 min" keep raw with value None.
    time: Cell[str] = Field(default_factory=Cell)
    stipes: Cell[int] = Field(default_factory=Cell)
    # Tag color -> count, one entry per color in the page's tag_colors.
    tags: dict[str, Cell[int]] = {}
    F1: Cell[float] = Field(default_factory=Cell)
    F2: Cell[float] = Field(default_factory=Cell)
    F3: Cell[float] = Field(default_factory=Cell)
    F4: Cell[float] = Field(default_factory=Cell)
    F5: Cell[float] = Field(default_factory=Cell)
    F6: Cell[float] = Field(default_factory=Cell)
    F7: Cell[float] = Field(default_factory=Cell)
    F8: Cell[float] = Field(default_factory=Cell)
    F9: Cell[float] = Field(default_factory=Cell)
    F10: Cell[float] = Field(default_factory=Cell)
    F11: Cell[float] = Field(default_factory=Cell)
    F12: Cell[float] = Field(default_factory=Cell)
    F1_broken: Cell[bool] = Field(default_factory=Cell)
    F2_broken: Cell[bool] = Field(default_factory=Cell)
    F3_broken: Cell[bool] = Field(default_factory=Cell)
    F4_broken: Cell[bool] = Field(default_factory=Cell)
    F5_broken: Cell[bool] = Field(default_factory=Cell)
    F6_broken: Cell[bool] = Field(default_factory=Cell)
    F7_broken: Cell[bool] = Field(default_factory=Cell)
    F8_broken: Cell[bool] = Field(default_factory=Cell)
    F9_broken: Cell[bool] = Field(default_factory=Cell)
    F10_broken: Cell[bool] = Field(default_factory=Cell)
    F11_broken: Cell[bool] = Field(default_factory=Cell)
    F12_broken: Cell[bool] = Field(default_factory=Cell)
    # Free text written instead of values, e.g. "DEAD confirmed".
    row_text: Cell[str] = Field(default_factory=Cell)
    # Answers only: the enterer's notes, kept for reference, not scored.
    answer_notes: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def depth_m(self) -> float | None:
        """Depth in meters, or None if the depth or its unit is unknown."""
        depth = self.depth.value
        unit = self.depth_unit.value
        if depth is None or unit is None:
            return None
        return depth * METERS_PER_FOOT if unit == "Feet" else depth


class Page(BaseModel):
    """One PDF page, each a separate table with its own header."""

    page: int
    header: Header = Field(default_factory=Header)
    rows: list[Row] = []


class Marginalia(BaseModel):
    """Free text found outside the table, captured for review, not parsed."""

    location: str
    text: str
    rep: int | None = None


class Flag(BaseModel):
    """A validation rule that fired, for review; values are never changed.

    sheet_row None means the whole page; field None means the whole row.
    """

    rule: str
    page: int
    sheet_row: int | None = None
    field: str | None = None
    message: str


class SheetDocument(BaseModel):
    """Everything read from one survey sheet (one PDF)."""

    source: Source
    pages: list[Page] = []
    marginalia: list[Marginalia] = []
    flags: list[Flag] = []
