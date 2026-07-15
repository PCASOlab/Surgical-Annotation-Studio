# Packaging as a standalone app

This turns the app into something a non-technical lab member can just
double-click -- a `.app` on macOS or a folder with a `.exe` on Windows --
with no Python install required on their end.

## 1. Install build tools

From the repo root, on the machine/OS you want to build *for* (PyInstaller
builds for the OS it's running on -- you can't cross-build a Windows .exe
from macOS or vice versa):

```bash
python3 -m venv .venv
source .venv/bin/activate        # .venv\Scripts\activate on Windows
pip install -r requirements.txt -r requirements-build.txt
```

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
- **macOS**: https://evermeet.cx/ffmpeg/ (separate downloads for `ffmpeg`
  and `ffprobe`; after downloading, run
  `chmod +x resources/ffmpeg/mac/ffmpeg resources/ffmpeg/mac/ffprobe`)
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
python3 packaging/build.py
```

Output goes to `dist/Surgical Annotation Studio/`. On macOS this includes
a `Surgical Annotation Studio.app` you can drag into `/Applications`; on
Windows, `Surgical Annotation Studio.exe` inside that folder.

Zip the whole `dist/Surgical Annotation Studio/` folder to share it --
everything the app needs (including bundled ffmpeg, if you added it) is
inside.

## 4. (Optional) App icon

Drop an icon at `packaging/mac/icon.icns` (macOS) or
`packaging/windows/icon.ico` (Windows) before building and it'll be picked
up automatically.

## Building automatically with GitHub Actions

`.github/workflows/build.yml` builds both a macOS and a Windows package on
every push of a version tag (e.g. `git tag v1.0 && git push --tags`), or
manually via the "Run workflow" button on GitHub's Actions tab. It does
**not** bundle ffmpeg (fetching a specific static build reliably in CI is
brittle since download URLs change) -- if you want a self-contained build
from CI, edit the workflow to download and place the binaries under
`resources/ffmpeg/<platform>/` before the build step, using the sources
above.

## Known limitations

- PyInstaller builds are OS-specific: build on a Mac for macOS, on Windows
  for Windows. There's no cross-compiling.
- Antivirus/Gatekeeper: unsigned builds may get flagged by Windows
  Defender SmartScreen or macOS Gatekeeper on first run. Users can bypass
  this (Windows: "More info" -> "Run anyway"; macOS: right-click -> Open),
  but for wider distribution you'd want a code-signing certificate for
  each OS -- out of scope here.
