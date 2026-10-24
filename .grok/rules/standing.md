# DataGrabber standing

- Source-only unless the user says rebuild (`rebulid` counts). Then run `.grok/skills/rebuild/SKILL.md` as one command.
- Rebuild puts `~/Desktop/DataGrabber.dmg` on the Desktop. Do not leave `DataGrabber.app` on the Desktop. Never touch `/Applications/DataGrabber.app`.
- Marketing version lives in the repo file `VERSION` (now `1.0`). Rebuild copies that into the app. Do not bump `VERSION` on a test rebuild. Bump it only when the user asks for a GitHub Release: `1.0` → `1.1` → `1.2`. Use `2.0` only if the app is remade from scratch. The .app stays `DataGrabber.app`, so installing any version Replace-overwrites the one in Applications.
- The .app is always named `DataGrabber.app` with display name DataGrabber, so Finder Replace in Applications still works. Version is on the GitHub Release and in `Contents/Resources/VERSION.txt`.
- Never upload to Cloudflare/R2. The rclone copy line in the DataGrabber.py docstring is for the user in Terminal. The app ships read-only `r2.json`. Create a GitHub Release only when the user asks for an official release.
- County files live in `~/Library/Application Support/DataGrabber/taxlots_current/{stateToken}/{stateToken}-{countyToken}.sqlite`. R2 is fill-in only: never replace an existing finished currently-using file. ArcGIS auto-update is the only refresher. Auto-update defaults off.
- Launch fill: Cloudflare first (silent if missing or unfinished on R2), then ArcGIS for leftovers. After ArcGIS fails: `{name} county could not be downloaded, its ArcGIS taxlot database is currently down.`
- Installer: double-click the DMG, drag into Applications (Replace if it exists), open from Applications. That launch trashes the Desktop/Downloads installer.
- Never touch `Backup/` at the project root.
