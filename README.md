# Surgical Annotation Studio

A desktop app for annotating surgical videos in one place: cut case
videos into clips, label needle/tool keypoints, mark the semantic-state
timeline, and score technical skill (OSATS/RSS/PJ) -- all without
switching between separate tools.

Built and most tested on Linux; also runs on macOS and Windows with
ffmpeg installed.

---

## Requirements

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) (for video import/cutting)

## Install

```bash
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
2. **Standardize** it to a consistent frame rate and resolution.
3. Scrub through the video and cut it into clips: set a **Clip type**
   (`stitch` or `knot_tying` -- the Stitch ID field prefills with
   `stitch_`/`knot_` accordingly, just type the rest of the name), mark
   **Set start = playhead** and **Set end = playhead**, then
   **Add to clip list**. Repeat for every clip in the case, then
   **Cut all clips**.
4. While the video's on screen, score it. All scoring dropdowns start
   blank so it's obvious what hasn't been entered yet, and reset back to
   blank after saving/adding so you don't accidentally reuse a leftover
   value for the next case or stitch:
   - **Case-level**: OSATS and RSS subitem dropdowns, entered once per
     case with **Save case-level scores**. Only the subitems you actually
     set a value for get saved -- leaving one blank just skips it rather
     than saving a meaningless value.
   - **Per-stitch**: PJ, and the yank/curve factors for the
     pancreatic-duct and jejunum passes -- set these before clicking
     **Add to clip list** so they're saved with that clip (stitch clips
     only).
5. Use the **Zoom** controls or **Ctrl+scroll** to zoom into the video;
   drag the scrollbars to pan.

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
  clinical/          clinical CSV + score_entries.csv
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
