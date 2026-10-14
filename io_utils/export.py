"""
io_utils/export.py

Packs a project into a zip for sharing: cut clips, keypoint labels,
semantic annotations, scores and project metadata -- but never the
original videos, and never clinical files other than the score files
(an outcomes CSV loaded via the Clinical tab can hold patient identifiers).

plan_export() decides what goes in (and what's left out and why), so the
GUI can show it before anything is written; write_export_zip() writes it.
The recipient unzips and opens the folder with "Open existing project".
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from io_utils import clinical_io, xlsx_mirror
from io_utils.safe_io import atomic_write, read_locked_aware

STANDARDIZED_NAME = "_full_standardized.mp4"
DRAFT_NAME = "_clip_list_draft.json"
# already compressed -- store as-is, deflating them only costs time
_STORED_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".png", ".jpg", ".jpeg",
                    ".xlsx", ".h5", ".zip"}


@dataclass
class ExportItem:
    arcname: str                   # path inside the project folder, "/"-separated
    kind: str                      # clip / keypoints / semantic / scores / project / ...
    size: int
    source: Optional[Path] = None  # copied from disk ...
    data: Optional[bytes] = None   # ... or generated (filtered metadata, readme)


@dataclass
class ExportPlan:
    project_name: str
    cases: list[str]
    items: list[ExportItem] = field(default_factory=list)
    left_out: list[tuple[str, int, str]] = field(default_factory=list)  # (what, size, why)

    @property
    def total_size(self) -> int:
        return sum(i.size for i in self.items)

    def size_by_kind(self) -> dict[str, tuple[int, int]]:
        out: dict[str, tuple[int, int]] = {}
        for i in self.items:
            n, s = out.get(i.kind, (0, 0))
            out[i.kind] = (n + 1, s + i.size)
        return out


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _rel(pm, path: Path) -> str:
    return path.relative_to(pm.paths.root).as_posix()


def _file(pm, path: Path, kind: str) -> ExportItem:
    return ExportItem(_rel(pm, path), kind, path.stat().st_size, source=path)


def _generated(arcname: str, kind: str, data: bytes) -> ExportItem:
    return ExportItem(arcname, kind, len(data), data=data)


def _clip_folders(pm, cases: list[str]) -> set[str]:
    """labeled-data folder names belonging to these cases: <case>_<stitch>
    (clip files are already named that way; older ones get the prefix)."""
    names: set[str] = set()
    for case in cases:
        stems = {p.stem for p in pm.list_stitches(case)}
        stems |= {m.stitch_id for m in pm.all_stitch_meta() if m.case_id == case}
        for stem in stems:
            if stem.startswith("_full_standardized"):
                continue
            names.add(stem if stem.startswith(f"{case}_") else f"{case}_{stem}")
    return names


def _filtered_collected_data(csv_path: Path, folders: set[str]) -> bytes:
    """DLC CollectedData CSV keeping the 3 header rows + rows of these folders."""
    def read() -> list[list[str]]:
        with open(csv_path, newline="", encoding="utf-8") as f:
            return list(csv.reader(f))
    rows = read_locked_aware(csv_path, read)
    kept = rows[:3] + [r for r in rows[3:] if len(r) > 1 and r[1] in folders]
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\r\n").writerows(kept)
    return buf.getvalue().encode("utf-8")


def plan_export(pm, cases: list[str], include_drafts: bool = True,
                include_standardized: bool = False) -> ExportPlan:
    root = pm.paths.root
    all_cases = set(pm.list_cases()) | {m.case_id for m in pm.all_stitch_meta()}
    selected = [c for c in cases]
    everything = set(selected) >= all_cases
    plan = ExportPlan(project_name=root.name, cases=selected)

    # -- project settings + metadata (filtered to the selected cases)
    if pm.paths.config_yaml.exists():
        plan.items.append(_file(pm, pm.paths.config_yaml, "project"))
    if pm.paths.meta_json.exists():
        meta = json.loads(read_locked_aware(
            pm.paths.meta_json, lambda: pm.paths.meta_json.read_text(encoding="utf-8")))
        meta["stitches"] = {k: v for k, v in meta.get("stitches", {}).items()
                            if v.get("case_id") in selected}
        meta["case_types"] = {k: v for k, v in meta.get("case_types", {}).items()
                              if k in selected}
        plan.items.append(_generated("project_meta.json", "project",
                                     json.dumps(meta, indent=2).encode("utf-8")))

    # -- clips (pose/<case>/)
    for case in selected:
        case_dir = pm.paths.pose / case
        if not case_dir.is_dir():
            continue
        for f in sorted(p for p in case_dir.iterdir() if p.is_file()):
            if f.name.startswith("~"):
                continue  # temp file from an interrupted save
            if f.name == STANDARDIZED_NAME:
                if include_standardized:
                    plan.items.append(_file(pm, f, "standardized video"))
                else:
                    plan.left_out.append((_rel(pm, f), f.stat().st_size,
                                          "standardized copy of the whole case video"))
            elif f.name == DRAFT_NAME:
                if include_drafts:
                    plan.items.append(_file(pm, f, "unfinished clip list"))
                else:
                    plan.left_out.append((_rel(pm, f), f.stat().st_size,
                                          "clips not cut yet (option off)"))
            elif f.suffix.lower() == ".mp4":
                plan.items.append(_file(pm, f, "clip"))
            else:
                plan.items.append(_file(pm, f, "tracking data"))

    # -- keypoints (labeled-data/)
    ld = pm.paths.labeled_data
    if ld.is_dir():
        folders = _clip_folders(pm, selected)
        for entry in sorted(ld.iterdir()):
            if entry.is_dir():
                if everything or entry.name in folders:
                    for img in sorted(p for p in entry.rglob("*") if p.is_file()):
                        plan.items.append(_file(pm, img, "keypoint images"))
            elif entry.name.startswith("CollectedData_") and entry.suffix == ".csv":
                copy = xlsx_mirror.mirror_path(entry)
                if everything:
                    plan.items.append(_file(pm, entry, "keypoint labels"))
                    if copy.exists():
                        plan.items.append(_file(pm, copy, "Excel copies"))
                else:
                    data = _filtered_collected_data(entry, folders)
                    plan.items.append(_generated(_rel(pm, entry), "keypoint labels", data))
                    rows = list(csv.reader(io.StringIO(data.decode("utf-8"))))
                    plan.items.append(_generated(_rel(pm, copy), "Excel copies",
                                                 xlsx_mirror.build_bytes(rows, header_rows=3,
                                                                         numeric_from=3)))
            elif entry.name.startswith("CollectedData_") and entry.suffix == ".xlsx":
                continue  # Excel copy -- handled with its CSV above
            elif entry.is_file() and not entry.name.startswith("~"):
                if everything:
                    plan.items.append(_file(pm, entry, "keypoint labels"))
                else:
                    plan.left_out.append((_rel(pm, entry), entry.stat().st_size,
                                          "covers all cases and can't be split per case"))

    # -- semantic annotations (semantic/<case>_PJ_d2m_semantic_<rater>.xlsx)
    for case in selected:
        for x in pm.semantic_xlsx_for_case(case):
            plan.items.append(_file(pm, x, "semantic annotations"))

    # -- scores (clinical/score_entries_<case>_rater-<rater>.csv) -- and nothing else
    clinical = pm.paths.clinical
    score_files = {p for case in selected for p in clinical_io.case_score_files(clinical, case)}
    for p in sorted(score_files):
        plan.items.append(_file(pm, p, "scores"))
        if xlsx_mirror.mirror_path(p).exists():
            plan.items.append(_file(pm, xlsx_mirror.mirror_path(p), "Excel copies"))
    if clinical.is_dir():
        for f in sorted(p for p in clinical.iterdir() if p.is_file()):
            if f in score_files or (f.name.startswith("score_entries_")
                                    and clinical_io.RATER_TAG in f.name):
                continue  # this or another case's score file (or its Excel copy)
            if f.name.endswith(".migrated"):
                why = "backup of the old score files"
            elif f.name.startswith("score_entries"):
                why = "old-style score file (not split per rater yet)"
            else:
                why = "clinical data that may identify patients"
            plan.left_out.append((_rel(pm, f), f.stat().st_size, why))

    # -- original videos: always left out
    vids = pm.list_source_videos()
    if vids:
        plan.left_out.insert(0, (f"videos/ ({len(vids)} file{'s' if len(vids) != 1 else ''})",
                                 sum(v.stat().st_size for v in vids), "original case videos"))

    plan.items.append(_generated("README_EXPORT.txt", "readme", _readme(plan).encode("utf-8")))
    return plan


def _readme(plan: ExportPlan) -> str:
    lines = [
        f"Surgical Annotation Studio project export: {plan.project_name}",
        f"Exported {datetime.now():%Y-%m-%d %H:%M}",
        "",
        "To use it: unzip, start Surgical Annotation Studio, choose",
        "'Open existing project' and pick this folder.",
        "",
        f"Cases: {', '.join(plan.cases) or '(none)'}",
        "",
        "Included:",
    ]
    for kind, (n, size) in sorted(plan.size_by_kind().items()):
        lines.append(f"  {kind}: {n} file(s), {human_size(size)}")
    lines += ["", "Left out:"]
    if plan.left_out:
        lines += [f"  {what} -- {why}" for what, _, why in plan.left_out]
    else:
        lines.append("  nothing")
    lines += [
        "",
        "Cutting new clips needs the original case video: put it in videos/",
        "and click 'Rescan videos folder' in the Preprocessing tab.",
        "Scores are kept in one file per case and rater",
        "(clinical/score_entries_<case>_rater-<rater>.csv), so work from",
        "different raters can be combined by copying the files together.",
    ]
    return "\n".join(lines) + "\n"


def write_export_zip(plan: ExportPlan, dest: Path,
                     on_progress: Optional[Callable[[int, int], None]] = None) -> Path:
    """Write the zip (atomically: a failed export leaves no partial file).
    Everything goes under one top folder named after the project."""
    total = max(plan.total_size, 1)
    done = 0

    def write(tmp: Path) -> None:
        nonlocal done
        with zipfile.ZipFile(tmp, "w", allowZip64=True) as zf:
            for item in plan.items:
                name = f"{plan.project_name}/{item.arcname}"
                stored = Path(item.arcname).suffix.lower() in _STORED_SUFFIXES
                method = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
                if item.data is not None:
                    zf.writestr(name, item.data, compress_type=method)
                else:
                    zf.write(item.source, name, compress_type=method)
                done += item.size
                if on_progress:
                    on_progress(min(100, done * 100 // total), 100)

    atomic_write(Path(dest), write)
    return Path(dest)
