# Media Sorter

**Version** 0.7.0 (project panel, stacks, fast viewer) · **Date** 2026-11-20 · **Status** Working; AI tags are a first pass, remote access not yet switched on · **Supersedes** 0.6.0

Scans photo and video folders, scores each file 0–5, finds exact duplicates, keeps
shots taken together in one group, and gives you a dashboard to search, tag, rate,
bulk-edit, and move rejects to a review folder. It never deletes on its own.

## Install it

Each computer runs its own copy with its own photos and its own tags; the copies do not share anything.

- **Windows:** download `install.bat` from https://github.com/10xequity/media-sorter and double-click it.
  It installs into `%LOCALAPPDATA%\MediaSorter` with its own Python and ffmpeg (nothing else on the computer changes),
  puts a "Media Sorter" shortcut on the Desktop and Start menu, and opens the dashboard.
- **Mac** (Apple M-series chip only): download `install.command` and double-click it. If the Mac refuses to open it,
  go to System Settings, Privacy & Security, Open Anyway. It installs into `~/Applications/MediaSorter`.

Running the installer again repairs or updates an install; the library, settings and AI models are kept.
The first "Run AI" downloads the AI models (about 1.7 GB).

## Start it

Use the Media Sorter shortcut (or `run.bat` / `run.command`). It runs in the background with no window and opens
http://localhost:8765. "Quit app" in the dashboard stops it. Problems are written to `data/log.txt`.

## The screen

- **Left:** the library (sections, tags, people, events). Drag its bottom-right corner to widen it; long names wrap.
- **Middle:** the photos. A sequence (burst) shows as one stacked tile with a count; select it to see every photo in it,
  and use **Back** to return. Untick "Stack sequences" to see every file. Open a photo and it shows at once; the sharp
  version follows, and the next photos are loaded ahead. Close with the X or Esc.
- **Right: the Project.** Tick photos, or drag them over, from any folder or search; they stay collected while you
  browse and after a restart. Drag (or use the arrows) to put them in order, then **Make a video** (it follows that
  order and you can name the file) or **Export copies**.
- **Random pick** adds a random handful from whatever is on screen to the project (optionally narrowed by a word such
  as "hitting"), or shows one random shot at a time.

## Folders, drives and scanning

- **Folders** (header button) picks which folders are scanned, from a tree of this computer's drives.
- On start-up the app lists what is new or changed in those folders and asks before scanning.
- Each folder's drive is remembered by its serial number. If a drive comes back under a different letter, the
  library follows it: tags, scores and people are kept. If a drive is unplugged, its files stay in the library
  (shown from memory) and are never marked missing.
- Every scan is recorded; the history is at the bottom of the Folders dialog.

## Updates

When a newer version is published, an **Update to x.y.z** button appears in the header. It downloads the new program
files from GitHub, backs up the old ones in `data/backup/`, and restarts. Settings (`config.json`), the library
(`data/`), AI models and exports are never touched. Without internet: drag a Media Sorter zip onto `update.bat`.

To publish a version (from the development copy): raise the number in `VERSION`, commit, and push to `main`.

`python sorter.py --selftest`, `python ai.py --selftest`, `python reel.py --selftest` and `python update.py --selftest` run the built-in checks.
`python sorter.py --rescore` recalculates scores after you change `scoring` in `config.json`.

## The AI pass

