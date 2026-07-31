"""
core/project.py

ProjectManager owns the on-disk layout described in config.ProjectPaths and
is the single object every tab talks to for "what cases/videos/stitches
exist and what's already been annotated". Nothing here touches Qt -- keep
it importable/testable headlessly.
"""
from __future__ import annotations
import json
import shutil
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from core.config import ProjectPaths, NEEDLE_BODYPARTS, TOOL_BODYPARTS, DEFAULT_SCORER, DEFAULT_CASE_TYPE


@dataclass
class StitchMeta:
    """Provenance for one cut clip -- lets us map clip-local time back to the
    original (possibly multi-hour) case video's timebase, which is the
    timebase the *_PJ_d2m_semantic_*.xlsx files use (see CLAUDE.md note:
    'XLSX timestamps are in the ORIGINAL video's time-base')."""
    case_id: str
    stitch_id: str
    clip_path: str            # relative to project root
    source_video: str         # relative to project root (the raw case video)
    start_sec_in_source: float
    end_sec_in_source: float
    fps: float                 # fps of the cut clip itself
    width: int                 # width of the cut clip itself
    height: int                # height of the cut clip itself
    clip_type: str = "stitch"  # "stitch" | "knot_tying" (see config.CLIP_TYPES)
    # Native resolution/fps of the ORIGINAL raw source video, *before* any
    # standardization/resize -- required by downstream coordinate-space
    # code (e.g. GeoLift's `_video_resolution()` / `rescale_gt_to_native()`
    # convention) so keypoints can always be traced back to true native
    # pixels regardless of what the clip itself was resampled to.
    source_width: int = 0
    source_height: int = 0
    source_fps: float = 0.0


@dataclass
class ProjectConfig:
    project_name: str = "surgical_annotation_project"
    scorer: str = DEFAULT_SCORER
    needle_bodyparts: list[str] = field(default_factory=lambda: list(NEEDLE_BODYPARTS))
    track_tools: bool = False
    tool_bodyparts: list[str] = field(default_factory=lambda: list(TOOL_BODYPARTS))
    target_fps: int = 30
    target_width: int = 640
    target_height: int = 360


