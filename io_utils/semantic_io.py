"""
io_utils/semantic_io.py

Read/write *_PJ_d2m_semantic_<rater>.xlsx files in the exact schema already
in use (verified against 0b6823317195419dbd947019dbf72d96_PJ_d2m_semantic_Saw.xlsx):

  Sheet "Annotations":
    file, pcaso_var, start, stop, Comments, T1, E1, T2, E2, ..., T60, E60
    - file:      the original full-length case video's identifier
    - pcaso_var: stitch id, e.g. "stitch_00.25"
    - start/stop: stitch boundaries, as time-of-day in the ORIGINAL video's
                  timebase (NOT clip-relative -- see CLAUDE.md)
    - Tn/En:     timestamp (original video timebase) + event label, one
                 pair per explicit annotated event, left-to-right in time
                 order. Unused pairs are left blank.

  Sheet "Lists": reference list of event types (used for xlsx data
  validation dropdowns in the original files; we regenerate it identically).

  Sheet "QualityChecks": human-readable rules, reproduced verbatim so the
  file stays self-documenting if someone opens it outside this tool.

All timestamps are Python `datetime.time` objects so openpyxl round-trips
them as native Excel time cells (mm:ss.00-style display), exactly like the
source files.
"""
from __future__ import annotations
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from core.config import EVENT_TYPES, MAX_EVENT_PAIRS
from core.timefmt import parse_timecode
from io_utils.safe_io import atomic_write, read_locked_aware

ANNOTATIONS_SHEET = "Annotations"
LISTS_SHEET = "Lists"
QUALITY_SHEET = "QualityChecks"

QUALITY_CHECK_TEXT = [
    "QUALITY CHECKS (key rules)",
    "\u2022 Yank and Path must be integers in {1,2,3,4}.",
    "\u2022 First event must be TIP_ENTRY; no TIP_WITHDRAWAL_NEAR before it.",
    "\u2022 After the first TIP_EXIT_FAR, later TIP_EXIT_FAR must be preceded by TIP_WITHDRAWAL_FAR.",
    "\u2022 TAIL_EXIT only after a TIP_EXIT_FAR; at most one TAIL_EXIT per row.",
    "\u2022 READJUSTMENT_START must end at READJUSTMENT_END or TIP_WITHDRAWAL_NEAR; no overlaps.",
    "\u2022 CAM_REPOSITION_START must be closed by CAM_REPOSITION_END; may overlap other events.",
    "\u2022 Timestamps non-decreasing left\u2192right; duplicates only if simultaneous.",
    "\u2022 Time cells formatted as mm:ss.00.",
]

# hours too: "mm:ss.00" hid them for anything past the first hour
TIME_NUMBER_FORMAT = "hh:mm:ss.00"


# Conventions found in the lab's semantic files (see docs in this module's
# functions): stitch names are short ("stitch_12", "stitch_12(J)",
# "stitch_00.5", occasionally just "12") or long ("<case or video name>_
# stitch_12"); event times are relative to the clip ("for GUI" files) or in
# the original video's timebase (the original annotations) -- consistently
# within one file. The app writes the short name and keeps each file's time
# convention; new files use clip time.
CLIP_ID_PREFIXES = ("stitch_", "knot_")
_UUID = re.compile(r"[0-9a-fA-F]{8}(-?[0-9a-fA-F]{4}){3}-?[0-9a-fA-F]{12}")


def short_stitch_id(pcaso_var, *names) -> str:
    """The stitch ID without a case/video-name prefix, as written:
    'stitch_12(J)' for 'stitch_12(J)', '<case>_stitch_12(J)' or
    '<video name>_stitch_12(J)'. `names` are prefixes to strip (the longest
    that matches, any capitalisation); then anything still in front of the
    last '_stitch_' / '_knot_' goes (a video name can start with the case ID)."""
    v = _id_text(pcaso_var)
    low = v.lower()
    for name in sorted((str(n or "").strip() for n in names), key=len, reverse=True):
        if name and low.startswith(name.lower() + "_"):
            v = v[len(name) + 1:]
            low = v.lower()
            break
    cut = max(low.rfind("_" + prefix) for prefix in CLIP_ID_PREFIXES)
    return v[cut + 1:] if cut >= 0 else v


