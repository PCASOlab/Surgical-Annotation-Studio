# Surgical Annotation Studio

A desktop app for annotating surgical videos in one place: cut case
videos into clips, label needle/tool keypoints, mark the semantic-state
timeline, and score technical skill -- all without
switching between separate tools.

Procedures:
- Pancreaticojejunostomy (Whipple) - OSATS/RSS/PJ + stitch-level
- Paraesophageal Hernia (PEH) - OSATS/RSS/GEARS + stitch-level

Runs on Windows, macOS and Linux (Ubuntu 22.04 and newer).

**Ready-made downloads** (no Python or ffmpeg needed) are on the
[Releases page](https://github.com/PCASOlab/Surgical-Annotation-Studio/releases):
- **Windows:** unzip the `-windows.zip` and run `Surgical Annotation Studio.exe`.
- **macOS:** use the `-macos-apple-silicon.zip` (M1 or newer) or the
  `-macos-intel.zip`. To open it the first time, see
  [`packaging/README.md`](packaging/README.md#macos-and-linux-builds-with-github-actions).
- **Ubuntu / Linux:** unpack the `-linux-x86_64.tar.gz`, then:
  ```bash
  tar -xzf SurgicalAnnotationStudio-v*-linux-x86_64.tar.gz
  cd SurgicalAnnotationStudio-v*-linux-x86_64
  sudo apt install libxcb-cursor0          # once; Qt needs it on X11
  ./"Surgical Annotation Studio"           # or: ./install-desktop-entry.sh
  ```
  `install-desktop-entry.sh` adds the app to the applications menu.

To run from the source code instead, follow the sections below.

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
1. **Import video file...** to bring a case video into the project. It's
   copied into `<project>/videos/`. To skip the copy, move or copy the
   videos into that folder yourself: they're listed automatically when
   the project opens, or after **Rescan videos folder** if the app is
   already open. If a different video with the same name is already
   there, Import asks whether to replace it or import yours under a new
   name.
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
      | green: `Start ✓ 00:00:14.00` | marked; click it to jump back there |
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

   **Resuming a case.** Selecting a case shows all of its clips, and the
   **Status** column says where each one stands:

   | Status | Meaning |
   |---|---|
   | `• not cut` | in the list, not cut yet |
   | `✂ cut` (grey text) | already cut; its scores come from the score file, for the current rater |
   | `↻ re-cut needed` | cut before, but its start/end changed (or the file is missing) |

   Not-yet-cut clips are saved to `pose/<case_id>/_clip_list_draft.json`
   on every add, update and removal. If the app closes, crashes, or a cut
   fails, select the same case again and they come back. On the seek bar,
   cut clips are solid bands and clips still to cut are lighter.

   **Editing a cut clip.** **Edit selected** works on cut clips too, but
   their stitch ID and type are locked, since they name files and score
   rows that already exist.
   - Change only scores and click **Update**: they're written to the score
     file straight away. Clearing a score removes its row.
   - Change start/end: the clip becomes `↻ re-cut needed` and is cut again,
     with its scores, by the next **Cut all clips**.

   **Remove selected** works on clips that aren't cut yet. On a
   `↻ re-cut needed` clip it discards the change and restores the cut
   version. It never deletes a cut clip; to drop one, delete its file
   from `pose/<case_id>/`.

   **Cut all clips** cuts only clips that are `not cut` or
   `re-cut needed`. It asks first if any of them is a stitch clip with no
   scores. Clips are saved as `pose/<case_id>/<case_id>_<stitch_id>.mp4`.
5. While the video's on screen, score it. Which fields show up depends on
   the selected **Case type**:
   - **PJ / Whipple**: OSATS + RSS subitems case-level; PJ and the
     pancreatic-duct/jejunum yank & curve factors per stitch.
   - **PEH (Paraesophageal Hernia)**: the same OSATS subitems (shared
     across every case type) plus safety/closure/crural-exposure/GEARS
     case-level fields; stitch location, a single yank/curve factor
     (one value per stitch, not separate PD/J ones), needle handling,
     knot security, and crural suturing skill per stitch.

   **Enter your Rater name first.** The scoring dropdowns stay disabled
   until a rater and a case ID are filled in, so every saved score
   records who gave it.

   **Case-level scores save as soon as you pick them**, with no save
   button. Changing a value replaces the saved one, and setting a
   dropdown back to blank removes it. The line under the dropdowns
   confirms each save. When you open a case, or change the rater, the
   dropdowns show what's already saved for that case and rater. Blank
   means nothing is saved.

   **Per-stitch scores** are set before clicking **Add clip** and are
   stored with that clip (stitch clips only), together with the rater's
   name. They're written to the score file when the clips are cut. To
   change them before cutting, use **Edit selected**. The heading above
   the per-stitch fields says whether they apply to the next new clip or
   to the clip you're editing. After adding a clip they reset to blank,
   so a leftover value isn't reused for the next stitch. Only the
   subitems you set get saved; leaving one blank skips it.

   Both kinds go to `clinical/score_entries_<case_id>_rater-<rater>.csv`,
   one file per case and rater, one row per subitem. Case-level rows have
   an empty `video_id_annot` column. Stitch rows have `start`/`stop`, the
   clip's position in the original video.

   **Times everywhere use HH:MM:SS.hh** (hours, minutes, seconds,
   hundredths), e.g. `00:18:03.99`. That applies on screen, in the score
   files and in the semantic spreadsheets. Older score files that stored
   plain seconds (`1083.999`) are converted the next time they're saved.

   **Every CSV also has an Excel copy** with the same name and `.xlsx`.
   This applies to the score files and the keypoint `CollectedData`
   file, and the copy is updated on every save. Open the `.xlsx` in
   Excel: times show as `hh:mm:ss.00` and work as real times (sorting,
   `=stop-start`), and scores and coordinates are numbers. Opening the
   `.csv` directly makes Excel pick its own format, which hides the
   hours. The `.csv` stays the master file for scripts and DeepLabCut.
   If the copy is open in Excel during a save, the CSV is still saved and
   the status bar says the copy wasn't updated. It catches up on the next
   save, when the project is reopened, or with **Refresh** in the
   Clinical tab.

   Adding a new procedure's rubric later is a config change, not a code
   change -- see `core/config.py`'s `RUBRICS` registry.
6. Use the **Zoom** controls or **Ctrl+scroll** to zoom into the video;
   drag the scrollbars to pan. Every video player in the app (this tab,
   Semantic States, Keypoints) also has a **Go to:** field next to Frame
   -- type a timestamp (`1:23.5`, `01:23:45.67`, or plain seconds) and
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
as the Preprocessing tab). Set a **Rater** name first -- nothing is saved
without one -- then:
- **Load existing case-level scores** / **Load existing scores for this
  clip** pulls back whatever's already saved so it can be reviewed.
- Change a value and save again -- it replaces the old value in place
  rather than creating a duplicate row.
- Switching case/clip automatically saves whatever was filled in first,
  so nothing is lost from forgetting to click Save.

Scores are saved to one file per case and rater
(`clinical/score_entries_<case_id>_rater-<rater>.csv`), reviewable in the
Clinical tab.

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

### Share / Export
Packs the project into a zip for another lab member. They unzip it and
choose **Open existing project**.
1. Tick the **cases** to include (all by default).
2. Choose the **options**: whether to include clips not cut yet (on by
   default), and the standardized full-case videos (off: they're a
   640×360 copy of each whole case video).
3. Check the summary. It lists what goes in and what's left out, and
   why, with sizes.
4. Click **Export to zip…** and save it somewhere outside the project
   folder.

**Always included** for the selected cases:
- cut clips
- keypoint images and labels (the shared `CollectedData` CSV is filtered
  to those cases)
- semantic annotations
- score files
- the project settings and clip list
- a `README_EXPORT.txt` describing the export

**Never included:**
- the original videos in `videos/`
- any clinical file other than the score files. An outcomes CSV loaded
  in the Clinical tab can contain patient identifiers.

The clips still show patients' surgery, so share the zip only through
storage your institution approves for that.

Because scores, semantic annotations and keypoints are all stored per
rater or scorer, work done by different people in separate copies can be
combined by copying their files into one project.

---

## Project folder layout

```
<project>/
  videos/            imported case videos
  pose/<case_id>/    standardized video, cut clips (<case_id>_<stitch_id>.mp4),
                     and _clip_list_draft.json while a clip list is unfinished
  labeled-data/      DeepLabCut keypoint labels (CollectedData_<scorer>.csv + .xlsx copy)
                     + images
                     (<case_id>_<stitch_id>/<case_id>_<stitch_id>_imgNNN.png)
  semantic/          semantic-state annotations (one file per case + rater)
  clinical/          score_entries_<case_id>_rater-<rater>.csv (one per case + rater)
                     + .xlsx copy, plus any outcomes CSV loaded in the Clinical tab
  config.yaml        project settings (bodyparts, scorer, target fps/resolution)
```

Different raters' work is kept in separate files (rater name in the
score and semantic filenames, scorer name in the keypoint CSV filename),
so multiple people can annotate the same case without overwriting each
other's work.

Projects from before scores were split per rater
(`score_entries_<case_id>.csv`) are converted automatically the first time
they're opened. The app says which files it converted, and the old files
are kept with a `.migrated` ending.

## Customizing

Everything a lab might want to change lives in `core/config.py`:
keypoint names, the semantic-state list and hotkeys, and every scoring
rubric (labels, scales, and score ranges). Edit it there and every tab
picks up the change automatically.

## Packaging as a standalone app

Want to share this with someone who doesn't have Python installed? See
[`packaging/README.md`](packaging/README.md) to build a double-clickable
`.exe` (Windows), `.app` (macOS) or Linux package with PyInstaller --
including how GitHub builds the macOS and Linux ones automatically. Set up the venv with
`py setup_venv.py --build` (Windows) or `python3 setup_venv.py --build`
to get the packaging tools too.

## Known limitations

- Keypoint labeling is one frame at a time -- no automatic suggestion of
  which frames to label next.
- No built-in training step for DeepLabCut; this app only produces
  labels DeepLabCut can train on.
- No side-by-side comparison view for checking agreement between raters
  yet.
