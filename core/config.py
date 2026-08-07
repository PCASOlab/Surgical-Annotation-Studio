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

# ---------------------------------------------------------------------------
# Case types -- different procedures use different scoring rubrics.
# ---------------------------------------------------------------------------
CASE_TYPE_PJ_WHIPPLE = "PJ_WHIPPLE"
CASE_TYPE_PEH = "PEH"
CASE_TYPES: list[str] = [CASE_TYPE_PJ_WHIPPLE, CASE_TYPE_PEH]
CASE_TYPE_LABELS: dict[str, str] = {
    CASE_TYPE_PJ_WHIPPLE: "PJ / Whipple",
    CASE_TYPE_PEH: "PEH / Paraesophageal Hernia",
}
DEFAULT_CASE_TYPE = CASE_TYPE_PJ_WHIPPLE


# Scoring scales an annotator can add entries for (long-format rows
# appended to clinical/score_entries.csv, joinable back into
# merged_surgical_data_v2.csv's schema via case_id / video_id_annot).
#
# `level` is "case" (one value per case) or "stitch" (one value per needle
# pass). `better` records which direction is desirable ("higher"/"lower"),
# or "" for a non-ordinal/categorical field like stitch_location where
# there's no better-or-worse direction. `group` is the merged_surgical_
# data_v2.csv `scale` value the entry should be filed under (e.g. all
# osats_* subitems share group="OSATS"); `item` is that CSV's numeric
# `item` column for the subitem where one is established (0 if n/a --
# e.g. every PEH-specific field, since that rubric is new and has no
# existing item numbering yet).
@dataclass(frozen=True)
class ScaleDef:
    level: str            # "case" | "stitch"
    lo: int
    hi: int
    better: str            # "higher" | "lower" | ""
    group: str             # merged_surgical_data_v2.csv `scale` value
    item: int = 0           # merged_surgical_data_v2.csv `item` number (0 if n/a)
    display_label: str = ""  # human-readable description shown in the UI
    option_labels: Optional[dict[int, str]] = None

    def options(self) -> list[tuple[int, str]]:
        if self.option_labels:
            return [(v, self.option_labels.get(v, str(v))) for v in range(self.lo, self.hi + 1)]
        return [(v, str(v)) for v in range(self.lo, self.hi + 1)]


# ---------------------------------------------------------------------------
# OSATS -- shared, unchanged, across every case type (per lab convention).
# ---------------------------------------------------------------------------
def _osats_labels(lo_desc: str, mid_desc: str, hi_desc: str) -> dict[int, str]:
    """Condensed anchor descriptors for OSATS values 1/3/5 (the rubric only
    defines anchors there; 2/4 are left as plain numbers)."""
    return {1: f"1 \u2013 {lo_desc}", 3: f"3 \u2013 {mid_desc}", 5: f"5 \u2013 {hi_desc}"}


_OSATS_SCALE_DEFS: dict[str, ScaleDef] = {
    "osats_gentle": ScaleDef(
        level="case", lo=1, hi=5, better="higher", group="OSATS", item=24,
        option_labels=_osats_labels(
            "Rough, tears tissue, poor control",
            "Minor trauma, occasional breaks",
            "Appropriate tension, negligible injury",
        )),
    "osats_time": ScaleDef(
        level="case", lo=1, hi=5, better="higher", group="OSATS", item=25,
        option_labels=_osats_labels(
            "Uncertain, inefficient, no progress",
            "Slow but organized",
            "Confident, efficient, fluid",
        )),
    "osats_instrument": ScaleDef(
        level="case", lo=1, hi=5, better="higher", group="OSATS", item=26,
        option_labels=_osats_labels(
            "Overshoots target, slow to correct",
            "Some overshoot, quick to correct",
            "Accurate, minimal readjustment",
        )),
    "osats_flow": ScaleDef(
        level="case", lo=1, hi=5, better="higher", group="OSATS", item=27,
        option_labels=_osats_labels(
            "Uncertain, constantly changing focus",
            "Slow but planned, organized",
            "Safe, confident, maintains focus",
        )),
    "osats_tissue": ScaleDef(
        level="case", lo=1, hi=5, better="higher", group="OSATS", item=28,
        option_labels=_osats_labels(
            "One hand, poor coordination",
            "Both hands, sub-optimal dexterity",
            "Both hands, expertly complementary",
        )),
    "osats_summary": ScaleDef(
        level="case", lo=1, hi=5, better="higher", group="OSATS", item=29,
        option_labels=_osats_labels("Deficient", "Average", "Masterful")),
}
OSATS_SUBITEMS: list[str] = list(_OSATS_SCALE_DEFS.keys())

