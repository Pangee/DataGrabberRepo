---
name: rebuild
description: >
  Rebuild DataGrabber as a Desktop installer DMG with the PyInstaller command
  in DataGrabber.py. Trash any existing Desktop .app or .dmg first. Never
  touch /Applications/DataGrabber.app. Use when the user says "rebuild".
---

# Rebuild

Run the whole rebuild as **one** shell command. Do not split it into separate checks, listings, or follow-up commands. The user approves once.

Do not edit `~/Desktop/DataGrabber.app` or `~/Desktop/DataGrabber.dmg` unless the user asked for this rebuild. Outside a rebuild, change the project source only. The Desktop installer is replaced only by the command below.

Work from the repo root. The PyInstaller output is `dist/DataGrabber.app` inside the project. After a successful build, wrap that bundle in a drag-to-Applications disk image and put **DataGrabber.dmg** on the Desktop. Do not leave DataGrabber.app on the Desktop.

If `~/Desktop/DataGrabber.app` or `~/Desktop/DataGrabber.dmg` already exists, move that Desktop copy to the Trash, then copy the new installer there. Do not touch `/Applications/DataGrabber.app` or anything else under `/Applications`.

Build first. Only replace the Desktop installer after PyInstaller exits successfully, so a failed build leaves the current Desktop installer in place.

Before the old Desktop app is moved to the Trash, copy finished county files from its `Contents/Resources/taxlots_current` into `~/Library/Application Support/DataGrabber/taxlots_current`. Copy a `.sqlite` file only when its `meta` row `finished` is `1`, and check the copy is still finished before the Trash step. Do not copy the bytes' extended attributes. Do not copy `taxlots_update`, `.py` county files, partial files, or unfinished files. If Application Support already has a finished file with the same or a newer stamp, keep that file. Do not put the databases inside the new .app. If that check fails, stop. The current Desktop installer stays where it is.

Copy R2 access into `Contents/Resources/r2.json` of the new .app so another Mac can download county files without rclone. Prefer `~/Library/Application Support/DataGrabber/r2.json`, then `rclone.conf` `[r2]`. Do not print the keys. If no keys are found, stop so a Release is not shipped without cloud access.

Read the marketing version from the repo file `VERSION`. Put it in `CFBundleShortVersionString`, `CFBundleVersion`, and `Contents/Resources/VERSION.txt`. Do not edit `VERSION` during a rebuild. The bundle display name stays `DataGrabber`; the .app stays `DataGrabber.app`.

The DMG window is the usual drag-to-Applications installer: DataGrabber.app on the left, Applications on the right. Opening the app from Applications later throws the Desktop/Downloads `.dmg` in the Trash.