Select "Run AI" in the dashboard, or run `python ai.py`. It only looks at files it has not seen, so
re-running is quick. Everything runs on this computer. On the first run it downloads about 1.8 GB
of models into `models/` (from Hugging Face and Google's model storage); after that it needs no internet.

| It adds | How to use it |
|---|---|
| Tags for what is in the shot (hitting, serving, team photo, crowd, beach...) | "What the AI sees" in the left rail. The wording lives under `ai.tags` in `config.json`. |
| A suggested category, only where you have not set one | Categories in the left rail |
| Search by description | Type what you want ("player diving for the ball"). If no name or tag matches, it shows the closest pictures by look. Sort by "Closest to what I typed" to force it. |
| People | Faces that keep showing up are grouped as "person 1", "person 2"... Open one and select "Rename this person". Reusing a name merges two groups. |
| Eyes look closed | Listed under "Needs a decision"; nothing is moved. |
| Video dead time | The viewer lists stretches with no movement or a covered lens; select one to jump there. |
| Near-duplicates | A resized or re-exported copy is flagged as a duplicate of the largest version. This part needs no AI and runs with every scan. |

## Marking photos you have already posted

Put the pictures from an account into a folder (Instagram's own "Download your information" export works),
then run `python ai.py --posted "<folder>" <account name>`. Library photos that appear in that folder get a blue
"Posted" badge and the tags `posted` and `posted <account name>`; "Already posted" appears in the left rail.
Cropped, resized and recompressed copies still match. In a test on 48 simulated Instagram crops it found 46,
and wrongly marked one neighbouring burst frame. Edited versions of the same photo are marked too.

Limits, so you know what to trust:
- Tags come from a general-purpose picture model, not one trained on volleyball. It is reliable for broad
  things (team photo, crowd, beach, indoor) and rough for telling a hit from a block.
- People are grouped only from large, clear faces, and a person needs 3 photos to get a group. This is deliberately strict:
  on this library it named 336 of 3,960 faces, in 80 groups. Looser settings grouped by pose and sunglasses instead of by person.
- "Eyes look closed" is fooled by sunglasses and by people looking down.
  Photos only, not videos. Renaming a person is not covered by Undo; rename again to fix it.
- Dead time means "nothing moves" or "too dark". It cannot tell a rally from players walking around.
- It runs on the processor, about 1 file per second. The graphics card is not used yet.

## Exporting to CapCut, Edits or Canva

Select files, then "Export copies". They are copied (never moved) into `exports/<name>/`, numbered in the
order they were taken, and the folder opens. Drag that folder into CapCut or Canva, or send it to your phone for Edits.
If the selection has videos you can choose trimmed copies, which cut out the stretches where nothing moves.
Trimmed copies are re-encoded at high quality; the originals are untouched.

## Making a reel

"Make a reel" in the header cuts photos into a vertical 1080x1920 video for Instagram.

- **Which photos:** the ones you selected; or, with nothing selected, the best-rated or a random mix from whatever
  section, tag, event or search is on screen. Duplicates and "eyes closed" photos are left out, and you can skip
  photos already posted.
- **Templates:** fast cuts, slow build then burst, slow zoom highlights, top 5 (best last), stop-motion sequence.
  They live in `templates.json`, so the list can be refreshed without touching the program.
- **Result:** plays in the dialog with a Download link, and is saved in `exports/reels/`.
- **From Claude chat:** ask for it in plain words ("make a countdown reel from Beach Bash, skip anything posted").
  Claude runs `python reel.py ...` and sends the video into the chat, where you can open it on your phone.

Limits: photos only (video clips are not cut in yet), no text and no sound are added (put those on in Edits or Instagram),
and tall crops follow detected faces, so a photo with no faces is cropped around its middle.

## Round-trip with Claude

Each export folder holds `manifest.json` (every file with its library id, tags, score, category, event) and
`README for Claude.md`. Open the folder in Claude and ask for what you want ("pick the 10 best for a reel and
tag them"). Claude writes `apply.json`, then `python sorter.py --apply <that file>` puts the tags, ratings,
categories and events back into the library. One Undo reverses the whole import.

## Using it from another computer (not switched on yet)

The app only answers on this computer. To reach it from the fieldhouse, install Tailscale (a free private
network between your own devices) on both computers, then add this computer's Tailscale name to
`allowed_hosts` in `config.json` and share port 8765 over Tailscale. Only devices signed in to your Tailscale
account can reach it. The exact Tailscale command is unverified until it is installed here.

## What it touches

- **Reads** every photo and video under `roots` in `config.json`.
- **Downloads** the AI models once, into `models/`. Your photos are never sent anywhere.
- **Writes** a database, thumbnails and hover previews into `data/` in this folder.
- **Copies** files you export into `exports/` in this folder.
- **Moves** files only when you ask: into `review_dir` (`rejects` or `duplicates`), and back with Undo or "Put back".
- **Deletes** only files already in the review folder, only when you confirm, and only to the Windows Recycle Bin.
- **Network**: this computer only. Nothing is uploaded anywhere.

## The score

| Score | Meaning |
|---|---|
| 0 | Nothing visible (black or blank: pocket shots) |
| 1 | Badly blurred or badly exposed |
| 2–5 | A rough ranking by sharpness, lighting, contrast and color within your library |

Built from focus (45%), lighting (20%), contrast (15%), color (10%) and resolution (10%).
Videos are judged on three frames. Your own rating always overrules the automatic one.
The thresholds live under `scoring` in `config.json`.
Files scoring 0 or 1 are flagged for a decision. B-roll is held to a lower bar: only a 0 flags it.

## Keys

In the viewer: `←` `→` browse, `0`–`5` rate and advance, `X` move to review, `Esc` close.
In the grid: click opens, Ctrl-click or the corner circle selects, Shift-click selects a range,
`Ctrl+A` selects everything in the current view, `Ctrl+Z` undoes the last change.

## Not built yet

- Using the graphics card to speed up the AI pass.
- Access from another computer: prepared, waiting on Tailscale (see above).
- Project files for CapCut or Canva (timelines, templates): exports are plain folders of media.
- Trimming is by movement only; it does not find rallies.
