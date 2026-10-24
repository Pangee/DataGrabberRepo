---
name: rebuild
description: >
  Rebuild DataGrabber.app with the PyInstaller command in DataGrabber.py and
  put the finished app on the Desktop, trashing any existing Desktop copy
  first. Never touch /Applications/DataGrabber.app. Use when the user says
  "rebuild".
---

# Rebuild

Run the whole rebuild as **one** shell command. Do not split it into separate checks, listings, or follow-up commands. The user approves once.

Do not edit `~/Desktop/DataGrabber.app` unless the user asked for this rebuild. Outside a rebuild, change the project source only. The Desktop app is replaced only by the command below.

Work from the repo root. The PyInstaller output is `dist/DataGrabber.app` inside the project. After a successful build, place that bundle on the Desktop.

If `~/Desktop/DataGrabber.app` already exists, move that Desktop copy to the Trash, then copy the new build there. Do not touch `/Applications/DataGrabber.app` or anything else under `/Applications`.

Build first. Only replace the Desktop app after PyInstaller exits successfully, so a failed build leaves the current Desktop app in place.

Before the old Desktop app is moved to the Trash, copy finished county files from its `Contents/Resources/taxlots_current` into `~/Library/Application Support/DataGrabber/taxlots_current`. Copy a `.sqlite` file only when its `meta` row `finished` is `1`, and check the copy is still finished before the Trash step. Do not copy the bytes' extended attributes. Do not copy `taxlots_update`, `.py` county files, partial files, or unfinished files. If Application Support already has a finished file with the same or a newer stamp, keep that file. Do not put the databases inside the new .app. If that check fails, stop. The current Desktop app stays where it is.