def _match_key(pcaso_var, *names) -> str:
    """Comparison form of a stitch ID: no prefix, lower case, a bare number
    read as a stitch ('12' -> 'stitch_12'), leading zeros ignored
    ('stitch_01' == 'stitch_1', 'stitch_00.5' == 'stitch_0.5')."""
    k = short_stitch_id(pcaso_var, *names).lower()
    if k[:1].isdigit():
        k = "stitch_" + k
    m = re.fullmatch(r"(stitch|knot)_(\d+)(.*)", k)
    return f"{m.group(1)}_{int(m.group(2))}{m.group(3)}" if m else k


def find_stitch_row(rows: list["StitchRow"], stitch_id: str,
                    case_id: str = "") -> Optional["StitchRow"]:
    """The row for `stitch_id` (a clip name): an exact pcaso_var match if
    there is one, else a row naming the same stitch another way."""
    exact = next((r for r in rows if r.pcaso_var == stitch_id), None)
    if exact is not None:
        return exact
    key = _match_key(stitch_id, case_id)
    return next((r for r in rows if _match_key(r.pcaso_var, r.file, case_id) == key), None)


def _row_time_convention(row: "StitchRow", slack: float = 1.5) -> Optional[str]:
    """'clip' if the row's events only fit 0..clip length, 'video' if they
    only fit start..stop, None if both or neither fit (or no data). The
    slack covers start/stop recorded in whole seconds."""
    times = [time_to_seconds(t) for t, _ in row.sorted_events()]
    times = [t for t in times if t is not None]
    start, stop = time_to_seconds(row.start), time_to_seconds(row.stop)
    if not times or start is None or stop is None:
        return None
    fits_clip = all(-slack <= t <= (stop - start) + slack for t in times)
    fits_video = all(start - slack <= t <= stop + slack for t in times)
    if fits_clip != fits_video:
        return "clip" if fits_clip else "video"
    return None


def file_uses_clip_time(rows: list["StitchRow"], default: bool = True) -> bool:
    """Whether a file stores event times relative to the clip. Decided per
    file -- the lab's files never mix the two -- from the rows that are
    unambiguous; a clip starting near the video start fits both ways (they
    differ by `start`). Empty or undecidable files: `default` (clip time)."""
    votes = Counter(_row_time_convention(r) for r in rows)
    if votes["clip"] or votes["video"]:
        return votes["clip"] >= votes["video"]
    return default


def find_row_in_file(xlsx_path: Path, stitch_id: str, case_id: str = "") -> Optional["StitchRow"]:
    return find_stitch_row(read_rows(xlsx_path), stitch_id, case_id) if xlsx_path.exists() else None


def case_file_aliases(case_id: str) -> list[str]:
    """Names a case's semantic files have been saved under: the case ID (in
    the app, the full video name), and for an ID starting with a UUID, the
    UUID with and without hyphens (the original annotation files)."""
    names = [case_id]
    m = _UUID.match(case_id or "")
    if m:
        names += [m.group(0), m.group(0).replace("-", "")]
    return list(dict.fromkeys(names))


def semantic_files_for_case(semantic_dir: Path, case_id: str,
                            rater: Optional[str] = None) -> list[Path]:
    """<alias>_PJ_d2m_semantic_<rater>.xlsx files of a case (any rater if
    None), matching 'semantic'/'Semantic' and case alike. The app's own
    file name comes first."""
    if not semantic_dir.exists():
        return []
    aliases = [a.lower() for a in case_file_aliases(case_id)]
    marker = "_pj_d2m_semantic_"
    found = []
    for p in semantic_dir.glob("*.xlsx"):
        low = p.name.lower()
        if p.name.startswith("~") or marker not in low:
            continue
        prefix, _, rest = low.rpartition(marker)
        if prefix in aliases and (rater is None or rest[:-len(".xlsx")] == rater.lower()):
            found.append(p)
    return sorted(found, key=lambda p: (aliases.index(p.name.lower().rpartition(marker)[0]), p.name))