# ---------------------------------------------------------------------------
# PJ / Whipple rubric (pancreaticojejunostomy)
# ---------------------------------------------------------------------------
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

_PJ_WHIPPLE_SCALE_DEFS: dict[str, ScaleDef] = {
    **_OSATS_SCALE_DEFS,
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

# ---------------------------------------------------------------------------
# PEH rubric (paraesophageal hernia repair -- crural closure)
# ---------------------------------------------------------------------------
_PEH_SCALE_DEFS: dict[str, ScaleDef] = {
    **_OSATS_SCALE_DEFS,

    # -- case-level: closure/safety assessment --------------------------------
    "posterior_crural_exposure": ScaleDef(
        level="case", lo=1, hi=2, better="higher", group="CRURAL_EXPOSURE",
        option_labels={1: "1 \u2013 No: posterior junction not fully exposed",
                       2: "2 \u2013 Yes: fully exposed before closure"}),
    "closure_security": ScaleDef(
        level="case", lo=1, hi=2, better="higher", group="CLOSURE_SECURITY",
        option_labels={1: "1 \u2013 Inadequate: insecure or visible gap",
                       2: "2 \u2013 Adequate: appears secure"}),
    "closure_tightness": ScaleDef(
        level="case", lo=1, hi=3, better="", group="CLOSURE_TIGHTNESS",
        option_labels={1: "1 \u2013 Too Tight: overly constricted around esophagus",
                       2: "2 \u2013 Too Loose: inadequately approximated",
                       3: "3 \u2013 Ideal: appropriately calibrated"}),
    "calibration_performed": ScaleDef(
        level="case", lo=1, hi=2, better="higher", group="CALIBRATION",
        option_labels={1: "1 \u2013 No: not calibrated",
                       2: "2 \u2013 Yes: calibrated with bougie/endoscope"}),
    "safety": ScaleDef(
        level="case", lo=1, hi=3, better="higher", group="SAFETY",
        option_labels={1: "1 \u2013 Unsafe: major bleeding/tearing/injury risk",
                       2: "2 \u2013 Safe: minor bleeding/tearing, no injury",
                       3: "3 \u2013 Optimal: no bleeding, tearing, or injury"}),

    # -- case-level: GEARS ------------------------------------------------------
    "gears_depth_perception": ScaleDef(
        level="case", lo=1, hi=5, better="higher", group="GEARS",
        option_labels={1: "1 \u2013 Poor: frequent corrections needed",
                       2: "2 \u2013 Below average", 3: "3 \u2013 Competent: occasional adjustments",
                       4: "4 \u2013 Above average", 5: "5 \u2013 Excellent throughout"}),
    "gears_bimanual_dexterity": ScaleDef(
        level="case", lo=1, hi=5, better="higher", group="GEARS",
        option_labels={1: "1 \u2013 Poor coordination", 2: "2 \u2013 Below average",
                       3: "3 \u2013 Average", 4: "4 \u2013 Above average",
                       5: "5 \u2013 Excellent coordination"}),
    "gears_efficiency": ScaleDef(
        level="case", lo=1, hi=5, better="higher", group="GEARS",
        option_labels={1: "1 \u2013 Excessive movements, poor workflow",
                       2: "2 \u2013 Below average", 3: "3 \u2013 Average",
                       4: "4 \u2013 Above average", 5: "5 \u2013 Excellent economy of motion"}),
    "gears_force_sensitivity": ScaleDef(
        level="case", lo=1, hi=5, better="higher", group="GEARS",
        option_labels={1: "1 \u2013 Frequent excessive force, tissue trauma",
                       2: "2 \u2013 Below average",
                       3: "3 \u2013 Appropriate, occasional over/under",
                       4: "4 \u2013 Above average",
                       5: "5 \u2013 Excellent, consistently appropriate"}),
    "gears_robotic_control": ScaleDef(
        level="case", lo=1, hi=5, better="higher", group="GEARS",
        option_labels={1: "1 \u2013 Poor instrument/camera control",
                       2: "2 \u2013 Below average", 3: "3 \u2013 Average",
                       4: "4 \u2013 Above average", 5: "5 \u2013 Excellent platform control"}),
    "gears_autonomy": ScaleDef(
        level="case", lo=1, hi=5, better="higher", group="GEARS",
        option_labels={1: "1 \u2013 Requires constant guidance",
                       2: "2 \u2013 Requires frequent guidance",
                       3: "3 \u2013 Mostly independent",
                       4: "4 \u2013 Requires minimal guidance",
                       5: "5 \u2013 Fully independent"}),
    "overall_gears": ScaleDef(
        level="case", lo=1, hi=5, better="higher", group="GEARS",
        option_labels={1: "1 \u2013 Poor overall", 2: "2 \u2013 Below average",
                       3: "3 \u2013 Competent", 4: "4 \u2013 Above average",
                       5: "5 \u2013 Expert"}),

    # -- case-level: overall RSS (single item for PEH, unlike Whipple's 3 subitems)
    "overall_rss": ScaleDef(
        level="case", lo=1, hi=3, better="higher", group="RSS",
        option_labels={1: "1 \u2013 Poor overall suturing",
                       2: "2 \u2013 Average overall suturing",
                       3: "3 \u2013 Excellent overall suturing"}),

    # -- stitch-level --------------------------------------------------------
    # Categorical, not ordinal -- `better` is left blank since there's no
    # better-or-worse direction, just which of the 3 anatomic locations.
    "stitch_location": ScaleDef(
        level="stitch", lo=1, hi=3, better="", group="STITCH_LOCATION",
        option_labels={1: "1 \u2013 Posterior",
                       2: "2 \u2013 Anterior right",
                       3: "3 \u2013 Anterior left"}),
    # Same values/labels as PJ/Whipple's yank/curve scales -- just one
    # combined field per stitch instead of separate PD_/J_ versions, since
    # a crural closure stitch has no pancreatic-duct-vs-jejunum distinction.
    "yank_factor": ScaleDef(level="stitch", lo=1, hi=3, better="lower", group="YANK_FACTOR",
                             option_labels=_YANK_LABELS),
    "off_curvature": ScaleDef(level="stitch", lo=1, hi=3, better="lower", group="OFF_CURVATURE",
                               option_labels=_CURVE_LABELS),
    "needle_handling": ScaleDef(
        level="stitch", lo=1, hi=2, better="higher", group="NEEDLE_HANDLING",
        option_labels={1: "1 \u2013 No: inefficient handling / unnecessary tissue contact",
                       2: "2 \u2013 Yes: efficient, no unnecessary contact"}),
    "knot_security": ScaleDef(
        level="stitch", lo=1, hi=2, better="higher", group="KNOT_SECURITY",
        option_labels={1: "1 \u2013 No: insecure or inappropriate tension",
                       2: "2 \u2013 Yes: secure, appropriate tension"}),
    "crural_suturing_skill": ScaleDef(
        level="stitch", lo=1, hi=3, better="higher", group="CRURAL_SUTURING_SKILL",
        option_labels={1: "1 \u2013 Poor: hesitant, tissue trauma, or multiple passes",
                       2: "2 \u2013 Average: minor inefficiencies, single-pass success",
                       3: "3 \u2013 Excellent: efficient, precise, consistent single-pass"}),
    # Same 1-3 scale as crural_suturing_skill, scored per stitch, but
    # assessing knot-tying technique specifically rather than the suture pass.
    "knot_tying_skill": ScaleDef(
        level="stitch", lo=1, hi=3, better="higher", group="KNOT_TYING_SKILL",
        option_labels={1: "1 \u2013 Poor: hesitant, tissue trauma, or multiple attempts",
                       2: "2 \u2013 Average: minor inefficiencies, secure on first attempt",
                       3: "3 \u2013 Excellent: efficient, precise, consistently secure"}),
}

# ---------------------------------------------------------------------------
# Rubric registry -- add a new case type here and it shows up everywhere
# (Preprocessing tab's Case type selector + scoring panel) automatically.
# ---------------------------------------------------------------------------
RUBRICS: dict[str, dict[str, ScaleDef]] = {
    CASE_TYPE_PJ_WHIPPLE: _PJ_WHIPPLE_SCALE_DEFS,
    CASE_TYPE_PEH: _PEH_SCALE_DEFS,
}


def case_level_scales(case_type: str) -> list[str]:
    return [k for k, v in RUBRICS[case_type].items() if v.level == "case"]


def stitch_level_scales(case_type: str) -> list[str]:
    return [k for k, v in RUBRICS[case_type].items() if v.level == "stitch"]


# Backward-compatible aliases (existing code/tests written against the
# original single-rubric names) -- point at PJ/Whipple, the original set.
SCALE_DEFS: dict[str, ScaleDef] = _PJ_WHIPPLE_SCALE_DEFS
CASE_LEVEL_SCALES: list[str] = case_level_scales(CASE_TYPE_PJ_WHIPPLE)
STITCH_LEVEL_SCALES: list[str] = stitch_level_scales(CASE_TYPE_PJ_WHIPPLE)
RSS_SUBITEMS: list[str] = [k for k, v in _PJ_WHIPPLE_SCALE_DEFS.items() if v.group == "RSS"]

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
