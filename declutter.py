#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""declutter.py — Organize files by type and date, deduplicating by content.

Scans one or more input folders and builds a clean tree:

  Media/Photos/YYYY/MM/photo.jpg   (EXIF date, else WhatsApp filename date, else mtime)
  Media/Videos/YYYY/MM/video.mp4   (WhatsApp filename date, else mtime)
  Media/Music/Artist/Album/song.mp3          (audio with an artist tag, via mutagen)
  Media/Audio/YYYY/MM/voicenote.opus         (audio without tags: voice notes, WhatsApp...)
  Files/<ext>/file.<ext>
  Files/other/<source>/<relative_path>/...   (unrecognized types)
  Duplicates/<source>/<relative_path>/...    (extra copies, kept for review)
  duplicates_report.csv

Duplicates are detected by CONTENT (size -> partial SHA-256 -> full SHA-256),
never by name. Nothing is ever deleted: files are copied by default and extra
copies are set aside in Duplicates/ for manual review.

With -d/--duplicates-to, the extra copies go to that folder instead of
<output>/Duplicates. Used alone (without -o) nothing is reorganized: the
originals stay where they are and only the duplicates are copied (or moved
with --move) there — extract-only mode.

No required dependencies. With Pillow installed, photos are dated from EXIF;
with pillow-heif, HEIC/HEIF (iPhone) files are too. With mutagen, music is
classified by artist/album; without it, all audio is filed by date.

Recommended usage:
  1) python3 declutter.py -i /path/to/messy -o /path/to/organized --dry-run --report /tmp/report.csv
  2) review the report and the simulation
  3) python3 declutter.py -i /path/to/messy -o /path/to/organized --move --clean-empty-dirs

Extract-only (deduplicate a folder in place, originals untouched):
  python3 declutter.py -i /path/to/folder -d /path/to/duplicates --dry-run
  python3 declutter.py -i /path/to/folder -d /path/to/duplicates --move