def semantic_path_for(semantic_dir: Path, case_id: str, rater: str) -> Path:
    """The file to read/write for a case + rater: an existing one under any
    of the case's names, else the app's standard name."""
    existing = semantic_files_for_case(semantic_dir, case_id, rater)
    return existing[0] if existing else semantic_dir / f"{case_id}_PJ_d2m_semantic_{rater}.xlsx"


def _clear_row_cells(ws: Worksheet, row_idx: int) -> None:
    """Remove any existing cells from a row so rewritten data cannot leave
    stale values behind when the new row is shorter than the old one."""
    for key in list(ws._cells):
        if key[0] == row_idx:
            del ws._cells[key]


@dataclass
class StitchRow:
    file: str
    pcaso_var: str            # stitch id, e.g. "stitch_00.25"
    start: Optional[time]
    stop: Optional[time]
    comments: Optional[str]
    events: list[tuple[Optional[time], Optional[str]]] = field(default_factory=list)

    def sorted_events(self) -> list[tuple[time, str]]:
        return [(t, e) for (t, e) in self.events if t is not None and e]


def time_to_seconds(t) -> Optional[float]:
    """Seconds from a time cell in any of the forms found in the lab's files:
    Excel time, date-time, duration ([h]:mm:ss), or text ('0:14:24',
    '14:24.55', and '14.19.55' typed with dots = mm.ss.hh). None for an
    empty or unreadable cell (the caller reports unreadable ones)."""
    if t is None:
        return None
    if isinstance(t, (time, datetime)):
        return t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1e6
    if isinstance(t, timedelta):
        return t.total_seconds()
    if isinstance(t, str):
        text = t.strip()
        dotted = re.fullmatch(r"(\d+)\.(\d{1,2})\.(\d+)", text)
        if dotted:
            return int(dotted.group(1)) * 60 + int(dotted.group(2)) + float("0." + dotted.group(3))
        return parse_timecode(text) if ":" in text else None
    return None


