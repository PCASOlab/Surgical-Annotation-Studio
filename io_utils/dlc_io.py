"""
io_utils/dlc_io.py

Read/write DeepLabCut-format CollectedData_<scorer>.csv files, matching the
exact layout used by CollectedData_master.csv already in this project:

    scorer,,,master,master,master,master,master,master,master,master,master,master
    bodyparts,,,tip,tip,b1,b1,b2,b2,b3,b3,swage,swage
    coords,,,x,y,x,y,x,y,x,y,x,y
    labeled-data,<video_folder>,<image_name>.png,<x>,<y>,<x>,<y>,...

i.e. 3 index columns (literal "labeled-data", video-folder name, image
filename) followed by 2 columns per bodypart (x, y), in that order. Missing
keypoints are written as empty cells (matching the last example row of the
real file, where swage x/y is blank).

This module never trains or runs DLC itself -- it only produces/consumes
the CSV DeepLabCut's own labeling GUI and training pipeline expect, so a
project created here can be opened directly in DeepLabCut, and vice versa.
"""
from __future__ import annotations
import csv
from pathlib import Path
from typing import Optional

from core.config import NEEDLE_BODYPARTS


def collected_data_path(labeled_data_root: Path, scorer: str) -> Path:
    return labeled_data_root / f"CollectedData_{scorer}.csv"


def _header_rows(scorer: str, bodyparts: list[str]) -> list[list[str]]:
    row1 = ["scorer", "", ""] + [scorer for _ in bodyparts for _ in (0, 1)]
    row2 = ["bodyparts", "", ""] + [bp for bp in bodyparts for _ in (0, 1)]
    row3 = ["coords", "", ""] + ["x", "y"] * len(bodyparts)
    return [row1, row2, row3]


def read_collected_data(
    csv_path: Path,
) -> tuple[list[str], dict[tuple[str, str], dict[str, tuple[Optional[float], Optional[float]]]]]:
    """Returns (bodyparts, {(video_folder, image_name): {bodypart: (x, y)}}).
    x/y are None if the keypoint wasn't labeled on that frame."""
    if not csv_path.exists():
        return list(NEEDLE_BODYPARTS), {}
    with open(csv_path, newline="") as f:
        rows = list(csv.reader(f))
    if len(rows) < 3:
        return list(NEEDLE_BODYPARTS), {}
    bp_row = rows[1][3:]
    bodyparts: list[str] = []
    for i in range(0, len(bp_row), 2):
        bodyparts.append(bp_row[i])
    data: dict[tuple[str, str], dict[str, tuple[Optional[float], Optional[float]]]] = {}
    for r in rows[3:]:
        if len(r) < 3 or not r[0]:
            continue
        folder, image = r[1], r[2]
        coords = r[3:]
        kp: dict[str, tuple[Optional[float], Optional[float]]] = {}
        for i, bp in enumerate(bodyparts):
            xs = coords[2 * i] if 2 * i < len(coords) else ""
            ys = coords[2 * i + 1] if 2 * i + 1 < len(coords) else ""
            x = float(xs) if xs not in ("", None) else None
            y = float(ys) if ys not in ("", None) else None
            kp[bp] = (x, y)
        data[(folder, image)] = kp
    return bodyparts, data


def write_collected_data(
    csv_path: Path,
    scorer: str,
    bodyparts: list[str],
    data: dict[tuple[str, str], dict[str, tuple[Optional[float], Optional[float]]]],
) -> None:
    """Overwrites csv_path with the full table (header + all rows), sorted
    by (video_folder, image_name) for deterministic diffs."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    rows = _header_rows(scorer, bodyparts)
    for (folder, image) in sorted(data.keys()):
        kp = data[(folder, image)]
        row = ["labeled-data", folder, image]
        for bp in bodyparts:
            xy = kp.get(bp) or (None, None)
            x, y = xy
            row.append("" if x is None else repr(float(x)))
            row.append("" if y is None else repr(float(y)))
        rows.append(row)
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerows(rows)


def upsert_frame(
    csv_path: Path,
    scorer: str,
    bodyparts: list[str],
    video_folder: str,
    image_name: str,
    keypoints: dict[str, tuple[Optional[float], Optional[float]]],
) -> None:
    """Load, update/insert one frame's keypoints, and write back. Safe to
    call repeatedly as an annotator labels frame-by-frame -- each save is a
    full-table rewrite so a crash mid-session can't corrupt the CSV."""
    existing_bp, data = read_collected_data(csv_path)
    # Reconcile bodypart list: keep existing order, append any new ones.
    merged_bp = list(existing_bp)
    for bp in bodyparts:
        if bp not in merged_bp:
            merged_bp.append(bp)
    key = (video_folder, image_name)
    row = data.get(key, {})
    row.update(keypoints)
    data[key] = row
    write_collected_data(csv_path, scorer, merged_bp, data)


def labeled_frame_count(csv_path: Path) -> int:
    _, data = read_collected_data(csv_path)
    return len(data)