"""

import argparse
import csv
import errno
import hashlib
import os
import re
import shutil
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime

# ---------------------------------------------------------------------------
# TYPE CONFIGURATION — edit to taste
# ---------------------------------------------------------------------------

PHOTO_EXTS = {
    "jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff", "webp",
    "heic", "heif", "raw", "cr2", "cr3", "nef", "arw", "dng", "orf", "rw2",
}

VIDEO_EXTS = {
    "mp4", "mov", "avi", "mkv", "m4v", "wmv", "flv", "webm",
    "mts", "m2ts", "3gp", "mpg", "mpeg",
}

# m4b/m4r (audiobooks, ringtones) are deliberately NOT audio so they never
# land in Media/Music; add them here if you want them classified anyway.
AUDIO_EXTS = {
    "mp3", "flac", "m4a", "wav", "ogg", "oga", "opus",
    "aac", "wma", "aiff", "aif", "aifc", "ape", "amr",
}

FILE_EXTS = {
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods",
    "txt", "rtf", "csv",
    "zip", "rar", "7z", "tar", "gz",
    "epub", "mobi",
}

# System folders (NAS devices, Windows), skipped by exact name.
# Anything starting with "." is already skipped as hidden.
EXCLUDED_DIRS = {
    "@Recycle", "@Recently-Snapshot", "@Transcode",   # QNAP
    "@eaDir", "#recycle",                             # Synology
    "$RECYCLE.BIN", "System Volume Information",      # Windows
}

CHUNK = 1024 * 1024          # bytes per read when hashing
PARTIAL_BYTES = 64 * 1024    # bytes hashed in the partial pass

# ---------------------------------------------------------------------------
# DATES
# ---------------------------------------------------------------------------

try:
    from PIL import Image  # optional
    _PIL_OK = True
    try:
        from pillow_heif import register_heif_opener  # optional HEIC/HEIF support
        register_heif_opener()
        _HEIF_OK = True
    except Exception:
        _HEIF_OK = False
except ImportError:
    _PIL_OK = False
    _HEIF_OK = False

_IFD_EXIF = 0x8769           # Exif sub-IFD
_TAG_DT_ORIGINAL = 36867     # DateTimeOriginal (lives in the sub-IFD)
_TAG_DT_DIGITIZED = 36868    # DateTimeDigitized (idem)
_TAG_DT = 306                # DateTime (IFD0)


def exif_date(path):
    """EXIF date of an image, or None. DateTimeOriginal lives in the Exif
    sub-IFD, which getexif() does not include, so it is queried explicitly."""
    if not _PIL_OK:
        return None
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            candidates = []
            if hasattr(exif, "get_ifd"):
                try:
                    sub = exif.get_ifd(_IFD_EXIF)
                    candidates += [sub.get(_TAG_DT_ORIGINAL), sub.get(_TAG_DT_DIGITIZED)]
                except Exception:
                    pass
            # Fallbacks: IFD0 DateTime, plus flattened IFDs on some Pillow versions
            candidates += [exif.get(_TAG_DT), exif.get(_TAG_DT_ORIGINAL), exif.get(_TAG_DT_DIGITIZED)]
            for val in candidates:
                if not val:
                    continue
                try:
                    return datetime.strptime(str(val)[:19], "%Y:%m:%d %H:%M:%S")
                except ValueError:
                    continue
    except Exception:
        pass
    return None


# WhatsApp names its files after the real send date (and strips EXIF), so the
# filename beats mtime: IMG/VID/PTT/AUD-YYYYMMDD-WA0001.ext (phones) and
# desktop/web exports, whose prefix is localized ("WhatsApp Audio 2023-05-12
# at 10.23.11.ogg", "Imagen de WhatsApp 2023-05-12 a las...", "WhatsApp Bild
# 2023-05-12 um...") — hence the loose second pattern.
_WA_NAME_RES = (
    (re.compile(r"^(?:IMG|VID|PTT|AUD)-(\d{8})-WA\d", re.IGNORECASE), "%Y%m%d"),
    (re.compile(r"WhatsApp\D*?(\d{4}-\d{2}-\d{2})(?!\d)", re.IGNORECASE), "%Y-%m-%d"),
)


def _filename_date(name):
    """Date encoded in a WhatsApp-style filename, or None. Strict: a renamed
    file that merely matches the pattern (month 13...) must fall back to
    mtime, never crash the run. The year bounds are fixed constants — a
    destination must depend only on the file, never on today's clock, or
    re-runs across New Year would re-place files."""
    for rx, fmt in _WA_NAME_RES:
        m = rx.search(name)
        if not m:
            continue
        try:
            dt = datetime.strptime(m.group(1), fmt)
        except ValueError:
            return None
        if 2005 <= dt.year <= 2099:
            return dt
        return None
    return None


def file_date(path, is_photo):
    """Date used for sorting: EXIF for photos when available, then WhatsApp
    filename date, else mtime."""
    if is_photo:
        dt = exif_date(path)
        if dt:
            return dt
    dt = _filename_date(os.path.basename(path))
    if dt:
        return dt
    try:
        return datetime.fromtimestamp(os.path.getmtime(path))
    except (OSError, OverflowError, ValueError):
        # Corrupt mtimes land in 1970/01: an easy-to-spot review bucket
        return datetime(1970, 1, 1)


def _safe_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0

# ---------------------------------------------------------------------------
# AUDIO TAGS (music vs loose audio)
# ---------------------------------------------------------------------------

try:
    import mutagen  # optional: classify music by artist/album tags
    _MUTAGEN_OK = True
except ImportError:
    _MUTAGEN_OK = False

# Tag keys vary by container: easy keys for MP3/MP4, native Vorbis comments
# for FLAC/Ogg/Opus, 'Author'/'WM/...' for WMA (ASF), raw ID3 frames for
# WAV/AIFF, 'Album Artist' (with a space) for APE. Each field is therefore
# looked up through a list of known keys, in preference order.
_ARTIST_KEYS = ("albumartist", "Album Artist", "WM/AlbumArtist", "TPE2",
                "artist", "Author", "TPE1")
_ALBUM_KEYS = ("album", "WM/AlbumTitle", "TALB")

# Characters invalid on Windows/SMB/exFAT (the output often lives on a NAS
# share), plus control characters.
_UNSAFE_CHARS = re.compile(r'[<>:"|?*/\\\x00-\x1f]')
_WIN_RESERVED = ({"CON", "PRN", "AUX", "NUL"}
                 | {"COM%d" % i for i in range(1, 10)}
                 | {"LPT%d" % i for i in range(1, 10)})


def _safe_component(value):
    """Sanitize a tag value into a folder name valid on Windows/SMB/exFAT,
    or None if nothing usable is left."""
    s = unicodedata.normalize("NFC", str(value))  # NFC: one 'Beyoncé', not two
    s = _UNSAFE_CHARS.sub("_", s)
    # Cap first, strip after: truncation must not leave a trailing dot/space
    # (rejected by SMB/Windows).
    s = s[:80].strip(". ")
    if not s:
        return None
    if s.upper() in _WIN_RESERVED:
        s += "_"
    return s


def _first_tag(tags, keys):
    """First key whose value sanitizes to something usable. Value-based, not
    key-based: an empty 'albumartist' must not shadow a valid 'artist'."""
    for key in keys:
        try:
            values = tags.get(key)
            if not values:
                continue
            # str() is uniform across the value types involved: list[str],
            # ID3 TextFrame, ASFUnicodeAttribute, APETextValue.
            component = _safe_component(values[0])
        except Exception:
            continue
        if component:
            return component
    return None


def audio_tags(path):
    """(artist, album) from music tags, sanitized as folder names.
    (None, None) without mutagen, without readable tags, or on any error —
    like exif_date(), nothing here may ever raise."""
    if not _MUTAGEN_OK:
        return None, None
    try:
        audio = mutagen.File(path, easy=True)
        if audio is None or not audio.tags:
            return None, None
        return _first_tag(audio.tags, _ARTIST_KEYS), _first_tag(audio.tags, _ALBUM_KEYS)
    except Exception:
        return None, None

# ---------------------------------------------------------------------------
# HASHING (3-phase dedup)
# ---------------------------------------------------------------------------

def partial_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(PARTIAL_BYTES))
    return h.hexdigest()


def full_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

# ---------------------------------------------------------------------------
# PATH VALIDATION
# ---------------------------------------------------------------------------

def _is_within(child, parent):
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:
        return False


def validate_paths(inputs, output, duplicates_to):
    """Reject overlapping input/destination folders (each run would re-ingest
    its own output) and drop repeated or nested inputs (their files would be
    flagged as duplicates of themselves)."""
    cleaned = []
    for r in inputs:
        rp = os.path.realpath(r)
        if not os.path.isdir(rp):
            print("WARNING: input folder does not exist, skipping: %s" % r, file=sys.stderr)
            continue
        cleaned.append(rp)

    final = []
    # Lexical tie-break: equal-length roots must keep a stable order so the
    # source labels (Photos, Photos_2...) never swap between runs.
    for r in sorted(set(cleaned), key=lambda p: (len(p), p)):
        if any(_is_within(r, prev) for prev in final):
            print("WARNING: '%s' is inside another input; skipping it to avoid processing it twice." % r)
            continue
        final.append(r)

    if not final:
        print("ERROR: no valid input folder.", file=sys.stderr)
        sys.exit(2)

    output_root = os.path.realpath(output) if output else None
    dup_root = os.path.realpath(duplicates_to) if duplicates_to else None
    for what, root_path in (("output", output_root), ("duplicates destination", dup_root)):
        if root_path is None:
            continue
        if os.path.exists(root_path) and not os.path.isdir(root_path):
            print("ERROR: %s exists and is not a folder: %s" % (what, root_path), file=sys.stderr)
            sys.exit(2)
        for r in final:
            if _is_within(root_path, r) or _is_within(r, root_path):
                print("ERROR: input and %s overlap:\n  input: %s\n  %s: %s\n"
                      "Running like this re-ingests it and multiplies files. "
                      "Use a folder outside the inputs." % (what, r, what, root_path), file=sys.stderr)
                sys.exit(2)

    return final, output_root, dup_root

# ---------------------------------------------------------------------------
# CLASSIFICATION AND DESTINATIONS
# ---------------------------------------------------------------------------

def source_labels(inputs):
    """Map each input root to a unique <source> folder name for Files/other/
    and Duplicates/. Two inputs with the same leaf name ('/a/Photos',
    '/b/Photos') become 'Photos' and 'Photos_2' instead of silently merging."""
    labels = {}
    used = set()
    for root in inputs:
        base = os.path.basename(os.path.normpath(root)) or "root"
        label, i = base, 1
        while label in used:
            i += 1
            label = "%s_%d" % (base, i)
        used.add(label)
        labels[root] = label
    return labels


def dest_for(path, input_root, output_root, labels):
    """Destination path based on file type and date."""
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lstrip(".").lower()

    if ext in PHOTO_EXTS:
        dt = file_date(path, is_photo=True)
        return os.path.join(output_root, "Media", "Photos",
                            "%04d" % dt.year, "%02d" % dt.month, name)
    if ext in VIDEO_EXTS:
        dt = file_date(path, is_photo=False)
        return os.path.join(output_root, "Media", "Videos",
                            "%04d" % dt.year, "%02d" % dt.month, name)
    if ext in AUDIO_EXTS:
        artist, album = audio_tags(path)        # (None, None) without mutagen
        if artist:
            parts = ["Media", "Music", artist] + ([album] if album else []) + [name]
            return os.path.join(output_root, *parts)
        # Untagged audio (voice notes, WhatsApp...): by date, like photos
        dt = file_date(path, is_photo=False)
        return os.path.join(output_root, "Media", "Audio",
                            "%04d" % dt.year, "%02d" % dt.month, name)
    if ext in FILE_EXTS:
        return os.path.join(output_root, "Files", ext, name)

    # Unrecognized type -> Files/other/<source>/<relative_path>/
    rel_dir = os.path.relpath(os.path.dirname(path), input_root)
    if rel_dir == ".":
        rel_dir = ""
    source = labels[input_root]
    return os.path.join(output_root, "Files", "other", source, rel_dir, name)


def unique_path(dest, reserved):
    """Append _1, _2... while dest is taken on disk OR already assigned in
    this run; 'reserved' lets dry-run simulate renames like the real run."""
    base, ext = os.path.splitext(dest)
    candidate = dest
    i = 1
    while os.path.exists(candidate) or candidate in reserved:
        candidate = "%s_%d%s" % (base, i, ext)
        i += 1
    reserved.add(candidate)
    return candidate

# ---------------------------------------------------------------------------
# SCANNING
# ---------------------------------------------------------------------------

def scan_files(inputs, warnings):
    """Return a list of (path, input_root) for every regular file."""
    files = []
    seen = set()

    def _dir_error(e):
        warnings["unreadable_dirs"] += 1
        print("WARNING: could not list folder %s (%s)"
              % (getattr(e, "filename", "?"), e), file=sys.stderr)

    for root in inputs:
        for dirpath, dirnames, filenames in os.walk(root, onerror=_dir_error):
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".") and d not in EXCLUDED_DIRS]
            for fn in filenames:
                if fn.startswith("."):
                    continue
                full = os.path.join(dirpath, fn)
                # Moving a symlink could leave a broken link in the output
                if os.path.islink(full):
                    warnings["symlinks"] += 1
                    continue
                # FIFOs/sockets/devices would hang the hashing step
                if not os.path.isfile(full):
                    warnings["special"] += 1
                    continue
                rp = os.path.realpath(full)
                if rp in seen:
                    continue
                seen.add(rp)
                files.append((full, root))
    return files

# ---------------------------------------------------------------------------
# DEDUPLICATION
# ---------------------------------------------------------------------------

def _record_dupes(matches, h, originals, duplicates):
    """Keep the oldest copy as the original; the rest become duplicates."""
    matches.sort(key=lambda t: _safe_mtime(t[0]))
    originals.append(matches[0] + (h,))
    for dup in matches[1:]:
        duplicates.append((dup[0], dup[1], matches[0][0], h))


def find_duplicates(files, warnings):
    """
    Phase 1: group by size (unique sizes need no hashing).
    Phase 2: within each group, hash the first 64 KiB.
    Phase 3: within each subgroup, full SHA-256.
    """
    by_size = defaultdict(list)
    for path, root in files:
        try:
            size = os.path.getsize(path)
        except OSError as e:
            warnings["unreadable"] += 1
            print("WARNING: could not read %s (%s)" % (path, e), file=sys.stderr)
            continue
        by_size[size].append((path, root))

    originals = []
    duplicates = []
    total_groups = sum(1 for g in by_size.values() if len(g) > 1)
    processed = 0

    for size, group in by_size.items():
        if len(group) == 1:
            originals.append(group[0] + (None,))
            continue

        processed += 1
        if processed % 100 == 0:
            print("  ... analyzing candidate group %d/%d"
                  % (processed, total_groups), flush=True)

        # Phase 2: partial hash
        by_partial = defaultdict(list)
        for path, root in group:
            try:
                by_partial[partial_hash(path)].append((path, root))
            except OSError as e:
                warnings["unreadable"] += 1
                print("WARNING: could not read %s (%s)" % (path, e), file=sys.stderr)

        for hp, subgroup in by_partial.items():
            if len(subgroup) == 1:
                originals.append(subgroup[0] + (None,))
                continue
            # A file that fits in the partial read is already fully hashed
            if size <= PARTIAL_BYTES:
                _record_dupes(subgroup, hp, originals, duplicates)
                continue
            # Phase 3: full hash
            by_hash = defaultdict(list)
            for path, root in subgroup:
                try:
                    by_hash[full_hash(path)].append((path, root))
                except OSError as e:
                    warnings["unreadable"] += 1
                    print("WARNING: could not read %s (%s)" % (path, e), file=sys.stderr)
            for h, matches in by_hash.items():
                if len(matches) == 1:
                    originals.append(matches[0] + (h,))
                else:
                    _record_dupes(matches, h, originals, duplicates)

    return originals, duplicates

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def _abort_disk_full():
    print("\nFATAL: destination disk is FULL (ENOSPC). Stopping now instead of "
          "failing file by file for hours.\nFree some space and re-run the same "
          "command: the script resumes where it left off without duplicating anything.",
          file=sys.stderr)
    sys.exit(3)


def main():
    ap = argparse.ArgumentParser(
        description="Organize files by type/date and detect real duplicates (by content).")
    ap.add_argument("-i", "--input", action="append", required=True,
                    help="Input folder (repeatable)")
    ap.add_argument("-o", "--output", default=None,
                    help="Output root folder (organizes originals). Optional if -d is given")
    ap.add_argument("-d", "--duplicates-to", default=None, metavar="PATH",
                    help="Folder where duplicates are set aside. With -o, replaces the "
                         "default <output>/Duplicates. Alone (without -o): extract-only "
                         "mode - originals stay untouched and only the duplicates are "
                         "copied (or moved with --move) there")
    ap.add_argument("--move", action="store_true", help="Move instead of copy")
    ap.add_argument("--dry-run", action="store_true", help="Simulate: touch nothing")
    ap.add_argument("--skip-duplicates", action="store_true",
                    help="Do not relocate duplicates, only record them in the CSV")
    ap.add_argument("--report", default=None,
                    help="CSV report path (with --dry-run, also writes the report)")
    ap.add_argument("--clean-empty-dirs", action="store_true",
                    help="With --move: remove folders left empty in the inputs")
    ap.add_argument("--skip-space-check", action="store_true",
                    help="Do not abort even if the destination seems short on space")
    args = ap.parse_args()
    if not args.output and not args.duplicates_to:
        ap.error("at least one of -o/--output or -d/--duplicates-to is required")

    # Odd filenames must not crash console output on strict-encoding terminals
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass

    inputs, output_root, dup_root = validate_paths(args.input, args.output, args.duplicates_to)
    extract_only = output_root is None
    if dup_root is None:
        dup_root = os.path.join(output_root, "Duplicates")
    labels = source_labels(inputs)
    report_path = os.path.realpath(args.report) if args.report else \
        os.path.join(output_root or dup_root, "duplicates_report.csv")
    action = shutil.move if args.move else shutil.copy2
    verb = "MOVE" if args.move else "COPY"

    if extract_only:
        print("NOTE: extract-only mode (-d without -o): originals stay in place;")
        print("      duplicates are %s to %s\n"
              % ("MOVED" if args.move else "COPIED", dup_root))

    if not _PIL_OK:
        print("NOTE: Pillow is not installed; photos will be dated by WhatsApp filename")
        print("      date or modification time instead of EXIF. For EXIF support:")
        print("      pip3 install Pillow\n")
    elif not _HEIF_OK:
        print("NOTE: without pillow-heif, HEIC/HEIF files are dated by filename/mtime.\n")
    if not _MUTAGEN_OK:
        print("NOTE: mutagen is not installed; music cannot be classified by artist/album")
        print("      and ALL audio goes to Media/Audio/ by date. For tag support:")
        print("      pip3 install mutagen   (on Python <= 3.7, pin mutagen==1.45.1)\n")

    warnings = defaultdict(int)

    print("Scanning input folders...", flush=True)
    files = scan_files(inputs, warnings)
    print("  %d files found." % len(files))

    print("Looking for duplicates by content (SHA-256, 3 phases)...", flush=True)
    originals, duplicates = find_duplicates(files, warnings)
    print("  %d unique files, %d real duplicates.\n" % (len(originals), len(duplicates)))

    # Space check before touching anything (copy mode only)
    if not args.move and not args.dry_run and not args.skip_space_check:
        needed = 0
        if not extract_only:
            for path, _root, _h in originals:
                try:
                    needed += os.path.getsize(path)
                except OSError:
                    pass
        if not args.skip_duplicates:
            for dup, _r, _o, _h in duplicates:
                try:
                    needed += os.path.getsize(dup)
                except OSError:
                    pass
        space_root = dup_root if extract_only else output_root
        try:
            os.makedirs(space_root, exist_ok=True)
            free = shutil.disk_usage(space_root).free
        except OSError:
            free = None
        if free is not None and free < needed * 1.02:
            print("ERROR: not enough space at destination: ~%.1f GiB needed, %.1f GiB free.\n"
                  "       Options: use --move (no copying within the same volume), free up\n"
                  "       space, or force with --skip-space-check at your own risk."
                  % (needed / 2.0 ** 30, free / 2.0 ** 30), file=sys.stderr)
            sys.exit(2)

    # --- Place originals ---
    errors = 0
    reserved = set()   # destinations already assigned in this run
    dest_map = {}      # original -> final destination (for the report)
    count = 0
    to_place = [] if extract_only else originals   # extract-only: leave originals alone
    for path, root, h in to_place:
        count += 1
        if count % 1000 == 0:
            print("  ... processing %d/%d" % (count, len(to_place)), flush=True)

        dest = dest_for(path, root, output_root, labels)
        if os.path.exists(dest):
            # Same content already at the destination? (makes re-runs resumable)
            try:
                if os.path.getsize(dest) == os.path.getsize(path):
                    h_src = h or full_hash(path)
                    if full_hash(dest) == h_src:
                        duplicates.append((path, root, dest, h_src))
                        continue
            except OSError:
                pass
        dest = unique_path(dest, reserved)
        dest_map[path] = dest

        if args.dry_run:
            print("[DRY-RUN] %s: %s -> %s" % (verb, path, dest))
        else:
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                action(path, dest)
            except (OSError, shutil.Error) as e:
                if getattr(e, "errno", None) == errno.ENOSPC:
                    _abort_disk_full()
                errors += 1
                dest_map.pop(path, None)
                print("ERROR with %s: %s" % (path, e), file=sys.stderr)

    # --- CSV report ---
    write_report = (not args.dry_run) or bool(args.report)
    if write_report:
        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        # backslashreplace: non-UTF8 filenames must not break the report
        with open(report_path, "w", newline="", encoding="utf-8", errors="backslashreplace") as f:
            w = csv.writer(f)
            w.writerow(["duplicate", "kept_original", "original_destination", "sha256"])
            for dup, root, orig, h in duplicates:
                w.writerow([dup, orig, dest_map.get(orig, orig), h])
    elif duplicates:
        print("(dry-run: add --report /path/to/report.csv to dump the duplicate list)")

    # --- Relocate duplicates ---
    if not args.skip_duplicates:
        for dup, root, orig, h in duplicates:
            rel = os.path.relpath(dup, root)
            dest = os.path.join(dup_root, labels[root], rel)
            # Copy mode only: with --move the duplicate must always leave the
            # input, even if an identical copy already sits at the destination
            if not args.move and os.path.exists(dest):
                # Same content already set aside? (makes copy-mode re-runs resumable)
                try:
                    if os.path.getsize(dest) == os.path.getsize(dup) and full_hash(dest) == h:
                        continue
                except OSError:
                    pass
            dest = unique_path(dest, reserved)
            if args.dry_run:
                print("[DRY-RUN] duplicate: %s (== %s) -> %s" % (dup, orig, dest))
            else:
                try:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    action(dup, dest)
                except (OSError, shutil.Error) as e:
                    if getattr(e, "errno", None) == errno.ENOSPC:
                        _abort_disk_full()
                    errors += 1
                    print("ERROR with duplicate %s: %s" % (dup, e), file=sys.stderr)

    # --- Remove empty folders after moving ---
    if args.clean_empty_dirs and args.move and not args.dry_run:
        removed = 0
        for root in inputs:
            for dirpath, _dirs, _files in os.walk(root, topdown=False):
                if dirpath == root:
                    continue
                try:
                    os.rmdir(dirpath)   # only removes empty dirs; otherwise fails and is ignored
                    removed += 1
                except OSError:
                    pass
        print("Empty folders removed from inputs: %d" % removed)

    print("\n--- SUMMARY ---")
    print("Unique files %s: %d"
          % ("kept in place" if extract_only else "processed", len(originals)))
    print("Duplicates detected:    %d" % len(duplicates))
    if warnings["symlinks"]:
        print("Symlinks skipped:       %d" % warnings["symlinks"])
    if warnings["special"]:
        print("Special files skipped:  %d (FIFOs, sockets...)" % warnings["special"])
    if warnings["unreadable"] or warnings["unreadable_dirs"]:
        print("Unreadable (see warnings): %d files, %d folders"
              % (warnings["unreadable"], warnings["unreadable_dirs"]))
    if write_report:
        print("Duplicates report:      %s" % report_path)
    if errors:
        print("Errors:                 %d (see messages above)" % errors)
    if args.dry_run:
        print("(Dry run: no files were touched)")

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. You can re-run the same command: the script resumes "
              "without duplicating anything.", file=sys.stderr)
        sys.exit(130)
