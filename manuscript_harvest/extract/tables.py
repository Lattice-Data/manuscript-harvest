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


# -- header detection --------------------------------------------------------

def _looks_like_labels(cells: List[Optional[str]], width: int = 2) -> bool:
    """True when a row reads like column names rather than data.

    `width` is how wide the table is. A single-column table has a single-cell
    header, so requiring two populated cells left every one-column supplement
    headerless with its column name read as data.
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
        and distinct >= max(min(2, len(present)), int(0.7 * len(present)))
    )


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

def profile_column(name: str, values: List[Optional[str]], limits: Limits) -> dict:
    """Summarise one column: type, cardinality, and either its full value set or
    examples plus a numeric range."""
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
    if len(ordered_unique) <= ceiling and short:
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
) -> Optional[TableCard]:
    """Build a card from already-read rows. None when the table holds no values."""
    if not rows:
        return None

    width = max((len(row) for row in rows), default=0)
    populated = max((sum(1 for v in row if clean_cell(v) is not None) for row in rows), default=0)
    if populated == 0:
        return None

    header_row, caption_lines, confidence = detect_header(rows, limits)
    notes: List[str] = []

    if header_row is None:
        header_names = _unique_names([None] * min(width, limits.max_columns))
        data_rows = [r for r in rows if any(clean_cell(v) is not None for v in r)]
        notes.append("no header row identified; columns are positional")
    else:
        header_cells = _row_cells(rows[header_row], width)
        header_names = _unique_names(header_cells)
        data_rows = rows[header_row + 1:]

    columns_dropped = 0
    if len(header_names) > limits.max_columns:
        columns_dropped = len(header_names) - limits.max_columns
        header_names = header_names[: limits.max_columns]
        notes.append(f"{columns_dropped} column(s) beyond the {limits.max_columns} cap not profiled")

    kept = len(header_names)
    columns_values: List[List[Optional[str]]] = [[] for _ in range(kept)]
    sample_rows: List[List[str]] = []
    n_rows = 0
    for row in data_rows:
        cells = _row_cells(row, kept)
        if all(c is None for c in cells):
            continue
        n_rows += 1
        for position in range(kept):
            columns_values[position].append(cells[position])
        if len(sample_rows) < limits.max_sample_rows:
            sample_rows.append(["" if c is None else c for c in cells])

    if n_rows == 0 and header_row is not None:
        # Header but nothing under it. Still worth a card: the column names alone
        # say what the table was going to be about.
        notes.append("header present but no data rows")

    columns = [
        profile_column(name, columns_values[position], limits)
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
        notes.append(f"scan stopped at {limits.max_scan_rows} rows; profile covers "
                     f"only those rows")

    if header_row is not None and confidence == "low":
        notes.append("header row detected without type-change confirmation; it may "
                     "be a first data row")

    return TableCard(
        source_file=source_file,
        locator=locator,
        title=title,
        caption=caption or (" ".join(caption_lines) if caption_lines else None),
        header=header_names,
        header_row=header_row,
        header_confidence=confidence,
        n_columns=len(header_names),
        n_rows=n_rows,
        n_rows_total=n_rows_total,
        truncated=truncated,
        columns=columns,
        sample_rows=sample_rows,
        columns_dropped=columns_dropped,
        data_ref=data_ref or {"file": source_file, "locator": locator,
                              "header_row": header_row},
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


def render(card: TableCard, limits: Limits) -> str:
    """The text a model reads for one table, inside `limits.max_card_chars`.

    Columns are rendered before sample rows because the column profiles are what
    answer metadata questions; if the budget runs out, the sample rows are what
    should go. Whatever is dropped is stated in the text.
    """
    head: List[str] = []
    name = card.title or "Table"
    head.append(f"TABLE: {name}")
    where = f"File: {card.source_file}"
    if card.locator:
        where += f" ({card.locator})"
    head.append(where)
    if card.caption:
        head.append(f"Caption: {card.caption}")

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
        for position, row in enumerate(card.sample_rows, start=1):
            pairs = [f"{card.header[i]}={row[i]}"
                     for i in range(min(len(row), len(card.header))) if row[i]]
            line = f"  {position}) " + " | ".join(pairs)
            if len(line) > sample_budget:
                line = line[: max(0, sample_budget - 4)] + " ..."
            tail.append(line)
            sample_budget -= len(line)
            if sample_budget <= 0:
                break

    return "\n".join([fixed] + body + tail)
