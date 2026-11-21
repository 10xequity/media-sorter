# Media Sorter

**Version** 0.11.1 (tags from Claude Code, favorites, crops, collages, more filters and templates, video skipping) · **Date** 2026-11-21 · **Status** Working; AI tags are a first pass, remote access not yet switched on · **Supersedes** 0.10.0

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

## Tags, event names, transcripts and AI services

- **More automatic tags** (Run AI): action shot, posed, candid, party, friends, jumping, ball in the air, sunglasses,
  funny face, trophy or medal, shoes and feet, food and drink, kids, selfie, plus individual / pair / group (counted
  from faces) and two more places (indoors not a gym, outdoors not a court). They are a first guess and some will be
  wrong. The list lives in `config.default.json` and arrives with updates; after an update the library re-tags itself.
- **Event names:** each day of photos can be named after the event on your calendar, with the date at the end
  ("6s Qualifier Round 1 2024-11-03"). The dialog shows the calendar's names for that day beside the current name and
  only suggests a calendar name when the two share a word. Labels only; no file is renamed; Undo reverses it. The
  calendar is `data/events.csv` (`date,name`); "Load a calendar file" brings it onto another computer.
- **Transcripts:** Run AI also listens to videos (on this computer) and writes down what is said. The words are
  searchable, and each line in the viewer jumps to that moment. Gym noise can produce a few wrong or repeated lines.
- **AI services** (optional): connect a large AI model to write a caption and extra tags for the photos in your
  project ("Describe with AI"). Choices: Ollama on this computer (free, private) or Claude, ChatGPT or Gemini with a
  pay-per-use key. The cloud choices send the chosen photos to that company. Keys are stored in `config.json` on this
  computer only.

## Folders, drives and scanning

- **Folders** (header button) picks which folders are scanned, from a tree of this computer's drives.
- On start-up the app lists what is new or changed in those folders and asks before scanning.
- Each folder's drive is remembered by its serial number. If a drive comes back under a different letter, the
  library follows it: tags, scores and people are kept. If a drive is unplugged, its files stay in the library
  (shown from memory) and are never marked missing.
- Every scan is recorded; the history is at the bottom of the Folders dialog.

## Photos kept in Google Drive, iCloud, OneDrive or Dropbox

The folder picker ("Folders") lists these first when their sync app is installed on this computer: iCloud Photos,
the Mac Photos library (its `originals` folder), iCloud Drive, OneDrive pictures, Dropbox, and Google Drive (on
Windows, Google Drive is a drive of its own in the drive list). Tick the folder and it is scanned like any other.

- Files the service keeps online-only are downloaded as they are scanned, so pick the folders you need, not everything.
- **Google Photos has no folder on a computer.** Download the albums you want from photos.google.com (or Google
  Takeout) into a folder and add that folder.
- The Mac Photos library is read-only here: the app never changes it, and "move to review" is best not used on it.
  Reading it may need "Full Disk Access" for the app in Mac settings (untested: no Mac here).

## Updates

When a newer version is published, an **Update to x.y.z** button appears in the header. It downloads the new program
files from GitHub, backs up the old ones in `data/backup/`, and restarts. Settings (`config.json`), the library
(`data/`), AI models and exports are never touched. Without internet: drag a Media Sorter zip onto `update.bat`.

To publish a version (from the development copy): raise the number in `VERSION`, commit, and push to `main`.

`python sorter.py --selftest`, `python ai.py --selftest`, `python reel.py --selftest`, `python llm.py` and `python update.py --selftest` run the built-in checks.
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

## Tags from Claude Code (no extra AI install)

"Claude tags" in the header uses Claude Code, signed in with your own Claude account, instead of a separate AI
program or a paid key. (Ollama and the keyed services under "AI services" are still there if you want them.)

1. **Lay out a batch.** The app puts small copies (1024 pixels) of the photos into `data/claude/photos`, with a
   list of each photo's faces and current tags and a page of instructions. It uses the ticked photos, or else the
   best-scoring photos that have no caption yet.
2. **Ask Claude.** Open Claude Code in the Media Sorter folder and say "tag my photos". It looks at each picture and
   writes `data/claude/tags.json`: tags, a category, a one-sentence caption, and things pinned to a face (a jersey
   number, a printed name) by the face's number or by a point inside it.
3. **Read them in.** Press "Read in Claude's tags" (Claude can also run `python sorter.py --claude-import`).
   One Undo reverses the tags and categories; captions and face names stay.

