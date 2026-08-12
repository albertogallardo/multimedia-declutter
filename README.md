# Declutter

**Organize and deduplicate photos, videos and files — without ever deleting anything.**

`declutter.py` takes one or more messy folders and rebuilds their contents into a clean structure organized by type and date, detecting **real duplicates by content** (never by name). It is a single Python 3.6+ script with no required dependencies, and it runs anywhere: Linux, macOS, or a NAS over SSH.

By design it is **non-destructive**: it copies by default, sets duplicate copies aside in a `Duplicates/` folder for review, and has a dry-run mode that doesn't touch a single file.

---

## What it does

1. **Scans** the input folders recursively (one or more).
2. **Detects duplicates by content** with SHA-256 in 3 phases (see [§How duplicates are detected](#how-duplicates-are-detected)), across all inputs.
3. **Keeps a single copy** of each file (the one with the oldest modification time) and places it in the output structure.
4. **Sets the extra copies aside** in `Duplicates/`, preserving their original path so you can audit where each one came from.
5. **Writes a CSV report** of what is a duplicate of what, and where the kept original ended up.

---

## Requirements

| Component | Required? | Purpose |
|---|---|---|
| Python 3.6 or later | Yes | Run the script |
| Pillow (`pip3 install Pillow`) | No | Date photos from EXIF (`DateTimeOriginal`). Without it, modification time is used |
| pillow-heif (`pip3 install pillow-heif`) | No | Read EXIF from HEIC/HEIF (iPhone) files too |

The script tells you at startup if either optional package is missing and what that implies.

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

> The output folder **cannot be inside an input** (or vice versa). The script checks and aborts with a clear error if they overlap, because running that way re-ingests its own output and multiplies files.

---

## Output structure

Everything hangs from the folder given with `-o`:

```
<output>/
├── Media/
│   ├── Photos/YYYY/MM/photo.jpg        # EXIF date if available; else mtime
│   └── Videos/YYYY/MM/video.mp4        # modification time
├── Files/
│   ├── pdf/…  docx/…  rar/…  mp3/…     # one folder per extension (flat)
│   └── other/<input>/<relative_path>/file.xyz   # unrecognized types
├── Duplicates/<input>/<relative_path>/…          # extra copies, for review
└── duplicates_report.csv               # (or the path given with --report)
```

- **`<input>`** is the name of the input folder the file came from. With several inputs, you always know the origin of every duplicate or "odd" file.
- Unrecognized types and duplicates **keep their original relative path**; recognized photos, videos and files are reorganized by date/extension.
- **Name collisions** with different content (two different `IMG_001.jpg` landing in the same folder): both are kept, adding a `_1`, `_2`… suffix. Nothing is ever overwritten. Dry-run simulates these renames too.

### Recognized extensions (editable, see [§Customization](#customization))

| Group | Extensions |
|---|---|
| Photos | jpg, jpeg, png, gif, bmp, tif, tiff, webp, heic, heif, raw, cr2, cr3, nef, arw, dng, orf, rw2 |
| Videos | mp4, mov, avi, mkv, m4v, wmv, flv, webm, mts, m2ts, 3gp, mpg, mpeg |
| Files | pdf, doc, docx, xls, xlsx, ppt, pptx, odt, ods, txt, rtf, csv, zip, rar, 7z, tar, gz, mp3, flac, m4a, wav, ogg, epub, mobi |

Any other extension (or files without one) → `Files/other/…`.

### Always ignored

- System folders: `@Recycle`, `@Recently-Snapshot`, `@Transcode` (QNAP), `@eaDir`, `#recycle` (Synology), `$RECYCLE.BIN`, `System Volume Information` (Windows).
- **Hidden** files and folders (names starting with `.`).
- **Symbolic links** (counted and reported in the summary; processing them could leave broken links in the output).
- **Special files** (FIFOs, sockets, devices): trying to read them would hang the process.
- Repeated paths: if the same folder is reachable twice via different paths or links, it is processed only once.

---

## Usage

```
python3 declutter.py -i INPUT [-i INPUT2 …] -o OUTPUT [options]
```

| Option | Effect |
|---|---|
| `-i, --input PATH` | Input folder. **Repeatable** to process several at once (deduplication runs across all of them). Required |
| `-o, --output PATH` | Output root folder. Required. Must not overlap with the inputs |
| `--move` | **Move** instead of copy. Within the same volume this is an instant rename |
| `--dry-run` | Simulation: prints every action (`[DRY-RUN] …`) without touching anything. Does not create the output |
| `--report PATH` | CSV report path. Without it, the report goes to `<output>/duplicates_report.csv`. **With `--dry-run` this is the only way to get the CSV** |
| `--skip-duplicates` | Do not relocate duplicates to `Duplicates/`; only record them in the CSV. Essential for incremental re-runs in copy mode (see [§Re-runs](#re-runs-resuming-and-incremental-passes)) |
| `--clean-empty-dirs` | Only with `--move`: afterwards, remove folders left empty in the inputs (never the input root itself, and only if truly empty) |
| `--skip-space-check` | Skip the upfront free-space check at the destination (copy mode) |

---

## How dates are decided (the `YYYY/MM` folder)

**Photos** (with Pillow installed), in this order:

1. `DateTimeOriginal` from the Exif sub-IFD — the real capture date written by cameras and phones.
2. `DateTimeDigitized` from the Exif sub-IFD.
3. `DateTime` from IFD0.
4. No readable EXIF → file modification time (mtime).

**Videos**: always mtime (video metadata is not read; see [§Known limitations](#known-limitations)).

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
| `original_destination` | Where that kept original was placed in the output (especially useful after `--move`, when the input path no longer exists) |
| `sha256` | Content hash (identical for the whole pair/group) |

Filenames with non-UTF-8 bytes appear with escape sequences instead of breaking the report.

---

## Re-runs, resuming and incremental passes

The script is **resumable and idempotent**: if a file already exists at its destination with identical content, it is not copied or renamed again; it is recorded as a duplicate of the destination.

- **Interrupted (Ctrl+C) or disk full**: re-run the same command and it picks up where it left off, without overwriting anything.
- **Resuming or repeating with `--move`**: naturally clean — whatever was already moved is no longer in the input.
- **Resuming or repeating in copy mode**: add `--skip-duplicates`. Otherwise everything already placed by the previous pass would count as a "duplicate of the destination" and be physically copied into `Duplicates/`, bloating it for no reason.
- **Incremental passes** (feeding new folders into the same output over time): same advice — in copy mode use `--skip-duplicates`; with `--move` it's not needed.

---

## Built-in safeguards

- **Input/output overlap**: aborts before starting (exit code 2) with an explanation.
- **Repeated or nested inputs**: deduplicated with a warning (prevents files being flagged as "duplicates of themselves").
- **Space check** (copy mode): before touching anything, estimates the total to copy and aborts if the destination lacks that space +2% margin, suggesting alternatives (`--move`, freeing space, or `--skip-space-check`).
- **Disk full mid-run (ENOSPC)**: stops dead (exit code 3) instead of failing file by file for hours. Free some space, re-run, and it resumes.
- **Non-UTF-8 filenames**: neither the console nor the CSV crash (output uses replacement/escapes).
- **Unreadable folders** (permissions): reported on stderr and counted in the summary instead of being silently skipped.
- **Final summary** with counters: unique files processed, duplicates, symlinks skipped, special files skipped, unreadable items and errors.

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

- `PHOTO_EXTS`, `VIDEO_EXTS`, `FILE_EXTS`: add or remove extensions (lowercase, without the dot).
- `EXCLUDED_DIRS`: folders ignored by exact name.
- `PARTIAL_BYTES` (64 KiB) and `CHUNK` (1 MiB): read sizes for the partial and full hash. The defaults work well; there's rarely a reason to touch them.

---

## Known limitations

- **Video dates**: always mtime (reading the real creation date would require dependencies like ffprobe/exiftool).
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
```

---

## License

[MIT](LICENSE)
