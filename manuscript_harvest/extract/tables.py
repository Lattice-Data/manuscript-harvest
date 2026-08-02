"""Turning a spreadsheet into something a model can actually read.

A supplementary table is not prose and must not be treated as prose. Three facts
from this corpus set the design:

- `41591_2018_269_MOESM1_ESM.xlsx` puts a title on row 1, a caption on row 2, a
  blank row on row 3, and the real header on **row 4**. Anything that assumes
  row 1 is the header reads the caption as column names and the column names as
  data.
- One sheet in that same file is 16,596 rows by 88 columns. It cannot go into a
  prompt, and almost none of it would help if it did.
- What answers the curation questions is the *column*, not the row. A column
  whose two distinct values are `{M, F}` answers sex. A column of
  `{10x Genomics Chromium v3, Smart-seq2}` answers library kit. Enumerating a
  low-cardinality column is both tiny and decisive.

So each table becomes a **card**: caption, detected header, one profile line per
column (with the full value set when the cardinality is low), and a few sample
rows. Typically a few hundred characters instead of a million cells.

The card does not copy the data. It records `data_ref` -- file, sheet, header row
-- so code that wants the real values re-reads the original file at the exact
offset the card was built from. Duplicating a 2.4 GB corpus to paraphrase it
would be the wrong trade.
"""

import datetime as _datetime
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .limits import Limits

NUMBER = "number"
DATE = "date"
BOOLEAN = "boolean"
TEXT = "text"
MIXED = "mixed"
EMPTY = "empty"

_TRUE_FALSE = {"true", "false", "yes", "no", "y", "n"}
_NUMERIC_RX = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_PERCENT_RX = re.compile(r"^[+-]?\d+(?:\.\d+)?\s*%$")


@dataclass
class TableCard:
    """One table, summarised. `text` is produced by `render`, not stored here."""

    source_file: str
    locator: str
    title: Optional[str] = None
    caption: Optional[str] = None
    header: List[str] = field(default_factory=list)
    header_row: Optional[int] = None
    header_rows: Optional[List[int]] = None
    """Both rows, 0-based, when the header spans two: a group label row above a
    sub-header row. `header_row` stays the sub-header, which is the one the data
    starts under."""
    header_confidence: str = "low"
    n_columns: int = 0
    n_rows: int = 0
    """Data rows actually scanned."""
    n_rows_total: Optional[int] = None
    """Rows the source claims to have, when it says; None when unknown."""
    truncated: bool = False
    columns: List[dict] = field(default_factory=list)
    sample_rows: List[List[str]] = field(default_factory=list)
    columns_dropped: int = 0
    data_ref: dict = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# -- cell handling -----------------------------------------------------------