```bash
set -euo pipefail
cd "/Users/danielrohr/Documents/Visual Studios Projects/DataGrabber"
APP_SRC="dist/DataGrabber.app"
DESKTOP_APP="$HOME/Desktop/DataGrabber.app"
DESKTOP_DMG="$HOME/Desktop/DataGrabber.dmg"
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
info["CFBundleName"] = "DataGrabber"
info["CFBundleDisplayName"] = "DataGrabber"
version = Path("VERSION").read_text(encoding="utf-8").strip()
if not version:
    raise SystemExit("ERROR: VERSION file is empty")
# Marketing version from VERSION. Rebuild must not bump that file.
info["CFBundleShortVersionString"] = version
info["CFBundleVersion"] = version
info["CFBundleGetInfoString"] = f"DataGrabber {version}"
with p.open("wb") as fh:
    plistlib.dump(info, fh)
note = Path("dist/DataGrabber.app/Contents/Resources/VERSION.txt")
note.write_text(f"DataGrabber {version}\n", encoding="utf-8")
print("INFO_PLIST_OK")
print("CFBundleShortVersionString", version)
print("CFBundleVersion", version)
PY
python - << 'PY'
# Ship R2 read keys inside the .app (like certifi) so another Mac can
# download county files with a double-click. Keys stay out of git.
import json
import sys
from pathlib import Path

def from_r2_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    access = data.get("access_key_id") or data.get("accessKeyId")
    secret = data.get("secret_access_key") or data.get("secretAccessKey")
    if access and secret:
        return str(access), str(secret)
    return None

def from_rclone(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    section = None
    access = None
    secret = None
    found = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            if section == "r2" and access and secret:
                found = (access, secret)
            section = line[1:-1].strip()
            access = None
            secret = None
            continue
        if section != "r2" or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key == "access_key_id":
            access = value
        elif key == "secret_access_key":
            secret = value
    if section == "r2" and access and secret:
        found = (access, secret)
    return found

home = Path.home()
creds = from_r2_json(home / "Library/Application Support/DataGrabber/r2.json")
if creds is None:
    creds = from_rclone(home / ".config/rclone/rclone.conf")
if creds is None:
    creds = from_rclone(home / ".rclone.conf")
if creds is None:
    print("ERROR: R2_CREDS_MISSING", file=sys.stderr)
    raise SystemExit(1)
out = Path("dist/DataGrabber.app/Contents/Resources/r2.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(
    json.dumps({"access_key_id": creds[0], "secret_access_key": creds[1]}) + "\n",
    encoding="utf-8",
)
print("R2_CREDS_BUNDLED")
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
if [ -e "$DESKTOP_DMG" ]; then
  mv "$DESKTOP_DMG" "$HOME/.Trash/DataGrabber $(date '+%Y-%m-%d %H%M%S').dmg"
  echo "TRASHED_OLD_DESKTOP_DMG"
else
  echo "NO_EXISTING_DESKTOP_DMG"
fi

# Drag-to-Applications installer. The .app stays inside the DMG, not on Desktop.
STAGE="$(mktemp -d /tmp/datagrabber-dmg.XXXXXX)"
RW_DMG="$(mktemp /tmp/datagrabber-rw.XXXXXX).dmg"
cleanup_dmg() {
  hdiutil detach "/Volumes/DataGrabber" -force >/dev/null 2>&1 || true
  rm -rf "$STAGE"
  rm -f "$RW_DMG"
}
trap cleanup_dmg EXIT
ditto "$APP_SRC" "$STAGE/DataGrabber.app"
ln -s /Applications "$STAGE/Applications"
mkdir -p "$STAGE/.background"
if [ -f "dmg-background.png" ]; then
  cp "dmg-background.png" "$STAGE/.background/background.png"
fi
hdiutil create -volname "DataGrabber" -srcfolder "$STAGE" -ov -fs HFS+ -format UDRW "$RW_DMG"
hdiutil attach -readwrite -noverify -noautoopen "$RW_DMG"
n=0
while [ ! -d "/Volumes/DataGrabber/DataGrabber.app" ]; do
  n=$((n + 1))
  if [ "$n" -gt 40 ]; then
    echo "ERROR: DMG did not mount" >&2
    exit 1
  fi
  sleep 0.25
done
if [ -f "/Volumes/DataGrabber/.background/background.png" ]; then
  osascript << 'APPLESCRIPT' || true
tell application "Finder"
  tell disk "DataGrabber"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {200, 120, 888, 520}
    set theViewOptions to the icon view options of container window
    set arrangement of theViewOptions to not arranged
    set icon size of theViewOptions to 128
    set background picture of theViewOptions to file ".background:background.png"
    set position of item "DataGrabber.app" of container window to {160, 200}
    set position of item "Applications" of container window to {520, 200}
    close
    open
    update without registering applications
    delay 2
  end tell
end tell
APPLESCRIPT
fi
bless --folder "/Volumes/DataGrabber" --openfolder "/Volumes/DataGrabber" || true
sync
hdiutil detach "/Volumes/DataGrabber"
hdiutil convert "$RW_DMG" -format UDZO -imagekey zlib-level=9 -o "$DESKTOP_DMG"
xattr -cr "$DESKTOP_DMG"
trap - EXIT
cleanup_dmg

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

if [ -e "$DESKTOP_APP" ]; then
  echo "ERROR: Desktop still has DataGrabber.app" >&2
  exit 1
fi
echo "DESKTOP_DMG_OK"
ls -ld "$DESKTOP_DMG"
```

Tell the user the Desktop **DataGrabber.dmg** path when it finishes. Say whether a previous Desktop app or installer was trashed. Confirm the Applications bundle was not modified. Tell them to double-click the disk image and drag DataGrabber into Applications.
