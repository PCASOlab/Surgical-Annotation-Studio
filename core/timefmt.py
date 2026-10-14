"""
core/timefmt.py

One time format for everything the user sees or exports:
HH:MM:SS.hh (hours, minutes, seconds, hundredths), the same as the
semantic annotation spreadsheets. Internally times stay in (milli)seconds;
this is only for display and for the start/stop columns of score files.
"""
from __future__ import annotations

from typing import Optional


def format_timecode(seconds: float) -> str:
    """12.3456 -> '00:00:12.35'"""
    hundredths = int(round(max(0.0, float(seconds)) * 100))
    h, rem = divmod(hundredths, 360_000)
    m, rem = divmod(rem, 6_000)
    s, hs = divmod(rem, 100)
    return f"{h:02d}:{m:02d}:{s:02d}.{hs:02d}"


def parse_timecode(text: str) -> Optional[float]:
    """'HH:MM:SS.hh', 'MM:SS.hh' or plain seconds -> seconds (any number of
    decimals, so older HH:MM:SS.mmm values work too). None if unparseable."""
    text = str(text).strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3:
        return None
    try:
        if len(parts) == 1:
            seconds = float(parts[0])
        elif len(parts) == 2:
            seconds = int(parts[0]) * 60 + float(parts[1])
        else:
            seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def normalize_time_cell(value: Optional[object]) -> str:
    """Score-file start/stop cell -> HH:MM:SS.hh. Older files stored plain
    seconds ('14.266'); those are converted, timecodes and blanks are kept."""
    text = "" if value is None else str(value).strip()
    if not text or ":" in text:
        return text
    try:
        return format_timecode(float(text))
    except ValueError:
        return text
