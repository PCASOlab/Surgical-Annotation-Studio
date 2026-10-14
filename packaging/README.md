# Packaging as a standalone app

This turns the app into something a non-technical lab member can just
double-click -- a `.app` on macOS or a folder with a `.exe` on Windows --
with no Python install required on their end.

## 1. Install build tools

From the repo root, on the machine/OS you want to build *for* (PyInstaller
builds for the OS it's running on -- you can't cross-build a Windows .exe
from macOS or vice versa):

```bash
py setup_venv.py --build          # Windows
python3 setup_venv.py --build     # macOS
```

Then activate the venv with the command it prints. `--build` adds
PyInstaller on top of the app's own packages.

The script avoids the two Windows problems that break the build: it uses
a python.org Python rather than the Microsoft Store one (PyInstaller has
trouble with the Store version's sandboxed paths), and it moves the venv
to a short path if the repo folder is too deep for PySide6's long file
paths. `build.py` also refuses to run under the Store Python. The main
README has details and manual setup steps.

With the venv active, use `python` for the commands below on both OSes.

## 2. (Optional but recommended) Bundle ffmpeg

The app shells out to `ffmpeg`/`ffprobe` for video import/cutting. Without
this step, whoever runs the packaged app still needs ffmpeg installed
separately and on their PATH. To avoid that, download a static ffmpeg
build and drop the binaries in:

```
resources/ffmpeg/mac/ffmpeg          resources/ffmpeg/mac/ffprobe
resources/ffmpeg/windows/ffmpeg.exe  resources/ffmpeg/windows/ffprobe.exe
```

Trusted sources for static (no-install-needed) builds:
- **macOS**: https://ffmpeg.martin-riedl.de/ (separate `ffmpeg` and
  `ffprobe` zips; pick **arm64** for Apple Silicon or **amd64** for Intel
  to match the Mac you build on; after unzipping, run
  `chmod +x resources/ffmpeg/mac/ffmpeg resources/ffmpeg/mac/ffprobe`).
  evermeet.cx, used before, no longer serves downloads.
- **Windows**: https://www.gyan.dev/ffmpeg/builds/ (grab the "release
  essentials" build, then copy `bin/ffmpeg.exe` and `bin/ffprobe.exe`)

These binaries are only used at build time (`--add-data` bundles them into
`dist/`) -- don't commit them to git, they're large and this repo's
`.gitignore` already excludes `resources/ffmpeg/`.

If you skip this step, the build still works fine -- just document to
your users that they need `ffmpeg` installed (`brew install ffmpeg` /
download from gyan.dev and add to PATH).

## 3. Build

```bash
python packaging/build.py
```

Output goes to `dist/Surgical Annotation Studio/`. On macOS this includes
a `Surgical Annotation Studio.app` you can drag into `/Applications`; on
Windows, `Surgical Annotation Studio.exe` inside that folder.

To share it on Windows, zip the whole `dist/Surgical Annotation Studio/`
folder: everything the app needs is inside, including bundled ffmpeg if
you added it. Name the zip after the version, e.g.
`SurgicalAnnotationStudio-v1.2-windows.zip`. The version also appears in
the app's window title; it's set in `core/version.py`, so update it
before building a new release.

## 4. (Optional) App icon

Drop an icon at `packaging/mac/icon.icns` (macOS) or
`packaging/windows/icon.ico` (Windows) before building and it'll be picked
up automatically.

## macOS builds with GitHub Actions

A Mac app has to be built on a Mac, so `.github/workflows/build.yml` builds
it on GitHub's Mac machines. There are two builds, **Apple Silicon** (M1
and later) and **Intel**, and each one:
1. downloads the matching static ffmpeg/ffprobe (and signs them, since
   Apple Silicon refuses unsigned programs),
2. runs `packaging/build.py`,
3. checks that the app starts and that its bundled ffmpeg runs, and
4. zips `Surgical Annotation Studio.app` as
   `SurgicalAnnotationStudio-<tag>-macos-apple-silicon.zip` / `...-intel.zip`.

It runs when a version tag is pushed (`git tag v1.2 && git push origin
v1.2`) and attaches both zips to a **draft** release for that tag. Add the
Windows zip and the release notes to the draft, then publish it. Wait
for the workflow to finish before creating the release yourself, or the
zips land on a second draft. The workflow can also be run by hand from the
Actions tab ("Run workflow"); the zips are then under that run's
Artifacts.

**Mac users opening the app the first time:** it isn't signed with an
Apple developer certificate, so macOS blocks it.
1. Unzip it and move `Surgical Annotation Studio.app` to Applications.
2. Double-click it; macOS refuses to open it.
3. Open System Settings → Privacy & Security, scroll down and click
   **Open Anyway** next to the app's name.

If video import or cutting then reports ffmpeg as missing or blocked, run
this once in Terminal:
`xattr -dr com.apple.quarantine "/Applications/Surgical Annotation Studio.app"`

## Known limitations

- PyInstaller builds are OS-specific: build on a Mac for macOS, on Windows
  for Windows. There's no cross-compiling.
- Antivirus/Gatekeeper: unsigned builds get flagged by Windows Defender
  SmartScreen or macOS Gatekeeper on first run. Users can bypass this
  (Windows: "More info" -> "Run anyway"; macOS: see above), but for wider
  distribution you'd want a code-signing certificate for each OS -- out of
  scope here.
- The Mac builds are made and checked on GitHub's Mac machines only; they
  haven't been tried on a lab Mac yet.
