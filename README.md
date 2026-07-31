# Surgical Annotation Studio

A desktop app for annotating surgical videos in one place: cut case
videos into clips, label needle/tool keypoints, mark the semantic-state
timeline, and score technical skill -- all without
switching between separate tools.

Procedures:
- Pancreaticojejunostomy (Whipple) - OSATS/RSS/PJ + stitch-level
- Paraesophageal Hernia (PEH) - OSATS/RSS/GEARS + stitch-level

Built and most tested on Linux; also runs on macOS and Windows with
ffmpeg installed.

---

## Requirements

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) (for video import/cutting)

## Install

```bash
cd Downloads/Surgical-Annotation-Studio/       # or however it is named/extracted from zip
python3 -m venv .venv
source .venv/bin/activate       # .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

Then install ffmpeg for your OS:

| OS | Command |
|---|---|
| Linux | `sudo apt install ffmpeg` (or your distro's equivalent) |
| macOS | `brew install ffmpeg` ([Homebrew](https://brew.sh)) |
| Windows | Download a build from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) ("release essentials"), unzip, add its `bin/` folder to PATH |

If ffmpeg isn't found, the app's Preprocessing tab shows a warning with
the right command for whatever OS you're actually running on.

## Run

```bash
python3 main.py
```

This opens a dialog to **create a new project** (pick an empty folder) or
**open an existing one**. To skip the dialog:

```bash
python3 main.py /path/to/project
```

The app opens maximized. Press **F11** for fullscreen, **Ctrl+M** to
toggle maximize/restore, **Esc** to exit fullscreen.

---

## The workflow

Work through the tabs left to right for each case:

### Dashboard
See every case in the project and how much work is done on each
(clips cut, keypoints labeled, semantic annotations saved). Click
**Rescan project** if files were added outside the app.

### 1. Preprocessing
1. **Import video file...** to bring a case video into the project.
2. Enter a **Case ID** (auto-filled from the video's filename -- editing
   it manually is never overwritten by picking a different video later)
   and pick a **Case type**. This selects which scoring rubric applies to
   the whole case (see below); it's remembered per case, so reopening a
   case later automatically shows the same rubric again.
3. **Standardize** it to a consistent frame rate and resolution.
4. Scrub through the video and cut it into clips: set a **Clip type**
   (`stitch` or `knot_tying` -- the Stitch ID field prefills with
   `stitch_`/`knot_` accordingly, just type the rest of the name), mark
   **Set start = playhead** and **Set end = playhead**, then
   **Add to clip list**. Repeat for every clip in the case, then
   **Cut all clips**.
5. While the video's on screen, score it. Which fields show up depends on
   the selected **Case type**:
   - **PJ / Whipple**: OSATS + RSS subitems case-level; PJ and the
     pancreatic-duct/jejunum yank & curve factors per stitch.
   - **PEH (Paraesophageal Hernia)**: the same OSATS subitems (shared
     across every case type) plus safety/closure/crural-exposure/GEARS
     case-level fields; stitch location, a single yank/curve factor
     (one value per stitch, not separate PD/J ones), needle handling,
     knot security, and crural suturing skill per stitch.

   All scoring dropdowns start blank so it's obvious what hasn't been
   entered yet, and reset back to blank after saving/adding so you don't
   accidentally reuse a leftover value for the next case or stitch. Only
   the subitems you actually set a value for get saved -- leaving one
   blank just skips it. Case-level scores save with **Save case-level
   scores**; per-stitch scores are set before clicking **Add to clip
   list** so they're captured with that clip (stitch clips only).

   Adding a new procedure's rubric later is a config change, not a code
   change -- see `core/config.py`'s `RUBRICS` registry.
6. Use the **Zoom** controls or **Ctrl+scroll** to zoom into the video;
   drag the scrollbars to pan. Every video player in the app (this tab,
   Semantic States, Keypoints) also has a **Go to:** field next to Frame
   -- type a timestamp (`1:23.5`, `01:23:45.678`, or plain seconds) and
   press Enter to jump there instead of only scrubbing.

### 1.5 Existing Clips
For clips that are already cut and sitting in `pose/<case_id>/*.mp4` --
e.g. a whole PEH dataset where every stitch has already been isolated --
and just need clinical scores assigned, without re-running the
Preprocessing tab's import/cut workflow. No registration step needed --
if the clip file is there, it shows up here.

Laid out like the Semantic States tab: pick a **Case** and a
**Stitch/clip**, watch the video at the top, then fill in scores below --
**Case-level** (once per case) and/or **Scores for this clip**
(stitch-level), with fields depending on the **Case type** (same rubrics
as the Preprocessing tab). Set a **Rater** name, then:
- **Load existing case-level scores** / **Load existing scores for this
  clip** pulls back whatever's already saved so it can be reviewed.
- Change a value and save again -- it replaces the old value in place
  rather than creating a duplicate row.
- Switching case/clip automatically saves whatever was filled in first,
  so nothing is lost from forgetting to click Save.

Each case's scores are saved to their own file
(`clinical/score_entries_<case_id>.csv`), reviewable in the Clinical tab.

### 2. Keypoints (DLC)
Pick a case and clip, then click to place each keypoint in order (shown
on the right). Drag a point to adjust it, right-click to remove it.
Turn on **Track tools too** to also label the two tool tips and the tool
crotch. Points save automatically as you place, adjust, or delete them --
there's no separate save step, so nothing is lost if you move to the next
frame without thinking about it ("Force re-save" is there only for peace
of mind). Zoom with the controls next to it; middle-click-drag to pan
while zoomed in.

### 3. Semantic States
Pick a case and clip, enter your name as **Rater**, then scrub the video
and press **1-9** at the exact moment each state begins. Use
**Validate quality checks** before saving to catch ordering mistakes.
**Save** writes the annotation; **Load existing annotation** brings a
previous save back in for review or edits.

Switching to a different case or clip automatically saves whatever
annotation was on screen first (as long as a **Rater** name is set --
that's what the saved file is named after). The only way to lose
in-progress work is switching clips with no Rater name entered, which
pops an explicit warning rather than discarding anything silently.

### 4. Clinical
The **Case** selector lists every case in the project (scored or cut into
clips), whether or not you've loaded an outcomes CSV -- pick one to see
its scores on the right, or check **Show all cases** to see everything at
once. **Load clinical CSV...** additionally shows read-only outcome
context (patient identifiers are never shown) for cases present in that
CSV. The table on the right shows every score you've entered from the
Preprocessing tab -- this tab is for reviewing, not entering scores.

---

## Project folder layout

```
<project>/
  videos/            imported case videos
  pose/<case_id>/    standardized video + cut clips
  labeled-data/       DeepLabCut keypoint labels + images
  semantic/          semantic-state annotations (one file per case + rater)
  clinical/          clinical CSV + score_entries_<case_id>.csv (one per case)
  config.yaml        project settings (bodyparts, scorer, target fps/resolution)
```

Different raters' work is kept in separate files (rater name in the
semantic filename, scorer name in the keypoint CSV filename), so multiple
people can annotate the same case without overwriting each other's work.

## Customizing

Everything a lab might want to change lives in `core/config.py`:
keypoint names, the semantic-state list and hotkeys, and every scoring
rubric (labels, scales, and score ranges). Edit it there and every tab
picks up the change automatically.

## Packaging as a standalone app

Want to share this with someone who doesn't have Python installed? See
[`packaging/README.md`](packaging/README.md) to build a double-clickable
`.app` (macOS) or `.exe` (Windows) with PyInstaller -- including how to
have GitHub build both automatically for you.

## Known limitations

- Keypoint labeling is one frame at a time -- no automatic suggestion of
  which frames to label next.
- No built-in training step for DeepLabCut; this app only produces
  labels DeepLabCut can train on.
- No side-by-side comparison view for checking agreement between raters
  yet.