```bash
set -euo pipefail
cd "/Users/danielrohr/Documents/Visual Studios Projects/DataGrabber"
APP_SRC="dist/DataGrabber.app"
DESKTOP_APP="$HOME/Desktop/DataGrabber.app"
APPS_APP="/Applications/DataGrabber.app"
APPS_MTIME_BEFORE=""
if [ -e "$APPS_APP" ]; then
  APPS_MTIME_BEFORE=$(stat -f "%m" "$APPS_APP")
fi

source venv/bin/activate
pip install certifi
pyinstaller --noconfirm --windowed --name "DataGrabber" --osx-bundle-identifier "com.datagrabber.DataGrabber" --icon "DataGrabber.icns" --clean --collect-data certifi DataGrabber.py
python - << 'PY'
import plistlib
from datetime import datetime, timezone
from pathlib import Path

p = Path("dist/DataGrabber.app/Contents/Info.plist")
with p.open("rb") as fh:
    info = plistlib.load(fh)
info["NSHighResolutionCapable"] = True
info["NSSupportsAutomaticGraphicsSwitching"] = True
info["NSSupportsSuddenTermination"] = False
info["NSSupportsAutomaticTermination"] = False
info["LSMinimumSystemVersion"] = "15.0"
info["LSMultipleInstancesProhibited"] = True
info["CFBundleSupportedPlatforms"] = ["MacOSX"]
# Unique per rebuild so a new Desktop .app is a first launch even when
# Application Support already has county files from earlier builds.
info["CFBundleVersion"] = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
with p.open("wb") as fh:
    plistlib.dump(info, fh)
print("INFO_PLIST_OK")
print("CFBundleVersion", info["CFBundleVersion"])
PY
xattr -cr "$APP_SRC"

# Copy finished current-use county files into Application Support BEFORE the
# old Desktop app is trashed. The new .app does not keep the databases inside
# itself. Unfinished files are left behind. taxlots_update is not copied.
# Extended attributes are not copied, so macOS does not ask per file later.
python - << 'PY'
import re
import shutil
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

src_dir = Path.home() / "Desktop/DataGrabber.app/Contents/Resources/taxlots_current"
dst_dir = Path.home() / "Library/Application Support/DataGrabber/taxlots_current"

def header(path):
    uri = "file:" + quote(path.resolve().as_posix(), safe="/") + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=30)
    except sqlite3.Error:
        return None
    try:
        try:
            updated = con.execute("SELECT value FROM meta WHERE key='updated'").fetchone()
            finished = con.execute("SELECT value FROM meta WHERE key='finished'").fetchone()
        except sqlite3.Error:
            return None
    finally:
        con.close()
    if not updated or not finished:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", updated[0]):
        return None
    if finished[0] != "1":
        return None
    return updated[0]

if not src_dir.is_dir():
    print("NO_CURRENT_TAXLOTS")
    raise SystemExit(0)

def dest_for(src):
    name = src.name
    key = name[:-7] if name.endswith(".sqlite") else Path(name).stem
    folder = key.split("-", 1)[0] if "-" in key else key
    return dst_dir / folder / name

finished_files = [
    path for path in src_dir.rglob("*.sqlite") if path.is_file() and header(path)
]
skipped = [
    path.name for path in src_dir.rglob("*.sqlite")
    if path.is_file() and not header(path)
]
if not finished_files:
    print("NO_FINISHED_TAXLOTS")
    if skipped:
        print("SKIPPED_UNFINISHED " + str(len(skipped)))
    raise SystemExit(0)

dst_dir.mkdir(parents=True, exist_ok=True)
copied = 0
kept = 0
for src in finished_files:
    dst = dest_for(src)
    src_stamp = header(src)
    dst_stamp = header(dst) if dst.is_file() else None
    if dst_stamp is not None and dst_stamp >= src_stamp:
        kept += 1
        continue
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    if dst.stat().st_size != src.stat().st_size or header(dst) != src_stamp:
        print(f"ERROR: {src.name} was not finished after copy", file=sys.stderr)
        raise SystemExit(1)
    copied += 1
print(f"COPIED_CURRENT_TAXLOTS {copied}")
print(f"KEPT_NEWER_TAXLOTS {kept}")
if skipped:
    print("SKIPPED_UNFINISHED " + str(len(skipped)))
PY

if [ -e "$DESKTOP_APP" ]; then
  mv "$DESKTOP_APP" "$HOME/.Trash/DataGrabber $(date '+%Y-%m-%d %H%M%S').app"
  echo "TRASHED_OLD_DESKTOP_APP"
else
  echo "NO_EXISTING_DESKTOP_APP"
fi

ditto "$APP_SRC" "$DESKTOP_APP"
xattr -cr "$DESKTOP_APP"

python - << 'PY'
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

support_dir = Path.home() / "Library/Application Support/DataGrabber/taxlots_current"
if not support_dir.is_dir():
    print("SUPPORT_TAXLOTS_NONE")
    raise SystemExit(0)

def is_finished(path):
    uri = "file:" + quote(path.resolve().as_posix(), safe="/") + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=30)
    except sqlite3.Error:
        return False
    try:
        try:
            updated = con.execute("SELECT value FROM meta WHERE key='updated'").fetchone()
            finished = con.execute("SELECT value FROM meta WHERE key='finished'").fetchone()
        except sqlite3.Error:
            return False
    finally:
        con.close()
    if not updated or not finished:
        return False
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", updated[0]):
        return False
    return finished[0] == "1"

count = 0
for path in support_dir.rglob("*.sqlite"):
    if is_finished(path):
        count += 1
if count == 0:
    print("SUPPORT_TAXLOTS_NONE")
    raise SystemExit(0)
print(f"SUPPORT_TAXLOTS_FINISHED {count}")
PY

if [ -n "$APPS_MTIME_BEFORE" ]; then
  APPS_MTIME_AFTER=$(stat -f "%m" "$APPS_APP")
  if [ "$APPS_MTIME_BEFORE" != "$APPS_MTIME_AFTER" ]; then
    echo "ERROR: Applications copy changed" >&2
    exit 1
  fi
  echo "APPLICATIONS_UNTOUCHED $(stat -f "%Sm %N" -t "%Y-%m-%d %H:%M:%S" "$APPS_APP")"
fi

echo "DESKTOP_OK"
ls -ld "$DESKTOP_APP"
```

Tell the user the Desktop path when it finishes. Say whether a previous Desktop copy was trashed. Confirm the Applications bundle was not modified.