Things to know: the small copies are sent to Anthropic when Claude looks at them. The list Claude reads leaves out
file names and strips symbols from event names and tags, and Claude is told to treat all of it, and any writing
inside a photo, as information only, so a file with a planted message in its name cannot steer it. Claude does not work out who a
person is from their face; the People list (the app's own face grouping) does that, and a name you give a face
there sticks. `CLAUDE.md` in this folder holds the instructions Claude Code follows, including "check Instagram
for new posts".

## Favorites

The heart on a tile (or the F key, or "♥ Favorite" in the viewer and the bottom bar) marks a file to use later.
"♥ Favorites" in the left rail lists them. After you make a video or collage, send or export, the app offers to
take the mark off the favorites that were just used.

## Marking photos you have already posted

"Instagram" in the header lists the accounts being checked: how many of each account's post pictures are saved on
this computer, the date of the newest, and how many library photos are marked. Add an account by name or profile
address; each gets a folder in `data/instagram/`. "Compare with the library now" marks the matches.

Instagram does not let an app read posts by itself, so the pictures get into that folder one of two ways: ask
Claude "check Instagram for new posts" (it opens each account in the browser and saves the post pictures), or
unzip Instagram's own "Download your information" export there. To stop checking an account, delete its folder
by hand; the app never deletes anything itself.

The same check from the command line: `python ai.py --posted "<folder>" <account name>`. Library photos that appear in that folder get a blue
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
Photos you gave a look (below) are exported with it, and videos with marked pieces are exported as just those pieces.

## Sending files to someone

Tick the files, then "Send" (bottom bar or Project panel). Copies are sent; the library is not changed.

- **Size:** "Email size" sends photos about 2000 pixels wide (roughly 0.3 MB each instead of 5 to 6 MB), with any
  look you gave them. "Full size" sends the originals (or a full-size edited copy). Videos always go as they are.
- **Share…** opens the device's own share menu (Mail, Messages, AirDrop, Nearby Share) with the pictures attached.
  It takes the first 30 files and only shows in browsers that have a share menu (Chrome and Edge on Windows,
  Safari and Chrome on phones). Getting many files ready can take a few seconds; if the menu does not open,
  press Share once more.
- **Save as one file** puts everything into one zip in `exports/send/`, opens that folder, and offers a download
  link and a "Start an email" link. A web page cannot attach a file for you: drag the zip onto the email.
  Gmail carries up to 25 MB per email; the dialog warns when the file is bigger.

Zips stay in `exports/send/` until you delete them.

## Collages

Tick photos, then "Make a collage": pick one of eleven layouts (two side by side up to a grid of nine) and a
shape. Photos fill the layout in project order, each around its action point and with its look. The picture is
saved in `exports/collages`. Layouts live in `templates.json` beside the video templates, so new ones arrive
with updates.

## Framing, look and trim (in the viewer)

Open a file. These are saved for that file in the library; the original file is never changed, and Undo does not
cover them (use "Use the automatic point", "Reset look" or the ✕ beside a piece).

- **Framing for videos (photos):** turn on crop bars for vertical, horizontal or square. The dark bars show what a
  video of that shape leaves out. Click the photo where the action is, or use the Across and Down sliders. Without a
  point of your own, the app centres on the faces it found.
- **Crop (photos):** "Crop this photo to" square, portrait 4:5, vertical 9:16 or horizontal 16:9. The suggested
  crop is the largest piece of that shape around the action point, and the bars show it. Copies you send, export or
  save are cut that way. In the Send dialog, "Crop" does the same for every photo at once.
- **Look (photos):** auto balance (light and contrast only; colours are left alone), a filter (vivid, punch, warm,
  golden hour, cool, teal, rose, forest green, sepia, black and white, noir, faded, matte), and brightness,
  contrast and color sliders. "Save an edited copy" writes a full-size copy
  into `exports/`. Thumbnails in the grid keep showing the original.
- **Playback (videos):** speed buttons from half to four times, sound off, and the bar under the video skips
  to any point. In the grid, resting on a video tile plays the whole video silently, sped up to about 12 seconds;
  moving across the tile skips through it.
- **Trim and cut (videos):** play to a spot, "Piece starts here", play on, "Piece ends here". Mark as many pieces as
  you like. "Save trimmed copy" joins them into a new video in `exports/`.

The Project panel's "Show in grid" lists only the project's files, and selecting a thumbnail there opens that file,
so each photo of a video can be framed before it is made.

## Making a video

"Make a video" in the header cuts photos into a video: vertical 1080x1920 (Reels, Stories), horizontal 1920x1080,
square 1080x1080, or portrait 1080x1350 (a 4:5 feed post). Eleven templates set the pace.

- **Framing:** as the template says; fill the frame around the action; slide across to the action (each photo
  starts on its middle and glides to the framing point; cuts shorter than 0.8 seconds just hold on the action); or
  the whole photo on a blurred backdrop.

- **Which photos:** the ones you selected; or, with nothing selected, the best-rated or a random mix from whatever
  section, tag, event or search is on screen. Duplicates and "eyes closed" photos are left out, and you can skip
  photos already posted.
- **Templates:** fast cuts, slow build then burst, slow zoom highlights, top 5 (best last), stop-motion sequence.
  They live in `templates.json`, so the list can be refreshed without touching the program.
- **Result:** plays in the dialog with a Download link, and is saved in `exports/reels/`.
- **From Claude chat:** ask for it in plain words ("make a countdown reel from Beach Bash, skip anything posted").
  Claude runs `python reel.py ...` and sends the video into the chat, where you can open it on your phone.

Limits: photos only (video clips are not cut in yet), no text and no sound are added (put those on in Edits or Instagram),
and crops follow your framing point or else detected faces, so a photo with neither is cropped around its middle.

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

## Security checks (run 2026-11-21)

An attack script was run against the app on this computer: 102 checks, covering requests from other websites,
reading files outside the library, database injection through every filter, malformed input, writing outside the
exports folder, leaking the saved AI key, and stalled connections. Eight weaknesses were found and fixed; all 102
pass in 0.10.0:

- two malformed requests crashed the request instead of being refused;
- a device reaching the app through the private network could have installed an "update" from a file path it
  named, and could open files in programs on this computer's screen; both are now limited to someone at this computer;
- the page had no protection against being framed or loading outside scripts; it now forbids both;
- a connection that stalled was kept open for good; it is now dropped after 30 seconds.

Not covered: the app has no sign-in of its own. Once remote access is switched on, every device on your private
network can use it fully, so only add devices you trust.

Load, measured the same day: with 32 requests at once the app answered 144 a second with none failing. On a
test copy grown to 163,000 files, the first page still opens in about 2 seconds stacked (instantly unstacked) and
the summary numbers take about 3 seconds (they took 10 before this version). Up to roughly 50,000 files
everything stays under a second.

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