class ProjectManager:
    def __init__(self, root: Path):
        self.paths = ProjectPaths(Path(root))
        self.config = ProjectConfig()
        self._stitches: dict[str, StitchMeta] = {}  # key: f"{case_id}/{stitch_id}"
        self._case_types: dict[str, str] = {}  # case_id -> case type (see config.CASE_TYPES)

    # -- lifecycle ----------------------------------------------------
    @classmethod
    def create(cls, root: Path, config: Optional[ProjectConfig] = None) -> "ProjectManager":
        pm = cls(root)
        pm.paths.ensure_exists()
        if config is not None:
            pm.config = config
        pm._write_config_yaml()
        pm._save_meta()
        return pm

    @classmethod
    def load(cls, root: Path) -> "ProjectManager":
        pm = cls(root)
        pm.paths.ensure_exists()
        pm._load_config_yaml()
        pm._load_meta()
        return pm

    def _write_config_yaml(self) -> None:
        # Minimal, dependency-free YAML writer (avoids requiring pyyaml just
        # for a handful of scalar/list fields). Produces a file that is
        # readable by a real DeepLabCut project's config.yaml conventions,
        # so it can be pointed at / merged with an existing DLC config if
        # the lab already has one -- just paste `bodyparts:` and `skeleton:`
        # across.
        c = self.config
        lines = [
            f"Task: {c.project_name}",
            f"scorer: {c.scorer}",
            "bodyparts:",
        ]
        for bp in c.needle_bodyparts:
            lines.append(f"  - {bp}")
        if c.track_tools:
            for bp in c.tool_bodyparts:
                lines.append(f"  - {bp}")
        lines += [
            f"track_tools: {c.track_tools}",
            f"target_fps: {c.target_fps}",
            f"target_width: {c.target_width}",
            f"target_height: {c.target_height}",
        ]
        self.paths.config_yaml.write_text("\n".join(lines) + "\n")

    def _load_config_yaml(self) -> None:
        if not self.paths.config_yaml.exists():
            return
        c = ProjectConfig()
        bodyparts: list[str] = []
        in_bodyparts = False
        for raw in self.paths.config_yaml.read_text().splitlines():
            line = raw.rstrip()
            if not line:
                continue
            if line.startswith("Task:"):
                c.project_name = line.split(":", 1)[1].strip()
            elif line.startswith("scorer:"):
                c.scorer = line.split(":", 1)[1].strip()
            elif line.startswith("bodyparts:"):
                in_bodyparts = True
                continue
            elif line.startswith("  - ") and in_bodyparts:
                bodyparts.append(line[4:].strip())
                continue
            else:
                in_bodyparts = False
            if line.startswith("track_tools:"):
                c.track_tools = line.split(":", 1)[1].strip() == "True"
            elif line.startswith("target_fps:"):
                c.target_fps = int(line.split(":", 1)[1].strip())
            elif line.startswith("target_width:"):
                c.target_width = int(line.split(":", 1)[1].strip())
            elif line.startswith("target_height:"):
                c.target_height = int(line.split(":", 1)[1].strip())
        if bodyparts:
            # first NEEDLE count assumed fixed-length base set; anything
            # beyond that is tool keypoints
            n_needle = len(NEEDLE_BODYPARTS)
            c.needle_bodyparts = bodyparts[:n_needle] if len(bodyparts) >= n_needle else bodyparts
            c.tool_bodyparts = bodyparts[n_needle:]
        self.config = c

    def _save_meta(self) -> None:
        data = {
            "stitches": {k: asdict(v) for k, v in self._stitches.items()},
            "case_types": dict(self._case_types),
        }
        self.paths.meta_json.write_text(json.dumps(data, indent=2))

    def _load_meta(self) -> None:
        if not self.paths.meta_json.exists():
            self._stitches = {}
            self._case_types = {}
            return
        raw = json.loads(self.paths.meta_json.read_text())
        if "stitches" in raw:
            # current format
            self._stitches = {k: StitchMeta(**v) for k, v in raw["stitches"].items()}
            self._case_types = dict(raw.get("case_types", {}))
        else:
            # older format: the whole file was just the flat stitches dict
            self._stitches = {k: StitchMeta(**v) for k, v in raw.items()}
            self._case_types = {}

    # -- case type (which scoring rubric a case uses) -----------------------
    def set_case_type(self, case_id: str, case_type: str) -> None:
        if self._case_types.get(case_id) == case_type:
            return
        self._case_types[case_id] = case_type
        self._save_meta()

    def get_case_type(self, case_id: str) -> Optional[str]:
        return self._case_types.get(case_id)

    def get_case_type_or_default(self, case_id: str) -> str:
        return self._case_types.get(case_id, DEFAULT_CASE_TYPE)

    # -- case / video discovery ----------------------------------------
    def list_source_videos(self) -> list[Path]:
        if not self.paths.videos.exists():
            return []
        exts = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}
        return sorted(p for p in self.paths.videos.iterdir() if p.suffix.lower() in exts)

    def list_cases(self) -> list[str]:
        if not self.paths.pose.exists():
            return []
        return sorted(p.name for p in self.paths.pose.iterdir() if p.is_dir())

    def list_stitches(self, case_id: str) -> list[Path]:
        case_dir = self.paths.pose / case_id
        if not case_dir.exists():
            return []
        return sorted(p for p in case_dir.glob("*.mp4") if not p.name.startswith("_full_standardized"))

    def list_unregistered_clips(self) -> dict[str, list[Path]]:
        """Every clip file sitting under pose/<case_id>/*.mp4 that has NO
        matching StitchMeta entry yet -- e.g. clips that were cut by hand,
        by an older pipeline, or copied in from another project, rather
        than through this app's own Preprocessing tab. Keyed by case_id."""
        if not self.paths.pose.exists():
            return {}
        result: dict[str, list[Path]] = {}
        for case_dir in sorted(p for p in self.paths.pose.iterdir() if p.is_dir()):
            case_id = case_dir.name
            unregistered = []
            for clip_path in sorted(case_dir.glob("*.mp4")):
                if clip_path.name.startswith("_full_standardized"):
                    continue
                stitch_id = clip_path.stem
                if self.get_stitch_meta(case_id, stitch_id) is None:
                    unregistered.append(clip_path)
            if unregistered:
                result[case_id] = unregistered
        return result

    def register_stitch(self, meta: StitchMeta) -> None:
        self._stitches[f"{meta.case_id}/{meta.stitch_id}"] = meta
        self._save_meta()

    def get_stitch_meta(self, case_id: str, stitch_id: str) -> Optional[StitchMeta]:
        return self._stitches.get(f"{case_id}/{stitch_id}")

    def all_stitch_meta(self) -> list[StitchMeta]:
        return list(self._stitches.values())

    # -- annotation status (for the dashboard) --------------------------
    def semantic_xlsx_for_case(self, case_id: str) -> list[Path]:
        if not self.paths.semantic.exists():
            return []
        return sorted(self.paths.semantic.glob(f"{case_id}_PJ_d2m_semantic_*.xlsx"))

    def dlc_csv_for_case(self, case_id: str) -> list[Path]:
        case_dir = self.paths.pose / case_id
        if not case_dir.exists():
            return []
        return sorted(case_dir.glob("*_DLC_*.csv"))

    def status_summary(self) -> list[dict]:
        """One row per case_id known either from pose/ or from stitch meta,
        for the Dashboard tab table."""
        case_ids = set(self.list_cases())
        for m in self._stitches.values():
            case_ids.add(m.case_id)
        rows = []
        for cid in sorted(case_ids):
            stitches = self.list_stitches(cid)
            rows.append({
                "case_id": cid,
                "n_stitches": len(stitches),
                "n_dlc_csv": len(self.dlc_csv_for_case(cid)),
                "n_semantic_xlsx": len(self.semantic_xlsx_for_case(cid)),
            })
        return rows