def clean_cell(value: Any) -> Optional[str]:
    """One cell as a trimmed string, or None when it holds nothing.

    Excel's empty-looking cells are not all None: formulas evaluated to `""` and
    whitespace-only strings are common, and counting them as present is what
    makes a caption row look like a header row.
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()
        return text or None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (_datetime.datetime, _datetime.date)):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip() or None


def _row_cells(row: Sequence[Any], width: int) -> List[Optional[str]]:
    cells = [clean_cell(v) for v in row][:width]
    cells.extend([None] * (width - len(cells)))
    return cells


def _value_kind(text: str) -> str:
    lowered = text.lower()
    if lowered in _TRUE_FALSE:
        return BOOLEAN
    if _NUMERIC_RX.match(text) or _PERCENT_RX.match(text):
        return NUMBER
    if re.match(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2})?", text):
        return DATE
    return TEXT


def _as_number(text: str) -> Optional[float]:
    """The cell as a float, or None -- including for Inf and NaN.

    `float("inf")` succeeds, so a column holding `Inf` put `"max": Infinity` into
    `blocks.jsonl`: line 520 of 10.1038/s41467-023-40505-5, sheet
    `Supplementary Data 3`, column `neg. log10-pval`. Python's `json.loads`
    accepts that by default; `serde_json`, Go's `encoding/json`, PostgreSQL
    `jsonb` and DuckDB all reject the line, so the artifact was not JSON.
    """
    try:
        value = float(text.rstrip("% "))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


#: Spellings `float()` accepts and JSON does not. Counted so a range that leaves
#: them out can say it did.
_NON_FINITE_RX = re.compile(r"^[+-]?(?:inf(?:inity)?|nan)$", re.IGNORECASE)


def _looks_non_finite(text: str) -> bool:
    return bool(_NON_FINITE_RX.match(text.rstrip("% ").strip()))


# -- splitting a sheet that holds more than one table ------------------------

def split_blocks(rows: List[Sequence[Any]], limits: Limits) -> List[Tuple[int, int]]:
    """Blank-row-separated parts of a sheet, as `(start, end_exclusive)`.

    Returns `[]` when the sheet is one table, so the caller keeps its single-card
    path. 16 of the 56 xlsx sheets in this corpus hold two or more such groups.
    `01_aba4163_data_file_s1.xlsx` sheet `Figure 6` holds ten stacked panels;
    `detect_header` found the first one's header on row 2 and `build_card` read
    every later panel's title, units and header row as data, giving
    `Figure 6C [text, 23 distinct, 63 empty] = % Crescents | [% of CD3+] | ... |
    Figure 6D | Figure 6E | ...` at `header_confidence: high` with no note.

    The guard is what makes it safe, and "every part must be at least 2 rows"
    is the wrong guard -- it disables the split on `STable 4.4` (parts
    `[1, 13, 13, 13]`, a title row above three stacked tables) and on
    `Figure S7` (`[9, 1, 9, 10]`). Instead a 1-row part is merged into the part
    below it, because a lone row above a table is a panel title and
    `detect_header` already consumes it as a caption line; a trailing 1-row part
    is dropped; and the split happens only if two parts remain. Verified against
    every xlsx sheet here: it fires on 12 sheets and correctly collapses
    `STable 4.1` `[1, 13]`, `STable 4.2` `[1, 3086]` and `STable 4.5` `[1, 3086]`
    back to a single card.
    """
    parts: List[Tuple[int, int]] = []
    start: Optional[int] = None
    for index, row in enumerate(rows):
        if all(clean_cell(value) is None for value in row):
            if start is not None:
                parts.append((start, index))
                start = None
        elif start is None:
            start = index
    if start is not None:
        parts.append((start, len(rows)))

    merged: List[Tuple[int, int]] = []
    for part in parts:
        if merged and merged[-1][1] - merged[-1][0] == 1:
            merged[-1] = (merged[-1][0], part[1])
        else:
            merged.append(part)
    while merged and merged[-1][1] - merged[-1][0] == 1:
        merged.pop()
    return merged if len(merged) >= 2 else []


# -- header detection --------------------------------------------------------

def repeating_header(present: List[str]) -> Optional[Tuple[int, int]]:
    """`(offset, repeats)` when a row is a block of distinct names repeated.

    Two shapes in this corpus, both of which the 0.7-distinct rule rejected --
    leaving the card headerless and then printing the header strings themselves
    as data, in the authoritative `=` form:

    - `08_mmc5.xlsx` sheet `TV+vs.V-` is two identical eight-column tables side
      by side: 16 present cells, 8 distinct, threshold 11. The card fell back to
      "no header row identified; columns are positional" and then printed
      `7. column_7 [text, 3 distinct] = cluster | virus+ | virus-`.
    - `49_..._MOESM4_ESM.xlsx` sheet `Supplementary Data 5` is `Locus, Location`
      followed by `Sum.PIPs, N.SNPs` once per cell type: 18 cells, 4 distinct.
      Hence the small leading offset -- index columns come before the repeat.

    The smallest period wins, so the count reported is the real number of groups,
    and the block's names must be distinct, so a row of eighteen `n/a` does not
    qualify as a header.
    """
    for offset in (0, 1, 2):
        tail = present[offset:]
        if len(tail) < 4:
            continue
        for period in range(2, len(tail) // 2 + 1):
            if len(tail) % period:
                continue
            block = tail[:period]
            if len(set(block)) == period and tail == block * (len(tail) // period):
                return offset, len(tail) // period
    return None


def _looks_like_labels(cells: List[Optional[str]], width: int = 2) -> bool:
    """True when a row reads like column names rather than data.

    `width` is how wide the table is. A single-column table has a single-cell
    header, so requiring two populated cells left every one-column supplement
    headerless with its column name read as data.

    A row whose names repeat in blocks is a header too -- a sheet holding two
    tables side by side, or one group of columns per cell type. The numeric and
    long-cell clauses still apply, so a repeating row of numbers is still data.
    """
    present = [c for c in cells if c is not None]
    if len(present) < min(2, max(1, width)):
        return False
    numeric = sum(1 for c in present if _value_kind(c) == NUMBER)
    long_cells = sum(1 for c in present if len(c) > 80)
    distinct = len({c.lower() for c in present})
    return (
        numeric <= len(present) // 2
        and long_cells == 0
        and (distinct >= max(min(2, len(present)), int(0.7 * len(present)))
             or repeating_header(present) is not None)
    )


def _compose_header(rows: List[Sequence[Any]], index: int,
                    width: int) -> Optional[List[Optional[str]]]:
    """Join a sparse group-label row immediately above the header onto it.

    `Supplementary Data 5` of `49_..._MOESM4_ESM.xlsx` puts the cell type on row
    4 and `Sum.PIPs / N.SNPs` on row 5, so the card printed
    `column_5 [number, 6 distinct] = 0 | 0.01 | 0.02 | 0.03 | Endothelial OCRs |
    Sum.PIPs` -- two header strings offered as data values. Composed, the column
    is `Endothelial OCRs / Sum.PIPs`, which is also what makes the names unique.

    Forward-fill is the only reconstruction available: openpyxl in read-only mode
    renders a merged cell as its value followed by `None`s, and
    `ReadOnlyWorksheet` has no `merged_cells` attribute. The row immediately
    above must be non-blank -- a blank line between a caption and a header means
    they are not one header, which is what keeps
    `41591_2018_269_MOESM1_ESM.xlsx` (title, caption, blank, header on row 4)
    out of this path.
    """
    above = index - 1
    if above < 0:
        return None
    upper = _row_cells(rows[above], width)
    lower = _row_cells(rows[index], width)
    upper_present = [c for c in upper if c is not None]
    lower_present = [c for c in lower if c is not None]
    if len(upper_present) < 2 or len(upper_present) >= len(lower_present):
        return None
    if any(_value_kind(c) == NUMBER for c in upper_present):
        return None
    if not _looks_like_labels(upper, width):
        return None

    filled: List[Optional[str]] = []
    carried: Optional[str] = None
    for cell in upper:
        if cell is not None:
            carried = cell
        filled.append(carried)
    return [f"{group} / {name}" if group and name else (name or group)
            for group, name in zip(filled, lower)]


def detect_header(rows: List[Sequence[Any]], limits: Limits) -> Tuple[Optional[int], List[str], str]:
    """Find the header row. Returns `(row_index, caption_lines, confidence)`.

    The rule, in order: skip blank rows; treat a row with a single populated cell
    as a title or caption line; take the first row that is wide relative to the
    table and reads like labels. Confidence is `high` only when the row below the
    candidate has a different type profile -- numbers under text headers -- which
    is the evidence that separates a header from a first data row of gene names.
    """
    if not rows:
        return None, [], "low"

    width = max((sum(1 for v in row if clean_cell(v) is not None) for row in rows), default=0)
    if width == 0:
        return None, [], "low"

    captions: List[str] = []
    scan = rows[: limits.max_header_scan_rows]
    for index, row in enumerate(scan):
        cells = _row_cells(row, max(width, len(row)))
        present = [c for c in cells if c is not None]
        if not present:
            continue
        if len(present) == 1 and width > 1:
            captions.append(present[0])
            continue
        wide_enough = len(present) >= max(min(2, width), int(0.5 * width))
        if wide_enough and _looks_like_labels(cells, width):
            confidence = "low"
            following = rows[index + 1: index + 4]
            for next_row in following:
                next_cells = _row_cells(next_row, max(width, len(next_row)))
                header_kinds = [_value_kind(c) for c in cells if c is not None]
                next_kinds = [_value_kind(c) for c in next_cells if c is not None]
                if next_kinds and header_kinds and (
                    NUMBER in next_kinds and NUMBER not in header_kinds
                ):
                    confidence = "high"
                    break
            return index, captions, confidence

    # Nothing read like labels: the table is probably headerless (a matrix of
    # numbers, or a single wide column of free text). Say so instead of picking.
    return None, captions, "low"


def _unique_names(header: List[Optional[str]]) -> List[str]:
    names: List[str] = []
    seen: Dict[str, int] = {}
    for position, raw in enumerate(header):
        name = raw or f"column_{position + 1}"
        key = name.lower()
        if key in seen:
            seen[key] += 1
            name = f"{name} ({seen[key]})"
        else:
            seen[key] = 1
        names.append(name)
    return names


# -- column profiling --------------------------------------------------------

def profile_column(name: str, values: List[Optional[str]], limits: Limits,
                   complete: bool = True) -> dict:
    """Summarise one column: type, cardinality, and either its full value set or
    examples plus a numeric range.

    `complete` is False when the scan was truncated. The `=` form means *the
    complete value set* -- that is the entire point of the card -- so a value set
    drawn from a partial scan must never be rendered with it. Measured on
    10.1126/science.aat5031's `02_aat5031_data_s1.csv`, 40,269 lines scanned to
    5,000: the card printed `celltype [text, 12 distinct] = B cell | CD4 T cell
    | ...` where the file holds 33, missing Podocyte, Proximal tubule,
    Glomerular endothelium and every other epithelial and endothelial type.
    """
    present = [v for v in values if v is not None]
    profile: dict = {"name": name, "n_values": len(present), "n_empty": len(values) - len(present)}

    if not present:
        profile["dtype"] = EMPTY
        profile["n_unique"] = 0
        return profile

    kinds = {_value_kind(v) for v in present}
    dtype = kinds.pop() if len(kinds) == 1 else MIXED
    # A column of numbers with a couple of "NA"/"n.d." strings is still numeric.
    if dtype == MIXED and NUMBER in kinds | {dtype}:
        numeric = sum(1 for v in present if _value_kind(v) == NUMBER)
        if numeric >= 0.8 * len(present):
            dtype = NUMBER
    profile["dtype"] = dtype

    ordered_unique: List[str] = []
    seen = set()
    for value in present:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            ordered_unique.append(value)
    profile["n_unique"] = len(ordered_unique)

    short = all(len(v) <= limits.max_value_chars for v in ordered_unique)
    ceiling = limits.max_unique_numeric_values if dtype == NUMBER else limits.max_unique_values
    if complete and len(ordered_unique) <= ceiling and short:
        if dtype == NUMBER:
            profile["values"] = sorted(
                ordered_unique, key=lambda v: (_as_number(v) is None, _as_number(v) or 0))
        else:
            profile["values"] = sorted(ordered_unique, key=lambda v: v.lower())
    else:
        profile["examples"] = [v[: limits.max_value_chars] for v in ordered_unique[:5]]

    if dtype == NUMBER:
        numbers = sorted(n for n in (_as_number(v) for v in present) if n is not None)
        non_finite = sum(1 for v in present
                         if _as_number(v) is None and _looks_non_finite(v))
        if non_finite:
            profile["n_non_finite"] = non_finite
        if numbers:
            profile["min"] = numbers[0]
            profile["max"] = numbers[-1]
            profile["median"] = numbers[len(numbers) // 2]
    return profile


def build_card(
    rows: List[Sequence[Any]],
    source_file: str,
    locator: str,
    limits: Limits,
    title: Optional[str] = None,
    caption: Optional[str] = None,
    n_rows_total: Optional[int] = None,
    truncated: bool = False,
    data_ref: Optional[dict] = None,
    row_offset: int = 0,
) -> Optional[TableCard]:
    """Build a card from already-read rows. None when the table holds no values.

    `row_offset` is where `rows[0]` sits in the source, 0-based, which is nonzero
    when a sheet was split into panels. It only affects `data_ref`, whose row
    numbers are absolute and 1-based so `manuscript-extract table` can re-open
    the file at the exact offset the card was built from.
    """
    if not rows:
        return None

    width = max((len(row) for row in rows), default=0)
    populated = max((sum(1 for v in row if clean_cell(v) is not None) for row in rows), default=0)
    if populated == 0:
        return None

    header_row, caption_lines, confidence = detect_header(rows, limits)
    notes: List[str] = []

    header_rows: Optional[List[int]] = None
    if header_row is None:
        header_names = _unique_names([None] * min(width, limits.max_columns))
        data_start = 0
        notes.append("no header row identified; columns are positional")
    else:
        header_cells = _row_cells(rows[header_row], width)
        repeat = repeating_header([c for c in header_cells if c is not None])
        if repeat:
            offset, times = repeat
            notes.append(
                f"the header repeats {times} times across the row; this sheet holds "
                f"{times} tables side by side" if offset == 0 else
                f"the column names repeat in {times} groups across the row, after "
                f"{offset} leading column(s)")
        composed = _compose_header(rows, header_row, width)
        if composed is not None:
            header_cells = composed
            header_rows = [header_row - 1, header_row]
            notes.append(f"header spans 2 rows ({header_row} and {header_row + 1}); "
                         f"the upper row's labels are forward-filled rightwards, "
                         f"which is how a merged cell reads in read-only mode")
        header_names = _unique_names(header_cells)
        data_start = header_row + 1

    columns_dropped = 0
    if len(header_names) > limits.max_columns:
        columns_dropped = len(header_names) - limits.max_columns
        header_names = header_names[: limits.max_columns]
        notes.append(f"{columns_dropped} column(s) beyond the {limits.max_columns} cap not profiled")

    kept = len(header_names)
    columns_values: List[List[Optional[str]]] = [[] for _ in range(kept)]
    sample_rows: List[List[str]] = []
    n_rows = 0
    first_data: Optional[int] = None
    last_data: Optional[int] = None
    for index in range(data_start, len(rows)):
        cells = _row_cells(rows[index], kept)
        if all(c is None for c in cells):
            continue
        n_rows += 1
        if first_data is None:
            first_data = index
        last_data = index
        for position in range(kept):
            columns_values[position].append(cells[position])
        if len(sample_rows) < limits.max_sample_rows:
            sample_rows.append(["" if c is None else c for c in cells])

    if n_rows == 0 and header_row is not None:
        # Header but nothing under it. Still worth a card: the column names alone
        # say what the table was going to be about.
        notes.append("header present but no data rows")

    columns = [
        profile_column(name, columns_values[position], limits, complete=not truncated)
        for position, name in enumerate(header_names)
    ]
    # Trailing all-empty columns are Excel padding, not real fields.
    while columns and columns[-1]["dtype"] == EMPTY:
        columns.pop()
        header_names.pop()

    non_finite = sum(c.get("n_non_finite", 0) for c in columns)
    if non_finite:
        notes.append(f"{non_finite} non-finite value(s) (Inf/NaN) were not counted "
                     f"in the range")

    if truncated:
        of_total = f" of {n_rows_total}" if n_rows_total else ""
        notes.append(f"scan stopped at {limits.max_scan_rows} rows{of_total}; the "
                     f"value sets below are examples from those rows only")

    if header_row is not None and confidence == "low":
        notes.append("header row detected without type-change confirmation; it may "
                     "be a first data row")

    # A column holding its own header name is a sheet whose later table got read
    # as data. This is a safety net behind `split_blocks`, not a substitute for
    # it: before that splitter landed it fired on exactly 7 column-instances
    # across 5 cards of this corpus -- Figure 4 x2, Figure 6, Figure 7,
    # Figure S6 x2, Figure S9 -- all of them `high`, with no false positives.
    # Exact match, not substring: the substring variant fires on 50 columns here,
    # including legitimate ones.
    self_named = [column["name"] for column in columns
                  if column["name"].lower() in
                  {str(v).lower() for v in (column.get("values") or [])
                   + (column.get("examples") or [])}]
    if self_named:
        confidence = "low"
        shown = ", ".join(repr(n) for n in self_named[:3])
        more = f" and {len(self_named) - 3} more" if len(self_named) > 3 else ""
        notes.append(f"column {shown}{more} contains its own header name as a value; "
                     f"the sheet probably holds more than one table")

    return TableCard(
        source_file=source_file,
        locator=locator,
        title=title,
        caption=caption or (" ".join(caption_lines) if caption_lines else None),
        header=header_names,
        header_row=header_row,
        header_rows=header_rows,
        header_confidence=confidence,
        n_columns=len(header_names),
        n_rows=n_rows,
        n_rows_total=n_rows_total,
        truncated=truncated,
        columns=columns,
        sample_rows=sample_rows,
        columns_dropped=columns_dropped,
        data_ref={
            **{"file": source_file, "locator": locator},
            **(data_ref or {}),
            # Absolute, 1-based, and enough on their own to re-read the rows this
            # card describes. Before this the module docstring promised a
            # re-readable offset and `data_ref` carried no scan window and no
            # file hash, so nothing could act on the promise.
            "header_row": None if header_row is None else row_offset + header_row + 1,
            "first_data_row": None if first_data is None else row_offset + first_data + 1,
            "last_data_row": None if last_data is None else row_offset + last_data + 1,
        },
        notes=notes,
    )


# -- rendering ---------------------------------------------------------------

def _column_line(position: int, column: dict) -> str:
    parts = [f"{position:>3}. {column['name']} [{column['dtype']}"]
    if column["dtype"] == EMPTY:
        parts.append(", all empty]")
        return "".join(parts)
    parts.append(f", {column['n_unique']} distinct")
    if column.get("n_empty"):
        parts.append(f", {column['n_empty']} empty")
    parts.append("]")
    line = "".join(parts)

    if "values" in column:
        line += " = " + " | ".join(column["values"])
    elif "examples" in column:
        line += " e.g. " + ", ".join(column["examples"])
    if "min" in column:
        line += f" (range {_number(column['min'])}-{_number(column['max'])}, " \
                f"median {_number(column['median'])})"
    return line


def _number(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def render(card: TableCard, limits: Limits, caption: Optional[str] = None) -> str:
    """The text a model reads for one table, inside `limits.max_card_chars`.

    Columns are rendered before sample rows because the column profiles are what
    answer metadata questions; if the budget runs out, the sample rows are what
    should go. Whatever is dropped is stated in the text.

    `caption` is the *file's* caption, used when the table has none of its own --
    a spreadsheet sheet has no caption, but the publisher's description of the
    file it sits in ("Table S7. Cytokine analysis, related to Figure 6") is
    exactly what says what the sheet is.
    """
    head: List[str] = []
    name = card.title or "Table"
    head.append(f"TABLE: {name}")
    where = f"File: {card.source_file}"
    if card.locator:
        where += f" ({card.locator})"
    head.append(where)
    if card.caption or caption:
        head.append(f"Caption: {card.caption or caption}")

    shape = f"Shape: {card.n_rows} data row(s) x {card.n_columns} column(s)"
    if card.n_rows_total and card.n_rows_total != card.n_rows:
        shape += f"; source reports {card.n_rows_total} row(s)"
    if card.header_row is not None:
        shape += f"; header on row {card.header_row + 1}"
    head.append(shape)
    for note in card.notes:
        head.append(f"Note: {note}")

    fixed = "\n".join(head)
    budget = max(0, limits.max_card_chars - len(fixed) - 40)

    # Reserve roughly a fifth of what is left for sample rows.
    sample_budget = budget // 5 if card.sample_rows else 0
    column_budget = budget - sample_budget

    rendered: List[str] = []
    used = 0
    shown = 0
    for position, column in enumerate(card.columns[: limits.max_rendered_columns], start=1):
        line = "  " + _column_line(position, column)
        if used + len(line) > column_budget and shown:
            break
        rendered.append(line)
        used += len(line) + 1
        shown += 1

    hidden = len(card.columns) - shown + card.columns_dropped
    body = [f"Columns ({card.n_columns}):"] + rendered
    if hidden > 0:
        body.append(f"  ... {hidden} further column(s) not shown")

    tail: List[str] = []
    if card.sample_rows and sample_budget > 0:
        tail.append("Sample rows:")
        rows_shown = 0
        for position, row in enumerate(card.sample_rows, start=1):
            pairs = [f"{card.header[i]}={row[i]}"
                     for i in range(min(len(row), len(card.header))) if row[i]]
            line = f"  {position}) " + " | ".join(pairs)
            if len(line) > sample_budget:
                line = line[: max(0, sample_budget - 4)] + " ..."
            tail.append(line)
            rows_shown += 1
            sample_budget -= len(line)
            if sample_budget <= 0:
                break
        # The column line has always said how many it hid; this one broke out of
        # the loop and said nothing, in the module whose docstring promises the
        # opposite.
        if rows_shown < len(card.sample_rows):
            tail.append(f"  ... {len(card.sample_rows) - rows_shown} further sample "
                        f"row(s) not shown")

    return "\n".join([fixed] + body + tail)
