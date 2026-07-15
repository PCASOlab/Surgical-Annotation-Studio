"""
core/config.py

Single source of truth for every constant that downstream training code
(DLC, GeoLift, SAKF-Net) also depends on. If the annotation ontology or
keypoint set ever changes, this is the only file that should need editing;
every widget and io_utils module imports from here rather than hardcoding
literals.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Needle / tool keypoints (DeepLabCut bodyparts)
# ---------------------------------------------------------------------------
# Base 5-point needle skeleton (Ethicon C-1, 5.52 mm radius arc), in the
# canonical order used throughout the existing DLC project
# (~/Desktop/master-shivank-2025-12-28) and CollectedData_master.csv.
NEEDLE_BODYPARTS: list[str] = ["tip", "b1", "b2", "b3", "swage"]

# Optional instrument-tip keypoints. Off by default -- toggle in the
# Preprocessing/Keypoint tab ("Track tools too") or by editing config.yaml
# in a project directory. Kept separate from NEEDLE_BODYPARTS so existing
# DLC projects that only know about the 5 needle points are unaffected.
TOOL_BODYPARTS: list[str] = ["left_tool_tip", "right_tool_tip", "tool_crotch"]

NEEDLE_SKELETON: list[tuple[str, str]] = [
    ("tip", "b1"), ("b1", "b2"), ("b2", "b3"), ("b3", "swage"),
]

NEEDLE_RADIUS_MM = 5.52

# ---------------------------------------------------------------------------
# Semantic state ontology (10-class, per PAPER2_STATE_RECOGNITION.md)
# ---------------------------------------------------------------------------
# BACKGROUND is never explicitly clicked by an annotator -- it is the
# implicit state that fills any span of time not covered by an explicit
# event pair below. It is included here because downstream training code
# (SEMANTIC_STATES / NUM_STATES in sakfnet/config.py) counts it as state 0.
BACKGROUND_STATE = "BACKGROUND"

# The 9 explicitly annotated event types, in the order they appear on the
# "Lists" sheet of the existing *_PJ_d2m_semantic_*.xlsx files. Each is
# recorded as a single (timestamp, label) pair -- the *start* of that state.
# The state is considered active until the next event pair begins.
EVENT_TYPES: list[str] = [
    "TIP_ENTRY",
    "TIP_EXIT_FAR",
    "TIP_WITHDRAWAL_FAR",
    "TIP_WITHDRAWAL_NEAR",
    "TAIL_EXIT",
    "READJUSTMENT_START",
    "READJUSTMENT_END",
    "CAM_REPOSITION_START",
    "CAM_REPOSITION_END",
]

SEMANTIC_STATES: list[str] = [BACKGROUND_STATE] + EVENT_TYPES
NUM_STATES = len(SEMANTIC_STATES)

# Keyboard shortcuts for fast event entry in the Semantic tab (1-9, 0).
EVENT_HOTKEYS: dict[str, str] = {
    "1": "TIP_ENTRY",
    "2": "TIP_EXIT_FAR",
    "3": "TIP_WITHDRAWAL_FAR",
    "4": "TIP_WITHDRAWAL_NEAR",
    "5": "TAIL_EXIT",
    "6": "READJUSTMENT_START",
    "7": "READJUSTMENT_END",
    "8": "CAM_REPOSITION_START",
    "9": "CAM_REPOSITION_END",
}

# Max event-pair columns in the xlsx schema (T1/E1 .. T60/E60). 60 matches
# the existing PJ_d2m_semantic files; raise if a stitch ever needs more.
MAX_EVENT_PAIRS = 60

# ---------------------------------------------------------------------------
# Clinical CSV -- privileged fields that must never be logged/exported
# ---------------------------------------------------------------------------
PII_COLUMNS: set[str] = {"MRN", "Progressive_Number"}
SAFE_CASE_KEY = "case_id"

# Columns from merged_surgical_data_v2.csv worth surfacing read-only in the
# Clinical tab as "outcome / severity context" for the annotator.
CLINICAL_DISPLAY_FIELDS: list[str] = [
    "case_id", "video_id_clinical", "anastomosis",
    "Gland_texture", "FRS_gland_texture", "Duct_size_mm", "FRS_duct_size",
    "EBL", "FRS_EBL", "FRS", "POPF", "POPF_grade", "POPF_DIC",
    "Clavien_dindo", "age", "gender", "bmi", "ASA_class", "diabetes",
]

# Scoring scales an annotator can add entries for (long-format rows
# appended to clinical/score_entries.csv, joinable back into
# merged_surgical_data_v2.csv's schema via case_id / video_id_annot).
#
# `level` is "case" (one value per case -- the OSATS/RSS subitems) or
# "stitch" (one value per needle pass -- PJ score / yank / curve
# adherence). `better` records which direction is desirable so the UI can
# label scales for the annotator; it doesn't affect storage. `group` is
# the merged_surgical_data_v2.csv `scale` value the entry should be filed
# under (e.g. all osats_* subitems share group="OSATS"); `item` is that
# CSV's numeric `item` column for the subitem, so entries here join
# cleanly against the existing item numbering (OSATS = items 24-29,
# robotic_skills/RSS = items 30-32).
@dataclass(frozen=True)
class ScaleDef:
    level: str            # "case" | "stitch"
    lo: int
    hi: int
    better: str            # "higher" | "lower"
    group: str             # merged_surgical_data_v2.csv `scale` value
    item: int = 0           # merged_surgical_data_v2.csv `item` number (0 if n/a)
    display_label: str = ""  # human-readable description shown in the UI
    option_labels: Optional[dict[int, str]] = None

    def options(self) -> list[tuple[int, str]]:
        if self.option_labels:
            return [(v, self.option_labels.get(v, str(v))) for v in range(self.lo, self.hi + 1)]
        return [(v, str(v)) for v in range(self.lo, self.hi + 1)]


# Condensed anchor descriptors for OSATS values 1/3/5, taken from the lab's
# modified-OSATS rubric (values 2/4 are intentionally left as plain numbers
# -- the rubric only defines anchors at 1/3/5). Format matches the
# "N \u2013 description" style used for the other scales below.
def _osats_labels(lo_desc: str, mid_desc: str, hi_desc: str) -> dict[int, str]:
    return {1: f"1 \u2013 {lo_desc}", 3: f"3 \u2013 {mid_desc}", 5: f"5 \u2013 {hi_desc}"}


_OSATS_GENTLE_LABELS = _osats_labels(
    "Rough, tears tissue, poor control",
    "Minor trauma, occasional breaks",
    "Appropriate tension, negligible injury",
)
_OSATS_TIME_LABELS = _osats_labels(
    "Uncertain, inefficient, no progress",
    "Slow but organized",
    "Confident, efficient, fluid",
)
_OSATS_INSTRUMENT_LABELS = _osats_labels(
    "Overshoots target, slow to correct",
    "Some overshoot, quick to correct",
    "Accurate, minimal readjustment",
)
_OSATS_FLOW_LABELS = _osats_labels(
    "Uncertain, constantly changing focus",
    "Slow but planned, organized",
    "Safe, confident, maintains focus",
)
_OSATS_TISSUE_LABELS = _osats_labels(
    "One hand, poor coordination",
    "Both hands, sub-optimal dexterity",
    "Both hands, expertly complementary",
)
_OSATS_SUMMARY_LABELS = _osats_labels("Deficient", "Average", "Masterful")

# RSS (robotic skills) subitems all share the same 3-point anchor scale.
_RSS_LABELS = {1: "1 \u2013 Deficient", 2: "2 \u2013 Average", 3: "3 \u2013 Master"}

_PJ_LABELS = {1: "1 \u2013 Poor suture", 2: "2 \u2013 Average suture", 3: "3 \u2013 Excellent suture"}
_YANK_LABELS = {
    1: "1 \u2013 Minimal tissue trauma",
    2: "2 \u2013 Excessive force/displacement",
    3: "3 \u2013 Visible tissue trauma",
}
_CURVE_LABELS = {
    1: "1 \u2013 Follows curve of needle",
    2: "2 \u2013 Off ideal path",
    3: "3 \u2013 Visible tissue trauma",
}

SCALE_DEFS: dict[str, ScaleDef] = {
    # OSATS subitems -- case-level, 1-5, matched to merged_surgical_data_v2.csv
    # items 24-29. `display_label` intentionally left blank so the UI falls
    # back to showing the raw variable name (osats_gentle, osats_time, ...).
    "osats_gentle": ScaleDef(level="case", lo=1, hi=5, better="higher", group="OSATS", item=24,
                              option_labels=_OSATS_GENTLE_LABELS),
    "osats_time": ScaleDef(level="case", lo=1, hi=5, better="higher", group="OSATS", item=25,
                            option_labels=_OSATS_TIME_LABELS),
    "osats_instrument": ScaleDef(level="case", lo=1, hi=5, better="higher", group="OSATS", item=26,
                                  option_labels=_OSATS_INSTRUMENT_LABELS),
    "osats_flow": ScaleDef(level="case", lo=1, hi=5, better="higher", group="OSATS", item=27,
                            option_labels=_OSATS_FLOW_LABELS),
    "osats_tissue": ScaleDef(level="case", lo=1, hi=5, better="higher", group="OSATS", item=28,
                              option_labels=_OSATS_TISSUE_LABELS),
    "osats_summary": ScaleDef(level="case", lo=1, hi=5, better="higher", group="OSATS", item=29,
                               option_labels=_OSATS_SUMMARY_LABELS),
    # RSS (robotic skills) subitems -- case-level, 1-3, items 30-32
    "rss_needle": ScaleDef(level="case", lo=1, hi=3, better="higher", group="RSS", item=30,
                            option_labels=_RSS_LABELS),
    "rss_knot": ScaleDef(level="case", lo=1, hi=3, better="higher", group="RSS", item=31,
                          option_labels=_RSS_LABELS),
    "rss_workspace": ScaleDef(level="case", lo=1, hi=3, better="higher", group="RSS", item=32,
                               option_labels=_RSS_LABELS),
    # Per-stitch scales
    "PJ": ScaleDef(level="stitch", lo=1, hi=3, better="higher", group="PJ", option_labels=_PJ_LABELS),
    "PD_YANK": ScaleDef(level="stitch", lo=1, hi=3, better="lower", group="PD_YANK",
                         option_labels=_YANK_LABELS),
    "PD_CURVE": ScaleDef(level="stitch", lo=1, hi=3, better="lower", group="PD_CURVE",
                          option_labels=_CURVE_LABELS),
    "J_YANK": ScaleDef(level="stitch", lo=1, hi=3, better="lower", group="J_YANK",
                        option_labels=_YANK_LABELS),
    "J_CURVE": ScaleDef(level="stitch", lo=1, hi=3, better="lower", group="J_CURVE",
                         option_labels=_CURVE_LABELS),
}
CASE_LEVEL_SCALES: list[str] = [k for k, v in SCALE_DEFS.items() if v.level == "case"]
STITCH_LEVEL_SCALES: list[str] = [k for k, v in SCALE_DEFS.items() if v.level == "stitch"]
OSATS_SUBITEMS: list[str] = [k for k, v in SCALE_DEFS.items() if v.group == "OSATS"]
RSS_SUBITEMS: list[str] = [k for k, v in SCALE_DEFS.items() if v.group == "RSS"]

# A cut clip is either a needle-driving "stitch" pass or a "knot_tying"
# pass; PJ / yank / curve scores only apply to stitch clips.
CLIP_TYPES: list[str] = ["stitch", "knot_tying"]
DEFAULT_CLIP_TYPE = "stitch"

# ---------------------------------------------------------------------------
# Video preprocessing defaults
# ---------------------------------------------------------------------------
DEFAULT_TARGET_FPS = 30
DEFAULT_TARGET_WIDTH = 640
DEFAULT_TARGET_HEIGHT = 360
DEFAULT_CRF = 18  # visually lossless-ish, matches LANCZOS-resize convention already in use

# ---------------------------------------------------------------------------
# Project directory layout
# ---------------------------------------------------------------------------
@dataclass
class ProjectPaths:
    root: Path

    @property
    def videos(self) -> Path:
        """Raw, full-length uploaded case videos (pre-cut)."""
        return self.root / "videos"

    @property
    def pose(self) -> Path:
        """Standardized per-stitch clips + DLC tracking CSVs, by case_id."""
        return self.root / "pose"

    @property
    def labeled_data(self) -> Path:
        """Standard DeepLabCut labeled-data/ folder (extracted frames + CollectedData)."""
        return self.root / "labeled-data"

    @property
    def semantic(self) -> Path:
        return self.root / "semantic"

    @property
    def clinical(self) -> Path:
        return self.root / "clinical"

    @property
    def config_yaml(self) -> Path:
        return self.root / "config.yaml"

    @property
    def meta_json(self) -> Path:
        """Stitch-cut provenance: original video timebase offsets, fps, etc."""
        return self.root / "project_meta.json"

    def ensure_exists(self) -> None:
        for p in [self.videos, self.pose, self.labeled_data, self.semantic, self.clinical]:
            p.mkdir(parents=True, exist_ok=True)


DEFAULT_SCORER = "master"
