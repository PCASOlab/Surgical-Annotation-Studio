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

The app runs from a virtual environment (venv), so its packages are
installed into the same Python that runs it. `setup_venv.py` creates the
venv for you and picks the right Python:

**Windows (PowerShell or cmd):**

```powershell
cd Downloads\Surgical-Annotation-Studio\       # or however it is named/extracted from zip
py setup_venv.py
```

**macOS / Linux:**

```bash
cd Downloads/Surgical-Annotation-Studio/       # or however it is named/extracted from zip
python3 setup_venv.py
```

When it finishes, it prints the exact command to activate the venv.
Run that command once in each new terminal before starting the app.

On Windows, `python`, `python3` and `py` can each start a different
Python, for example a python.org install and the Microsoft Store one.
The script doesn't depend on which one you used to start it:
- It lists every installed Python and picks a 64-bit python.org install,
  3.10 or newer. It skips the Microsoft Store Python, which PyInstaller
  doesn't work well with.
- If no suitable Python is installed, it says so and links to the
  python.org download. Python 3.12 is a good choice.
- If the folder is too deep for Windows' 260-character path limit, which
  PySide6's long file paths can hit, it creates the venv in
  `%USERPROFILE%\.venvs\` instead of the project folder.

Options: `--build` also installs the packaging tools,
`--recreate` rebuilds an existing venv, and `--python <exe>` or
`--venv <path>` override the choices it makes.

<details>
<summary>Setting up the venv by hand instead</summary>

```bash
python3 -m venv .venv                  # Windows: py -3.12 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

On Windows, check `py -0p` first and pick a python.org install, not the
Microsoft Store one.
</details>

If PowerShell refuses to run `Activate.ps1` ("running scripts is
disabled"), run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
once and try again, or use the `activate.bat` command in cmd.exe.

Once the venv is active, `python` refers to the venv's interpreter on
every OS. Use `python` for all the commands below.

Then install ffmpeg for your OS:

| OS | Command |
|---|---|
| Linux | `sudo apt install ffmpeg` (or your distro's equivalent) |
| macOS | `brew install ffmpeg` ([Homebrew](https://brew.sh)) |
| Windows | Download a build from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) ("release essentials"), unzip, add its `bin/` folder to PATH |

If ffmpeg isn't found, the app's Preprocessing tab shows a warning with
the right command for whatever OS you're actually running on.

## Run

With the venv activated:

```bash
python main.py
```

This opens a dialog to **create a new project** (pick an empty folder) or
**open an existing one**. To skip the dialog:

```bash
python main.py /path/to/project
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
4. Scrub through the video and mark each clip:
   1. Pick a **Clip type** (`stitch` or `knot_tying`). The **Stitch ID**
      field prefills with `stitch_` or `knot_`, so type only the rest,
      e.g. `01`. Stitch IDs don't have to follow time order.
   2. Press **I** (**Set start**) at the first frame and **O**
      (**Set end**) at the last. The two chips next to the buttons show
      what's set:

      | Chip | Meaning |
      |---|---|
      | grey, dashed: `Start: not set` | not marked yet |
      | green: `Start ✓ 00:00:14.000` | marked; click it to jump back there |
      | red: `End ✗ …` | end is before start; set one of them again |

      The line under the chips always says what to do next and shows the
      clip's length once both ends are marked.
   3. Set the per-stitch scores (see step 5), then press **Enter** in the
      Stitch ID field, **Ctrl+Enter** anywhere in the tab, or click
      **Add clip**.

   The app checks each clip before adding it. It rejects a Stitch ID
   that's only the prefix (`stitch_`) or that's already in the list. It
   asks before you add a clip that overlaps another clip of the same
   type, or one whose file was already cut. If you type the case ID in
   front of the stitch ID, it's removed, since the app adds it anyway.

   **The clip list.** Clips are listed in start-time order:
   - The row of any clip the playhead is inside is highlighted.
   - Double-click a row to jump to that clip's start.
   - The video's seek bar shows every clip as a band in the same
     highlight color, and the range you're marking in green.

   **Editing a clip.** Select a row and click **Edit selected**. Its ID,
   type, start/end and scores load into the fields above, the clip turns
   orange on the seek bar, and a yellow line says which clip you're
   editing. Change anything, re-mark start/end if needed, and click
   **Update** (or press Enter), or **Cancel edit** to discard. If you were
   halfway through entering a new clip, it's set aside while you edit and
   comes back afterwards, scores included.

   Buttons that don't apply right now are greyed out. For example, **Add
   clip** turns on only once a valid start and end are set, and **Cut
   all** turns off while you're editing.

   **The list is saved as you go.** Every add, update and removal is
   saved to `pose/<case_id>/_clip_list_draft.json`. If the app closes,
   crashes, or a cut fails, select the same case again and the list comes
   back. It's cleared only for clips that were cut successfully.

   When the list is complete, click **Cut all clips**. It asks first if
   any stitch clip has no scores. Clips are saved as
   `pose/<case_id>/<case_id>_<stitch_id>.mp4`.
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
   scores**. Per-stitch scores are set before clicking **Add clip**, so
   they're stored with that clip (stitch clips only). To change them
   later, use **Edit selected**. The heading above the per-stitch fields
   says whether they apply to the next new clip or to the clip you're
   editing.

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

Labeled frames are saved under `labeled-data/<case_id>_<stitch_id>/` as
`<case_id>_<stitch_id>_imgNNN.png`, so every image file says which case
and stitch it came from.

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
  pose/<case_id>/    standardized video, cut clips (<case_id>_<stitch_id>.mp4),
                     and _clip_list_draft.json while a clip list is unfinished
  labeled-data/      DeepLabCut keypoint labels + images
                     (<case_id>_<stitch_id>/<case_id>_<stitch_id>_imgNNN.png)
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
have GitHub build both automatically for you. Set up the venv with
`py setup_venv.py --build` (Windows) or `python3 setup_venv.py --build`
to get the packaging tools too.

## Known limitations

- Keypoint labeling is one frame at a time -- no automatic suggestion of
  which frames to label next.
- No built-in training step for DeepLabCut; this app only produces
  labels DeepLabCut can train on.
- No side-by-side comparison view for checking agreement between raters
  yet.