def seconds_to_time(sec: float) -> time:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    rem = sec - h * 3600
    m = int(rem // 60)
    rem -= m * 60
    s = int(rem)
    micro = int(round((rem - s) * 1e6))
    if micro >= 1_000_000:
        micro -= 1_000_000
        s += 1
    if s >= 60:
        s -= 60
        m += 1
    if m >= 60:
        m -= 60
        h += 1
    return time(hour=min(h, 23), minute=m, second=s, microsecond=micro)


def _new_workbook(bodypart_events: list[str] = EVENT_TYPES) -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = ANNOTATIONS_SHEET
    header = ["file", "pcaso_var", "start", "stop", "Comments"]
    for i in range(1, MAX_EVENT_PAIRS + 1):
        header += [f"T{i}", f"E{i}"]
    ws.append(header)

    lists_ws = wb.create_sheet(LISTS_SHEET)
    for i, ev in enumerate(bodypart_events):
        lists_ws.append([ev, str(i + 1) if i < 4 else None])

    qc_ws = wb.create_sheet(QUALITY_SHEET)
    for line in QUALITY_CHECK_TEXT:
        qc_ws.append([line])
    return wb


def load_or_create(xlsx_path: Path) -> openpyxl.Workbook:
    if xlsx_path.exists():
        return read_locked_aware(xlsx_path, lambda: openpyxl.load_workbook(xlsx_path))
    return _new_workbook()


def read_rows(xlsx_path: Path) -> list[StitchRow]:
    if not xlsx_path.exists():
        return []
    wb = read_locked_aware(xlsx_path,
                           lambda: openpyxl.load_workbook(xlsx_path, data_only=True))
    if ANNOTATIONS_SHEET not in wb.sheetnames:
        return []
    ws = wb[ANNOTATIONS_SHEET]
    rows_out: list[StitchRow] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        row = list(row) + [None] * max(0, 5 - len(row))
        file_, pcaso_var, start, stop, comments = row[0], row[1], row[2], row[3], row[4]
        if _blank(file_) and _blank(pcaso_var):
            continue
        events: list[tuple[Optional[time], Optional[str]]] = []
        rest = row[5:]
        for i in range(0, len(rest) - 1, 2):
            t, label = rest[i], rest[i + 1]
            label = None if _blank(label) else str(label).strip()
            if _blank(t):
                t = None
            elif not isinstance(t, time):
                sec = time_to_seconds(t)
                # text / duration cells become times; unreadable ones stay as
                # they are so the loader can report them
                t = seconds_to_time(sec) if sec is not None else t
            if t is not None or label is not None:
                events.append((t, label))
        rows_out.append(StitchRow(
            file=None if _blank(file_) else str(file_).strip(),
            pcaso_var=_id_text(pcaso_var),
            start=_as_time(start), stop=_as_time(stop),
            comments=None if _blank(comments) else comments,
            events=events,
        ))
    return rows_out


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _as_time(value) -> Optional[time]:
    """start/stop cell (time, duration or text like '0:14:24') -> time."""
    sec = time_to_seconds(value)
    return None if sec is None else seconds_to_time(sec)


def _id_text(value) -> str:
    """pcaso_var as text; a number cell (12, 12.1) is read as typed."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return "" if value is None else str(value).strip()


def upsert_row(xlsx_path: Path, new_row: StitchRow) -> None:
    """Insert or replace the row matching (file, pcaso_var); leaves every
    other stitch's row in the workbook untouched. This is the operation the
    Semantic tab calls on every save, since one xlsx holds all stitches for
    one rater across a whole case."""
    wb = load_or_create(xlsx_path)
    if ANNOTATIONS_SHEET not in wb.sheetnames:
        # existing file with unexpected sheet naming -- rebuild fresh
        wb = _new_workbook()
    ws = wb[ANNOTATIONS_SHEET]

    target_row_idx: Optional[int] = None
    for r in range(2, ws.max_row + 1):
        if ws.cell(row=r, column=1).value == new_row.file and \
           ws.cell(row=r, column=2).value == new_row.pcaso_var:
            target_row_idx = r
            break
    if target_row_idx is None:
        # the same stitch saved under another name ("<case>_stitch_12",
        # "12", ...): update that row. Its `file` value is kept as it is
        # (it may differ from the case ID, typos included -- not ours to fix);
        # the name is rewritten in the short form.
        key = _match_key(new_row.pcaso_var, new_row.file)
        for r in range(2, ws.max_row + 1):
            file_val = ws.cell(row=r, column=1).value
            var_val = ws.cell(row=r, column=2).value
            if not _blank(var_val) and _match_key(var_val, file_val, new_row.file) == key:
                target_row_idx = r
                if not _blank(file_val):
                    new_row = StitchRow(file=file_val, pcaso_var=new_row.pcaso_var,
                                        start=new_row.start, stop=new_row.stop,
                                        comments=new_row.comments, events=new_row.events)
                break
    if target_row_idx is None:
        # first empty row (rows can be blank at the end of a sheet)
        target_row_idx = next((r for r in range(2, ws.max_row + 1)
                               if _blank(ws.cell(row=r, column=1).value)
                               and _blank(ws.cell(row=r, column=2).value)), ws.max_row + 1)

    events = new_row.sorted_events()
    if len(events) > MAX_EVENT_PAIRS:
        raise ValueError(
            f"{new_row.pcaso_var}: {len(events)} events exceeds MAX_EVENT_PAIRS={MAX_EVENT_PAIRS}"
        )

    # Clear the destination row first so a shorter overwrite cannot leave
    # stale values from a previously longer stitch entry to the right.
    _clear_row_cells(ws, target_row_idx)

    ws.cell(row=target_row_idx, column=1, value=new_row.file)
    ws.cell(row=target_row_idx, column=2, value=new_row.pcaso_var)
    c_start = ws.cell(row=target_row_idx, column=3, value=new_row.start)
    c_stop = ws.cell(row=target_row_idx, column=4, value=new_row.stop)
    c_start.number_format = TIME_NUMBER_FORMAT
    c_stop.number_format = TIME_NUMBER_FORMAT
    ws.cell(row=target_row_idx, column=5, value=new_row.comments)

    col = 6
    for i in range(MAX_EVENT_PAIRS):
        t, ev = events[i] if i < len(events) else (None, None)
        tc = ws.cell(row=target_row_idx, column=col, value=t)
        if t is not None:
            tc.number_format = TIME_NUMBER_FORMAT
        ws.cell(row=target_row_idx, column=col + 1, value=ev)
        col += 2

    atomic_write(xlsx_path, lambda tmp: wb.save(tmp))


# ---------------------------------------------------------------------------
# Quality-check validator -- mirrors the QUALITY_CHECK_TEXT rules exactly.
# ---------------------------------------------------------------------------
def validate_events(events: list[tuple[time, str]]) -> list[str]:
    """Returns a list of human-readable problems; empty list == clean."""
    problems: list[str] = []
    if not events:
        return problems

    for i, (t, _lbl) in enumerate(events):
        if not isinstance(t, time):
            problems.append(
                f"Event {i+1}: timestamp cell is not a valid time value ({t!r}). "
                f"This usually means a typo (e.g. '13.19.73' instead of a time) -- fix the cell."
            )

    secs = [time_to_seconds(t) for t, _ in events]
    labels = [e for _, e in events]

    # non-decreasing left-to-right
    for i in range(1, len(secs)):
        if secs[i] is not None and secs[i - 1] is not None and secs[i] < secs[i - 1]:
            problems.append(
                f"Event {i+1} ({labels[i]} @ {events[i][0]}) is earlier than "
                f"event {i} ({labels[i-1]} @ {events[i-1][0]}) -- timestamps must be non-decreasing."
            )

    # First *needle-interaction* event must be TIP_ENTRY; no TIP_WITHDRAWAL_NEAR
    # before it. CAM_REPOSITION_START/END are exempt from ordering checks --
    # per the rules they "may overlap other events" and routinely occur
    # before the first TIP_ENTRY (e.g. framing the shot).
    non_cam_idx = [i for i, l in enumerate(labels) if not l.startswith("CAM_REPOSITION")]
    first_tip_entry_idx = next((i for i, l in enumerate(labels) if l == "TIP_ENTRY"), None)
    if non_cam_idx and labels[non_cam_idx[0]] != "TIP_ENTRY":
        i0 = non_cam_idx[0]
        problems.append(
            f"First needle-interaction event is '{labels[i0]}' (event {i0+1}), "
            f"but it must be TIP_ENTRY."
        )
    if first_tip_entry_idx is not None:
        for i in range(first_tip_entry_idx):
            if labels[i] == "TIP_WITHDRAWAL_NEAR":
                problems.append(f"Event {i+1}: TIP_WITHDRAWAL_NEAR occurs before the first TIP_ENTRY.")

    # After the first TIP_EXIT_FAR *within a suturing pass*, a later
    # TIP_EXIT_FAR in that same pass must be preceded by TIP_WITHDRAWAL_FAR.
    # Each TIP_ENTRY starts a new pass and resets this check -- otherwise
    # every stitch with more than one needle pass would be flagged.
    seen_first_exit_far = False
    since_last_exit_far_had_withdrawal_far = False
    for i, l in enumerate(labels):
        if l == "TIP_ENTRY":
            seen_first_exit_far = False
            since_last_exit_far_had_withdrawal_far = False
        elif l == "TIP_EXIT_FAR":
            if seen_first_exit_far and not since_last_exit_far_had_withdrawal_far:
                problems.append(
                    f"Event {i+1}: TIP_EXIT_FAR not preceded by TIP_WITHDRAWAL_FAR "
                    f"since the previous TIP_EXIT_FAR in this pass."
                )
            seen_first_exit_far = True
            since_last_exit_far_had_withdrawal_far = False
        elif l == "TIP_WITHDRAWAL_FAR":
            since_last_exit_far_had_withdrawal_far = True

    # TAIL_EXIT only after a TIP_EXIT_FAR; at most one TAIL_EXIT per "pass"
    # (we check: at most one TAIL_EXIT between consecutive TIP_ENTRYs)
    tail_exit_count_since_entry = 0
    exit_far_seen_since_entry = False
    for i, l in enumerate(labels):
        if l == "TIP_ENTRY":
            tail_exit_count_since_entry = 0
            exit_far_seen_since_entry = False
        elif l == "TIP_EXIT_FAR":
            exit_far_seen_since_entry = True
        elif l == "TAIL_EXIT":
            if not exit_far_seen_since_entry:
                problems.append(f"Event {i+1}: TAIL_EXIT occurs without a preceding TIP_EXIT_FAR.")
            tail_exit_count_since_entry += 1
            if tail_exit_count_since_entry > 1:
                problems.append(f"Event {i+1}: more than one TAIL_EXIT since the last TIP_ENTRY.")

    # READJUSTMENT_START must end at READJUSTMENT_END or TIP_WITHDRAWAL_NEAR; no overlaps.
    # TIP_WITHDRAWAL_NEAR validly closes an active readjustment even if other
    # labels (e.g. CAM_REPOSITION_* or other intermediate events) occur in between.
    open_readj: Optional[int] = None
    for i, l in enumerate(labels):
        if l == "READJUSTMENT_START":
            if open_readj is not None:
                problems.append(f"Event {i+1}: READJUSTMENT_START opened while another is still open.")
            open_readj = i
        elif l in ("READJUSTMENT_END", "TIP_WITHDRAWAL_NEAR"):
            if l == "READJUSTMENT_END":
                if open_readj is None:
                    problems.append(f"Event {i+1}: READJUSTMENT_END with no matching READJUSTMENT_START.")
                    continue
            elif open_readj is None:
                # A stray tip withdrawal near does not itself create or demand a
                # readjustment; it only closes an active one when present.
                continue
            open_readj = None
    if open_readj is not None:
        problems.append(
            f"Event {open_readj+1}: READJUSTMENT_START is never closed by "
            f"READJUSTMENT_END or TIP_WITHDRAWAL_NEAR."
        )

    # CAM_REPOSITION_START must be closed by CAM_REPOSITION_END (overlaps OK)
    open_cam = 0
    for i, l in enumerate(labels):
        if l == "CAM_REPOSITION_START":
            open_cam += 1
        elif l == "CAM_REPOSITION_END":
            open_cam -= 1
            if open_cam < 0:
                problems.append(f"Event {i+1}: CAM_REPOSITION_END with no matching CAM_REPOSITION_START.")
                open_cam = 0
    if open_cam > 0:
        problems.append(f"{open_cam} CAM_REPOSITION_START event(s) never closed by CAM_REPOSITION_END.")

    return problems


def find_existing_stitch_timing(
    semantic_dir: Path, case_id: str, stitch_id: str
) -> Optional[tuple[float, float]]:
    """Looks across every *_PJ_d2m_semantic_*.xlsx already saved for
    `case_id` for a row matching `stitch_id` (pcaso_var), and returns its
    (start, stop) in seconds in the ORIGINAL video's timebase, if found.
    Used by the Existing Clips tab to recover accurate cut-offset metadata
    for a clip that's already been cut (and possibly already annotated)
    outside this tool, rather than guessing 0/duration."""
    if not semantic_dir.exists():
        return None
    for path in semantic_files_for_case(semantic_dir, case_id):
        row = find_stitch_row(read_rows(path), stitch_id, case_id)
        if row is not None:
            start_sec = time_to_seconds(row.start)
            stop_sec = time_to_seconds(row.stop)
            if start_sec is not None and stop_sec is not None:
                return start_sec, stop_sec
    return None
