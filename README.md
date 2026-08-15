# Declutter

**Organize and deduplicate photos, videos, music, audio and files — without ever deleting anything.**

`declutter.py` takes one or more messy folders and rebuilds their contents into a clean structure organized by type and date, detecting **real duplicates by content** (never by name). It is a single Python 3.6+ script with no required dependencies, and it runs anywhere: Linux, macOS, or a NAS over SSH.

By design it is **non-destructive**: it copies by default, sets duplicate copies aside in a `Duplicates/` folder for review, and has a dry-run mode that doesn't touch a single file.

It can also run in **extract-only mode** (`-d` without `-o`): deduplicate a folder against itself, setting only the duplicates aside in a folder of your choice, without reorganizing or touching the originals (see [§Extract-only mode](#extract-only-mode)).

---

## What it does

1. **Scans** the input folders recursively (one or more).
2. **Detects duplicates by content** with SHA-256 in 3 phases (see [§How duplicates are detected](#how-duplicates-are-detected)), across all inputs.
3. **Keeps a single copy** of each file (the one with the oldest modification time) and places it in the output structure — or leaves it where it is, in extract-only mode.
4. **Sets the extra copies aside** in `Duplicates/` (or the folder given with `-d`), preserving their original path so you can audit where each one came from.
5. **Writes a CSV report** of what is a duplicate of what, and where the kept original ended up.

---

## Requirements

| Component | Required? | Purpose |
|---|---|---|
| Python 3.6 or later | Yes | Run the script |
| Pillow (`pip3 install Pillow`) | No | Date photos from EXIF (`DateTimeOriginal`). Without it, the WhatsApp filename date or the modification time is used |
| pillow-heif (`pip3 install pillow-heif`) | No | Read EXIF from HEIC/HEIF (iPhone) files too |
| mutagen (`pip3 install mutagen`) | No | Classify music by artist/album from its tags. Without it, ALL audio goes to `Media/Audio/` by date. `pip` picks the right version for your Python (current mutagen needs ≥ 3.10; on 3.8/3.9 it resolves 1.47.x); only on 3.6/3.7 pin it by hand: `pip3 install mutagen==1.45.1` |

The script tells you at startup if any of the optional packages is missing and what that implies.

---

## Quick start — the 3-step recipe

```bash
# 1) SIMULATE: see what would happen, touching nothing, and dump the duplicate report
python3 declutter.py -i /path/to/messy-folder -o /path/to/organized \
    --dry-run --report /tmp/report.csv

# 2) REVIEW: read the simulation output and the CSV carefully

# 3) RUN: when everything looks right, move for real and clean up empty folders
python3 declutter.py -i /path/to/messy-folder -o /path/to/organized \
    --move --clean-empty-dirs
```

> The output folder **cannot be inside an input** (or vice versa), and the same goes for the duplicates folder given with `-d`. The script checks and aborts with a clear error if they overlap, because running that way re-ingests its own output and multiplies files.

---

## Output structure

Everything hangs from the folder given with `-o`:

```
<output>/
├── Media/
│   ├── Photos/YYYY/MM/photo.jpg        # EXIF date; else WhatsApp filename date; else mtime
│   ├── Videos/YYYY/MM/video.mp4        # WhatsApp filename date; else mtime
│   ├── Music/<Artist>/<Album>/song.mp3 # audio WITH an artist tag (needs mutagen)
│   │   └── <Artist>/song.mp3           # tagged with artist but no album
│   └── Audio/YYYY/MM/voicenote.opus    # audio without tags: WhatsApp, voice notes…
├── Files/
│   ├── pdf/…  docx/…  rar/…            # one folder per extension (flat)
│   └── other/<input>/<relative_path>/file.xyz   # unrecognized types
├── Duplicates/<input>/<relative_path>/…          # extra copies, for review
└── duplicates_report.csv               # (or the path given with --report)
```

- **`<input>`** is the name of the input folder the file came from. With several inputs, you always know the origin of every duplicate or "odd" file. Two inputs with the same folder name (`/a/Photos` and `/b/Photos`) get distinct labels: `Photos` and `Photos_2`.
- With `-d/--duplicates-to`, the `Duplicates/` tree lives at that path instead of inside the output.
- Unrecognized types and duplicates **keep their original relative path**; recognized photos, videos and files are reorganized by date/extension.
- **Name collisions** with different content (two different `IMG_001.jpg` landing in the same folder): both are kept, adding a `_1`, `_2`… suffix. Nothing is ever overwritten. Dry-run simulates these renames too.

### Recognized extensions (editable, see [§Customization](#customization))

| Group | Extensions |
|---|---|
| Photos | jpg, jpeg, png, gif, bmp, tif, tiff, webp, heic, heif, raw, cr2, cr3, nef, arw, dng, orf, rw2 |
| Videos | mp4, mov, avi, mkv, m4v, wmv, flv, webm, mts, m2ts, 3gp, mpg, mpeg |
| Audio | mp3, flac, m4a, wav, ogg, oga, opus, aac, wma, aiff, aif, aifc, ape, amr |
| Files | pdf, doc, docx, xls, xlsx, ppt, pptx, odt, ods, txt, rtf, csv, zip, rar, 7z, tar, gz, epub, mobi |

Any other extension (or files without one) → `Files/other/…`. Note that `m4b`/`m4r` (audiobooks, ringtones) are deliberately **not** listed as audio, so they never land in the music tree (see [§Customization](#customization) to add them).

### Always ignored

- System folders: `@Recycle`, `@Recently-Snapshot`, `@Transcode` (QNAP), `@eaDir`, `#recycle` (Synology), `$RECYCLE.BIN`, `System Volume Information` (Windows).
- **Hidden** files and folders (names starting with `.`).
- **Symbolic links** (counted and reported in the summary; processing them could leave broken links in the output).
- **Special files** (FIFOs, sockets, devices): trying to read them would hang the process.
- Repeated paths: if the same folder is reachable twice via different paths or links, it is processed only once.

---

## Usage

```
python3 declutter.py -i INPUT [-i INPUT2 …] (-o OUTPUT | -d DUPES_DIR | both) [options]
```

| Option | Effect |
|---|---|
| `-i, --input PATH` | Input folder. **Repeatable** to process several at once (deduplication runs across all of them). Required |
| `-o, --output PATH` | Output root folder. Required unless `-d` is given. Must not overlap with the inputs |
| `-d, --duplicates-to PATH` | Folder where duplicates are set aside. With `-o`, replaces the default `<output>/Duplicates`. Alone (without `-o`): [extract-only mode](#extract-only-mode). Must not overlap with the inputs |
| `--move` | **Move** instead of copy. Within the same volume this is an instant rename |
| `--dry-run` | Simulation: prints every action (`[DRY-RUN] …`) without touching anything. Does not create the output |
| `--report PATH` | CSV report path. Without it, the report goes to `duplicates_report.csv` under the output (or under `-d` in extract-only mode). **With `--dry-run` this is the only way to get the CSV** |
| `--skip-duplicates` | Do not relocate duplicates; only record them in the CSV. Combined with `-d` alone, a pure scan+report audit |
| `--clean-empty-dirs` | Only with `--move`: afterwards, remove folders left empty in the inputs (never the input root itself, and only if truly empty) |
| `--skip-space-check` | Skip the upfront free-space check at the destination (copy mode). With `-o` and `-d` on different volumes the check only measures the output's volume, so it is approximate |

---

## Extract-only mode

`-d` without `-o` deduplicates the inputs **in place**: nothing is reorganized, the kept originals stay exactly where they are, and only the redundant copies are set aside under `<dupes_dir>/<input>/<relative_path>/`.

```bash
# 1) Preview what would be extracted
python3 declutter.py -i /share/Media/Photos/FotosDeNavidad -d /share/Media/Duplicates --dry-run

# 2) Extract for real (move the duplicates out)
python3 declutter.py -i /share/Media/Photos/FotosDeNavidad -d /share/Media/Duplicates --move
```

- As everywhere else, **copy is the default**: without `--move` the duplicates are copied to the duplicates folder and also remain in the input. Use `--move` to actually extract them.
- Re-runs are safe in both variants: with `--move` the extracted copies are gone from the input; in copy mode, a duplicate already present at the destination with identical content is not copied again.
- Copy pass first, `--move` pass later? The move still extracts every duplicate from the input; the ones the copy pass already placed get a `_1` suffix at the destination (redundant but harmless — nothing is ever left behind silently).
- The CSV report goes to `<dupes_dir>/duplicates_report.csv` by default, and its `original_destination` column equals `kept_original` (originals are not relocated in this mode).
- `--skip-duplicates` combined with `-d` alone turns the run into a pure census: scan, report, touch nothing.

---

## How dates are decided (the `YYYY/MM` folder)

**Photos** (with Pillow installed), in this order:

1. `DateTimeOriginal` from the Exif sub-IFD — the real capture date written by cameras and phones.
2. `DateTimeDigitized` from the Exif sub-IFD.
3. `DateTime` from IFD0.
4. No readable EXIF → WhatsApp filename date (see below), else file modification time (mtime).

**Videos**: WhatsApp filename date, else mtime (video metadata is not read; see [§Known limitations](#known-limitations)).

**Audio without tags**: WhatsApp filename date, else mtime.

**WhatsApp filename date**: WhatsApp strips EXIF but encodes the real send date in the
filename, so `IMG-`/`VID-`/`PTT-`/`AUD-YYYYMMDD-WA….ext` (phones) and desktop/web exports —
whose prefix is localized: `WhatsApp Audio 2023-05-12 at ….ogg`, `Imagen de WhatsApp
2023-05-12 a las ….jpeg`, `WhatsApp Bild 2023-05-12 um ….jpg`… — are dated from the name.
A photo, a video and a voice note from the same chat land in the same `YYYY/MM` across all
three branches. Names that merely look like the pattern but carry a date impossible in the
calendar (month 13…) fall back to mtime; a genuinely future-dated name (a phone with a skewed
clock) is filed under its named year — an easy-to-spot review bucket, and deterministic across
re-runs.

## How audio is classified

The rule is literal: **any audio file with a readable artist tag goes to
`Media/Music/<Artist>/<Album>/`** (album omitted when missing) — including tagged podcasts and
audiobooks in `.mp3`/`.m4a`. Everything else — WhatsApp audio, voice notes, recordings, and any
music without tags — goes to `Media/Audio/YYYY/MM/`.

Notes:

- Tag reading needs **mutagen** (optional). Without it, nothing is promoted to `Media/Music/`
  and all audio is filed by date — the tree keeps the same shape either way.
- `albumartist` is preferred over `artist`, so compilations stay together; an empty
  `albumartist` falls through to `artist`.
- Tag keys are looked up per container (easy keys, Vorbis comments, ASF/`WM/…` for WMA, raw ID3
  frames for WAV/AIFF, APEv2), so tagged WMA/WAV/AIFF/APE libraries are classified too.
- Artist/album folder names are sanitized to be valid on Windows/SMB/exFAT shares: illegal
  characters (`<>:"|?*/\`) become `_`, names are capped at 80 chars, and Windows-reserved names
  (`CON`, `NUL`…) get a trailing `_`.

**Corrupt mtime** (impossible dates left behind by some filesystem or an old copy): the file is filed under `1970/01`, an easy-to-spot review bucket, instead of crashing the run.

---

## How duplicates are detected

"Duplicate" means **byte-for-byte identical content**, regardless of name, folder or date. Detection runs in 3 phases so it scales to hundreds of thousands of files:

1. **By size**: files with a unique size are never hashed (they can't have a duplicate).
2. **Partial hash**: within each same-size group, SHA-256 of the first 64 KiB. Discards most false candidates while reading very little.
3. **Full hash**: full-file SHA-256 only for those still matching. Two files with the same size and the same beginning but a different ending are **not** flagged as duplicates. (If a file fits entirely in 64 KiB, phase 2 already is the full hash and it is not re-read.)

Among identical copies, the one with the **oldest mtime is kept as the original**; the rest go to `Duplicates/` (or only to the CSV, with `--skip-duplicates`).

Progress is printed periodically (every 100 groups analyzed and every 1000 files placed), with flushing so it stays visible over SSH sessions and in logs.

---

## The CSV report

UTF-8 encoded, one row per detected duplicate:

| Column | Content |
|---|---|
| `duplicate` | Original path of the redundant file (where it was in the input) |
| `kept_original` | Input path of the copy that was kept |
| `original_destination` | Where that kept original was placed in the output (especially useful after `--move`, when the input path no longer exists). In extract-only mode originals are not relocated, so this equals `kept_original` |
| `sha256` | Content hash (identical for the whole pair/group) |

Filenames with non-UTF-8 bytes appear with escape sequences instead of breaking the report.

---

## Re-runs, resuming and incremental passes

The script is **resumable and idempotent**: if a file's content already sits at its destination — even under a collision-renamed name (`x_1.jpg`) — it is not copied again. In copy mode it is simply skipped and counted in the summary (`Already at destination`); with `--move` the source is still set aside into `Duplicates/`, so it always leaves the input.

- **Interrupted (Ctrl+C) or disk full**: re-run the same command and it picks up where it left off, without overwriting anything. Every transfer lands under a temporary `.part` name and is renamed into place only when complete, so an interruption can never leave a truncated file under a real name (at most a stray `.part` file after a power loss, easy to spot and delete).
- **Resuming or repeating with `--move`**: naturally clean — whatever was already moved is no longer in the input.
- **Resuming or repeating in copy mode**: safe as-is — already-placed files are skipped, not treated as duplicates, and nothing is re-copied into `Duplicates/`.
- **Incremental passes** (feeding new folders into the same output over time): same — content already placed is recognized and skipped in copy mode; with `--move` it is already gone from the input.
- **Extract-only mode** is idempotent on its own: duplicates already extracted (moved out, or copied with identical content at the destination) are not extracted again.

> ⚠️ **Upgrading from a version without audio support**: older versions placed mp3/flac/m4a/wav/ogg
> under `Files/<ext>/` and every other now-recognized audio extension (opus, oga, aac, wma, aiff,
> aif, aifc, ape, amr — including WhatsApp `.opus` voice notes) under `Files/other/<input>/…`.
> This version computes audio destinations under `Media/Music/` or `Media/Audio/`, so it does
> **not** recognize those files as already placed: resuming an old interrupted run (or an
> incremental pass into an old output) re-places every audio file under `Media/` and leaves the
> old copies behind. Either finish pending runs with the old script, or accept the re-placement
> and then clean up by hand: `Files/{mp3,flac,m4a,wav,ogg}` plus the audio buried in
> `Files/other/` (e.g. `find Files/other -name '*.opus'` — don't delete `Files/other/` wholesale,
> it also holds genuinely unrecognized files that were NOT re-placed).
> For the same reason, **don't install or remove mutagen between a run and its resume**: tagged
> audio would flip between `Media/Audio/` and `Media/Music/` and be re-copied.

---

## Built-in safeguards

- **Input/output overlap**: aborts before starting (exit code 2) with an explanation. The duplicates folder given with `-d` gets the same check.
- **Repeated or nested inputs**: deduplicated with a warning (prevents files being flagged as "duplicates of themselves").
- **Space check** (copy mode): before touching anything, estimates the total to copy and aborts if the destination lacks that space +2% margin, suggesting alternatives (`--move`, freeing space, or `--skip-space-check`).
- **Disk full mid-run (ENOSPC)**: stops dead (exit code 3) instead of failing file by file for hours. Free some space, re-run, and it resumes.
- **No partial files**: copies and cross-volume moves write to a temporary `.part` sibling and rename into place once complete, so an interrupted run never leaves a truncated file under a real name.
- **Non-UTF-8 filenames**: neither the console nor the CSV crash (output uses replacement/escapes).
- **Unreadable folders** (permissions): reported on stderr and counted in the summary instead of being silently skipped.
- **Final summary** with counters: unique files processed, duplicates, files already placed by a previous run, symlinks skipped, special files skipped, unreadable items and errors.

---

## Exit codes

| Code | Meaning |
|---|---|
| 0 | All good |
| 1 | Finished, but with errors on specific files (details on stderr) |
| 2 | Arguments/validation: invalid inputs, input/output overlap, or not enough space |
| 3 | Aborted mid-run because the destination disk filled up (ENOSPC) |
| 130 | Interrupted by the user (Ctrl+C) |

Useful when chaining it in scripts or cron jobs.

---

## Tips for long runs

**Long sessions.** With hundreds of thousands of files a run takes hours: launch it inside `tmux`/`screen`, or with `nohup` and a log:

```bash
nohup nice -n 19 ionice -c3 python3 declutter.py \
    -i /path/to/messy-folder -o /path/to/organized --move --clean-empty-dirs \
    > declutter.log 2>&1 &

tail -f declutter.log
```

**Low priority.** The `nice -n 19 ionice -c3` in the example makes the script yield CPU and disk to whatever else the machine is doing (media indexing, backups…) instead of competing with it. Handy on NAS devices, where it runs fine over SSH.

**Same volume = instant.** `--move` within the same volume is a rename (like `mv`): no data is copied and no extra space is needed. Across volumes it means copy + delete.

**Merging into your final folders.** The output creates its own `Media/` and `Files/` roots, so the comfortable route is organizing into a working folder first and, after reviewing, merging into your real folders with `mv` (instant on the same volume).

**Duplicates.** Don't delete `Duplicates/` blindly: review the CSV, spot-check that the originals are where `original_destination` says, and then decide.

---

## Customization

At the top of the script there are a few blocks meant to be edited freely:

- `PHOTO_EXTS`, `VIDEO_EXTS`, `AUDIO_EXTS`, `FILE_EXTS`: add or remove extensions (lowercase, without the dot). Adding `m4b`/`m4r` to `AUDIO_EXTS` classifies audiobooks/ringtones like any other audio (tagged ones will show up under `Media/Music/`).
- `EXCLUDED_DIRS`: folders ignored by exact name.
- `PARTIAL_BYTES` (64 KiB) and `CHUNK` (1 MiB): read sizes for the partial and full hash. The defaults work well; there's rarely a reason to touch them.

---

## Known limitations

- **Video dates**: mtime, except for WhatsApp-named files (reading the real creation date from video metadata would require dependencies like ffprobe/exiftool).
- **Untagged music is indistinguishable from a voice note**: it is filed by date, so an untagged album can smear across several `YYYY/MM` folders (one per mtime). Tag your files first (e.g. with MusicBrainz Picard) if you want them under `Media/Music/`.
- **Distinct artists can merge after sanitization**: `AC/DC` and `AC:DC` both become the folder `AC_DC` (files are never overwritten — collisions get `_1`, `_2`… suffixes).
- **`.aac` (ADTS) always goes to `Media/Audio/`**: mutagen cannot read tags from raw ADTS streams.
- **Hidden files**: not processed (by design; remove the leading dot if you want them organized).
- `Files/<ext>/` is **flat**: all PDFs together, all DOCX together… (original paths are only preserved under `other/` and `Duplicates/`).
- **Single-threaded hashing.** On HDDs the disk is the bottleneck, so it hardly matters; on SSDs a parallel approach would be faster.
- **Hardlinks** to the same content are treated as regular duplicates (different paths, same content).
- Moving **across volumes** uses copy + delete (standard `mv`/`shutil.move` behavior).

---

## Examples

```bash
# Simulation with report (always step 1)
python3 declutter.py -i /data/messy -o /data/organized --dry-run --report /tmp/report.csv

# Copy (the input is left untouched)
python3 declutter.py -i /data/messy -o /data/organized

# Move and clean up folders left empty in the input
python3 declutter.py -i /data/messy -o /data/organized --move --clean-empty-dirs

# Several inputs at once (deduplication runs across all of them)
python3 declutter.py -i /data/old1 -i /data/old2 -o /data/organized --move

# Second, incremental pass in copy mode (don't bloat Duplicates/)
python3 declutter.py -i /data/new-batch -o /data/organized --skip-duplicates

# Just a duplicate census, without organizing anything yet
python3 declutter.py -i /data/messy -o /data/organized --dry-run --report /tmp/census.csv

# Extract-only: preview, then move ONLY the duplicates out, originals untouched
python3 declutter.py -i /share/Media/Photos/FotosDeNavidad -d /share/Media/Duplicates --dry-run
python3 declutter.py -i /share/Media/Photos/FotosDeNavidad -d /share/Media/Duplicates --move

# Organize as usual, but keep the duplicates quarantine on another disk
python3 declutter.py -i /data/messy -o /data/organized -d /mnt/backup/dupes --move
```

---

## License

[MIT](LICENSE)
