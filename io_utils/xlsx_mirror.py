"""
io_utils/xlsx_mirror.py

Every CSV the app writes (score files, keypoint CollectedData) gets an
Excel copy next to it with the same name and .xlsx, so it opens in Excel
with proper types: times as real Excel times shown as hh:mm:ss.00
(a CSV can't carry a display format, so Excel showed them as mm:ss.0 and
hid the hours), numbers as numbers.

The CSV stays the master. The .xlsx is often the file someone has open in
Excel, so a locked copy never fails the save: failure_hook is told (the
main window shows it in the status bar) and the copy is rebuilt on the
next save or by refresh_stale() when a project is opened.
"""
from __future__ import annotations

import csv
import io
import sys
from datetime import time
from pathlib import Path
from typing import Callable, Iterable, Optional

import openpyxl
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from core.timefmt import parse_timecode
from io_utils.safe_io import atomic_write, read_locked_aware

TIME_FORMAT = "hh:mm:ss.00"

# set by the GUI: called with (xlsx_path, error) when a copy couldn't be updated
failure_hook: Optional[Callable[[Path, Exception], None]] = None


def mirror_path(csv_path: Path) -> Path:
    return Path(csv_path).with_suffix(".xlsx")


def _seconds_to_time(sec: float) -> time:
    sec = max(0.0, sec)
    micro = int(round((sec - int(sec)) * 1e6))
    whole = int(sec) + micro // 1_000_000
    micro %= 1_000_000
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    return time(min(h, 23), m, s, micro)


def _number(text: str):
    try:
        f = float(text)
    except ValueError:
        return text
    return int(f) if f.is_integer() and "." not in text and "e" not in text.lower() else f


def _build(rows: list[list[str]], header_rows: int, time_cols: Iterable[int],
           numeric_cols: Optional[Iterable[int]], numeric_from: Optional[int]) -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "data"
    time_cols, numeric_cols = set(time_cols), set(numeric_cols or ())
    widths: dict[int, int] = {}
    for r, row in enumerate(rows, start=1):
        for c, text in enumerate(row, start=1):
            value: object = text
            if r > header_rows and text != "":
                if c - 1 in time_cols:
                    sec = parse_timecode(text)
                    value = text if sec is None else _seconds_to_time(sec)
                elif c - 1 in numeric_cols or (numeric_from is not None and c - 1 >= numeric_from):
                    value = _number(text)
            cell = ws.cell(row=r, column=c, value=value)
            if isinstance(value, time):
                cell.number_format = TIME_FORMAT
            if r <= header_rows:
                cell.font = Font(bold=True)
            widths[c] = max(widths.get(c, 0), len(str(text)))
    for c, w in widths.items():
        ws.column_dimensions[get_column_letter(c)].width = min(max(w + 2, 8), 40)
    ws.freeze_panes = ws.cell(row=header_rows + 1, column=1)
    return wb


def build_bytes(rows: list[list[str]], header_rows: int = 1, time_cols: Iterable[int] = (),
                numeric_cols: Optional[Iterable[int]] = None,
                numeric_from: Optional[int] = None) -> bytes:
    buf = io.BytesIO()
    _build(rows, header_rows, time_cols, numeric_cols, numeric_from).save(buf)
    return buf.getvalue()


def write_mirror(csv_path: Path, rows: list[list[str]], header_rows: int = 1,
                 time_cols: Iterable[int] = (), numeric_cols: Optional[Iterable[int]] = None,
                 numeric_from: Optional[int] = None) -> bool:
    """Write <csv_path>.xlsx from the CSV's rows. Never raises for a file
    problem: reports it to failure_hook and returns False."""
    target = mirror_path(csv_path)
    wb = _build(rows, header_rows, list(time_cols), numeric_cols, numeric_from)
    try:
        atomic_write(target, lambda tmp: wb.save(tmp))
        return True
    except OSError as e:
        print(f"Excel copy not updated: {target}: {e}", file=sys.stderr)
        if failure_hook is not None:
            failure_hook(target, e)
        return False


def read_csv_rows(csv_path: Path) -> list[list[str]]:
    def read() -> list[list[str]]:
        with open(csv_path, newline="", encoding="utf-8") as f:
            return list(csv.reader(f))
    return read_locked_aware(csv_path, read)


def is_stale(csv_path: Path) -> bool:
    target = mirror_path(csv_path)
    return not target.exists() or target.stat().st_mtime < Path(csv_path).stat().st_mtime
