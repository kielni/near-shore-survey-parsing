"""Output model for one page of a frond sheet, as the LLM transcribes it.

Every value is a string as written on the sheet ("E.3", "2.5*", "-", "29ft"),
empty if blank; comparison/grid.py applies the conventions, as for the OCR
services. What each field holds is explained in prompt.txt.

Strict structured output needs every field required and no extra fields,
hence no defaults and extra="forbid".
"""

from pydantic import BaseModel, ConfigDict


class Row(BaseModel):
    """One plant row of the table."""

    model_config = ConfigDict(extra="forbid")

    rep: str
    depth: str
    time: str
    stipes: str
    tags: list[str]
    frond_lengths: list[str]
    row_text: str
    uncertain: list[str]


class Page(BaseModel):
    """One page of a frond sheet."""

    model_config = ConfigDict(extra="forbid")

    obsbud: str
    site: str
    date: str
    depth_units: str
    tag_columns: list[str]
    rows: list[Row]
    notes: list[str]
