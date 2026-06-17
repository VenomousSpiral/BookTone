"""
Core conversion logic for audiobook downloads.

All functions copied verbatim from test_download_conversion.py with minor import adjustments.
Handles OPUS concat, M4B chapter embedding, and MP3 re-encode via ffmpeg.
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time as _time_module
from pathlib import Path

from app.utils.path_utils import resolve_combined_audio_path

logger = logging.getLogger(__name__)


# ─── Real-time progress helpers ──────────────────────────────────────────

import re as _re
from typing import Callable, Optional

# Matches time=<value> where value is HH:MM:SS.ss (3 colon-separated parts)
# ffmpeg outputs like "time=00:01:23.456" — 3 groups separated by colons
_FFMPEG_TIME_RE = _re.compile(r'time=(\S+)')
_FFMPEG_OPENING_RE = _re.compile(rb'Opening .* for reading \(file (.*?)\)')


def _parse_hms(hms: str) -> float:
    """Convert ffmpeg time value (HH:MM:SS.ss or MM:SS.ms) to total seconds."""
    parts = hms.split(':')
    if len(parts) != 3:
        return 0.0
    hours = int(parts[0])
    minutes = int(parts[1])
    sec_str = parts[2]
    if '.' in sec_str:
        s, frac = sec_str.split('.', 1)
        seconds = float(s) + (int(frac.ljust(3, '0')[:3]) / 1000.0)
    else:
        seconds = float(sec_str)
    return hours * 3600 + minutes * 60 + seconds


def run_with_progress(
    cmd: list,
    total_duration_sec: float | None = None,
    input_files: list[str] | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
) -> "subprocess.CompletedProcess[str]":
    """Run ffmpeg with piped stderr and real-time progress tracking.

    Parses ``time=HH:MM:SS`` from ffmpeg's stderr to compute actual percentage
    based on the total audio duration (from ffprobe).  Also tracks file-level
    progress for concat operations that don't re-encode.

    Args:
        cmd: The full ffmpeg command list.
        total_duration_sec: Total expected output duration in seconds. If None,
            will probe input files to estimate it.
        input_files: List of input audio file paths (for file-count estimation).
        progress_callback: Called with (pct_int, message_str) on each update.

    Returns:
        CompletedProcess instance.
    """
    import subprocess as _subprocess
    import threading as _threading
    from io import StringIO as _StringIO

    # Determine total duration for percentage calculation
    dur = total_duration_sec
    has_total = dur is not None and dur > 0

    # If no known duration, probe input files to get a rough estimate (for concat copy mode)
    if not has_total and input_files:
        try:
            total_estimated = sum(probe_duration(f) or 0 for f in input_files[:1])
            if total_estimated > 5:  # only use if we got reasonable data from first file
                dur = total_estimated * len(input_files)
                has_total = True
        except Exception:
            pass

    proc = _subprocess.Popen(
        cmd,
        stdout=_subprocess.PIPE,
        stderr=_subprocess.PIPE,
        text=True,
        start_new_session=False,
    )

    # Background thread to read stderr and parse progress in real-time
    running = True
    last_pct = 0
    file_count_total = len(input_files) if input_files else 0
    files_started = set()

    def reader_thread():
        nonlocal last_pct, file_count_total, files_started
        try:
            while running:
                line = proc.stderr.readline()
                if not line and proc.poll() is not None:
                    break
                line_stripped = line.strip()

                # Parse time= from re-encode output (M4B AAC re-encode, MP3 re-encode)
                m_time = _FFMPEG_TIME_RE.search(line_stripped)
                if m_time and has_total:
                    current_sec = _parse_hms(m_time.group(1))
                    pct = min(95, int((current_sec / dur) * 100))
                    # Throttle updates: only report every ~2% or when near completion
                    if pct != last_pct and (pct % 2 == 0 or pct >= 90):
                        last_pct = pct
                        msg = f"Encoding... {m_time.group(1)}"
                        try:
                            progress_callback(pct, msg)
                        except KeyError:
                            pass  # job was deleted/failed
                    continue

                # For concat (copy mode), track which input files ffmpeg has opened
                if not has_total and file_count_total > 0:
                    m_open = _re.search(
                        r'Opening .* for reading \(file .+\)'
                        , line_stripped,
                    )
                    # Also match: [concat @ ...] Reading block from ...
                    if not m_open:
                        m_open = _re.search(r'Reading block from', line_stripped)

                continue
        except Exception:
            pass  # reader thread dies silently on process exit

    reader_t = _threading.Thread(target=reader_thread, daemon=True)
    reader_t.start()

    stdout_data, stderr_data = proc.communicate()
    running = False
    try:
        reader_t.join(timeout=5)
    except Exception:
        pass

    # For concat copy mode with file-count estimation: update based on output size growth
    if not has_total and input_files and progress_callback:
        # Probe the output to get its current duration vs expected total
        try:
            out_path = None
            for i, arg in enumerate(cmd):
                if arg == '-i':
                    continue  # skip inputs
                # The last non-flag argument is typically the output file
                pass
            # Find output by looking at positional args (after all -i and flags)
            positional = []
            for a in cmd:
                if a.startswith('-'):
                    positional.clear()  # reset on new flag group
                    continue
                positional.append(a)
            out_path = positional[-1] if positional else None

            if out_path and Path(out_path).exists():
                actual_dur = probe_duration(str(Path(out_path))) or 0
                expected_total = dur if has_total else sum(probe_duration(f) for f in input_files[:3]) * max(len(input_files)//3, 1)
                # Estimate total from first few files
                sample_dur = sum((probe_duration(f) or 0) for f in min(5, len(input_files))
                                 ) / min(5, len(input_files)) if input_files else None
                expected_total = (sample_dur * len(input_files)) if sample_dur and sample_dur > 1 else dur

                if actual_dur > 0 and expected_total > 0:
                    pct = min(94, int((actual_dur / expected_total) * 100))
                    try:
                        progress_callback(pct, f"Concatenating... {actual_dur:.0f}s")
                    except KeyError:
                        pass
        except Exception:
            pass

    result = _subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode,
        stdout=(stdout_data if isinstance(stdout_data, str) else (stdout_data.decode('utf-8', errors='replace') if stdout_data else '')),
        stderr=(stderr_data if isinstance(stderr_data, str) else (stderr_data.decode('utf-8', errors='replace') if stderr_data else '')),
    )

    # Final progress marker before completion
    if progress_callback:
        try:
            progress_callback(97, "Finalizing...")
        except KeyError:
            pass


def run(cmd: list, label: str | None = None) -> "subprocess.CompletedProcess[str]":
    """Run a command and return the CompletedProcess."""
    cmd_str = " ".join(str(x) for x in cmd[:10])
    if len(cmd) > 10:
        cmd_str += " ..."
    if label:
        logger.debug("[FFMPEG] %s: %s", label, cmd_str)
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        start_new_session=False,
    )
    return result


def probe_duration(filepath):
    """Get duration of an audio file via ffprobe (seconds)."""
    r = run([
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration,duration",
        "-of", "csv=p=0", filepath,
    ])
    if r.returncode == 0 and r.stdout.strip():
        return float(r.stdout.strip())
    return None


def probe_chapters(filepath):
    """Get chapter metadata via ffprobe.

    Returns list of {id, start_time, end_time, tags} dicts. Uses JSON output first;
    falls back to parsing the text output from ``-v info`` because some containers
    (mov/mp4 on ffmpeg ≥6) do not expose chapters through -show_entries chapter=.
    """
    import re

    # ── Method A: JSON (works for mkv, flac, etc.) ───────────────
    r = run([
        "ffprobe", "-v", "quiet",
        "-show_entries", "chapter=id,start_time,end_time,title",
        "-of", "json", filepath,
    ])
    if r.returncode == 0 and r.stdout.strip():
        try:
            data = json.loads(r.stdout)
            chaps = data.get("chapters", [])
            # Only use JSON chapters if they have valid titles. Ubuntu ffmpeg ≥6
            # returns chapter entries WITHOUT the title field - fall back to text.
            if chaps and any(
                c.get("title") for c in chaps  # type: ignore[index]
            ):
                return chaps
        except json.JSONDecodeError:
            pass

    # ── Method B: text output (works for mov/mp4/mkv) ───────────
    r2 = run(["ffprobe", "-v", "info", filepath])  # stdout + stderr merged via subprocess.run default
    combined = r2.stdout + '\n' + r2.stderr if hasattr(r2, 'stderr') else r2.stdout

    chap_header = re.compile(
        r'Chapter\s+#(\d+):(\d+)\s*:\s*start\s+([\d.]+)\s*,\s*end\s+([\d.]+)',
        re.MULTILINE
    )

    # Split combined output into per-chapter blocks using a lookahead split.
    chap_blocks = re.split(r'(?=Chapter\s+#)', combined)

    found = []
    for block in chap_blocks:
        m_header = re.search(
            r'^Chapter\s+#(\d+):(\d+)\s*:\s*start\s+([\d.]+)\s*,\s*end\s+([\d.]+)',
            block, re.MULTILINE,
        )
        if not m_header:
            continue

        cid = m_header.group(1)
        idx = m_header.group(2)
        start_raw = m_header.group(3)
        end_raw   = m_header.group(4)

        # Extract title from THIS block only (first 300 chars after header line).
        search_area = block[:300]
        title_m = re.search(r'\btitle\s*:\s*(.+)', search_area, re.MULTILINE | re.IGNORECASE)
        tags = {"title": title_m.group(1).strip()} if title_m else {}

        found.append({
            "id": f"CHAPTER_{cid}_{idx}",
            "start_time": float(start_raw),
            "end_time": float(end_raw),
            "tags": tags,
        })
    return found


# ─── CAS helpers: hash-to-index mapping for audio file sorting ──────────


def _load_chunk_hash_to_index(ebook_path: str,
                              cache_base_dir: Path) -> dict[str, int]:
    """Build {content_hash: index} map from parsed ebook data."""
    stream_cache_json = _find_stream_cache_match(
        Path(ebook_path).stem, cache_base_dir)

    if not (stream_cache_json and stream_cache_json.exists()):
        return {}

    try:
        with open(stream_cache_json) as f:
            data = json.load(f)

        chunk_map: dict[str, int] = {}
        for c in data.get("chunks", []):
            h = c.get("_content_hash")
            if h:
                chunk_map[h] = int(c["index"])
        return chunk_map
    except Exception as e:
        logger.warning(
            "[DOWNLOAD] Failed to load chunk hash map: %s", e,
        )
        return {}


def _sort_audio_files_by_index(audio_files: list[Path],
                               hash_to_idx_map: dict[str, int]) -> list[Path]:
    """Sort audio files by their content-hash-to-chunk-index mapping.

    Files whose hashes are in the map get sorted numerically.
    Orphaned files (not in any parsed version) sort last with index=999999.
    """
    def sort_key(p: Path):
        stem = p.stem
        idx = hash_to_idx_map.get(stem, 999999)
        return (0 if stem in hash_to_idx_map else 1, idx)

    return sorted(audio_files, key=sort_key)


# ─── Core conversion logic ──────────────────────────────────────────────

def build_concat_list(audio_files: list[str], output_dir: str) -> Path:
    """Write an ffmpeg concat input file.

    Uses absolute paths so that the concat text file can be read from any working directory,
    and properly escapes single quotes in filenames for safety.
    """
    concat_file = Path(output_dir) / "concat_input.txt"
    with open(concat_file, "w") as f:
        for af in audio_files:
            abs_path = str(Path(af).resolve())
            escaped = abs_path.replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
    return concat_file


def build_chunk_durations(audio_files: list[str]) -> dict[int, float]:
    """Probe each opus file and return {file_index: duration_seconds}.

    Uses sequential file-based indices [0..n-1] as keys so that chapter lookups
    (which reference chunk_time_index positions) resolve correctly.
    """
    durations = {}
    for i, fpath in enumerate(audio_files):
        r = run(["ffprobe", "-v", "quiet",
                 "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(fpath)])
        dur_str = r.stdout.strip()
        if dur_str:
            try:
                durations[i] = float(dur_str)
            except ValueError:
                pass
    return durations


def build_ffmetadata(chapters, chunk_durations, total_duration_sec):
    """Build FFMETADATA1 text from ebook chapters + audio timestamps.

    Parameters:
      chapters          : list of chapter dicts with start_chunk/end_chunk indices
      chunk_durations   : dict mapping chunk_index (int) → duration in seconds for that opus file
      total_duration_sec: total combined audio length in seconds
    """
    # Build sorted list of (chunk_index, start_time_seconds) for O(1) lookups
    sorted_indices = sorted(chunk_durations.keys())
    cum_dur = {}
    running = 0.0
    for idx in sorted_indices:
        cum_dur[idx] = running
        running += chunk_durations[idx]
    if not cum_dur:
        return ";FFMETADATA1"

    # Helper: get cumulative start time for a chunk index with ceiling lookup + interpolation
    def _get_start_sec(chunk_idx):
        if chunk_idx in cum_dur:
            return cum_dur[chunk_idx]
        sorted_keys = list(cum_dur.keys())
        if not sorted_keys:
            return 0.0
        ceiling_key = None
        for k in sorted_keys:
            if k >= chunk_idx:
                ceiling_key = k
                break
        below = [k for k in sorted_keys if k <= chunk_idx]
        above = [k for k in sorted_keys if k > chunk_idx]
        if not below:
            return cum_dur.get(ceiling_key, total_duration_sec)
        if ceiling_key is None or (not above):  # all keys <= chunk_idx
            return cum_dur[max(sorted_keys)]
        bk, ak = max(below), min(above)
        frac = (chunk_idx - bk) / float(max(ak - bk, 1))
        return round(cum_dur[bk] + frac * (cum_dur[ak] - cum_dur[bk]), 3)

    _TITLE_MAP = {
        "preface": "Preface",
        "title page/summary": "Title Page / Summary",
        "epilogue": "Epilogue",
    }

    def _make_chapter_name(raw_name: str, idx: int) -> str:
        if not os.path.splitext(raw_name)[1] and '/' not in raw_name:
            cleaned = ' '.join(raw_name.split()).strip()
            return cleaned if cleaned else f"Section {idx + 1}"

        stem = os.path.splitext(os.path.basename(raw_name))[0]
        lower = stem.lower().replace("_", " ")

        for key, display in _TITLE_MAP.items():
            if key in lower:
                return display

        parts = stem.lower().split("split_")
        if len(parts) > 1 and parts[-1].isdigit():
            num = int(parts[-1]) + 1
            return f"Chapter {num}"

        cleaned = lower.replace("_", " ").title()
        for prefix in ("Two Stars Path To Solace Split",):
            if cleaned.startswith(prefix):
                remainder = cleaned[len(prefix):].strip().lstrip('0')
                return (f"Chapter {remainder}"
                        if remainder and remainder.isdigit() else f"Part {idx + 1}")
        return f"Section {idx + 1}"

    lines = [";FFMETADATA1"]

    for ci, ch in enumerate(chapters):
        name = _make_chapter_name(ch["name"], ci)
        sc = ch.get("start_chunk")
        ec = ch.get("end_chunk")

        start_sec = _get_start_sec(sc) if sc is not None else 0.0

        # End time calculation with interpolation support
        if ec is not None and (ec + 1) in cum_dur:
            end_sec = cum_dur[ec + 1]
        elif ec is not None:
            sorted_keys = list(cum_dur.keys())
            exact_key = min(sorted_keys, key=lambda k: abs(k - (ec + 1))) if sorted_keys else None
            if exact_key == ec + 1 and exact_key in cum_dur:
                end_sec = cum_dur[exact_key]
            elif sorted_keys:
                below = [k for k in sorted_keys if k <= ec + 1]
                above = [k for k in sorted_keys if k > ec + 1]
                if below and above:
                    bk, ak = max(below), min(above)
                    frac = ((ec + 1) - bk) / float(max(ak - bk, 1))
                    end_sec = round(cum_dur[bk] + frac * (cum_dur[ak] - cum_dur[bk]), 3)
                elif above:
                    end_sec = total_duration_sec
                else:
                    end_sec = round(cum_dur[max(sorted_keys)], 3)
            else:
                end_sec = total_duration_sec
        else:
            end_sec = total_duration_sec

        # Clamp to valid range (milliseconds - TIMEBASE=1/1000)
        raw_end_ms = int(round(end_sec * 1000))
        start_ms   = max(0, int(round(start_sec * 1000)))
        min_end    = max(start_ms + 500, start_ms)          # enforce ≥500ms duration
        end_ms     = min(int(total_duration_sec * 1000),
                         max(raw_end_ms, min_end))

        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append(f"START={start_ms}")
        lines.append(f"END={end_ms}")
        lines.append(f"title={name}")

    return "\n".join(lines)


def convert_opus(audio_files: list[str], work_dir: str):
    """Concatenate opus files → combined.opus (copy mode)."""
    concat_file = build_concat_list(audio_files, work_dir)
    output = Path(work_dir) / "combined.opus"

    result = run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy", "-f", "opus",
        "-loglevel", "error",
        str(output),
    ], label="OPUS concat (copy)")

    if concat_file.exists():
        os.unlink(str(concat_file))
    return result, output


def convert_m4b(audio_files: list[str], chapters, total_duration_sec=None,
                chunk_time_index=None, work_dir: str = None):
    """Concatenate opus → MP4(AAC) then embed chapter metadata for M4B.

    Returns a dict with keys: 'success', 'output_path', 'duration',
        'concat_result', 'embed_result', 'chapters_embedded'.
    """
    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="m4b_"))
    temp_mp4 = work / "temp_combined.mp4"
    chapters_file = work / "chapters.txt"
    output_m4b = work / "combined.m4b"

    # Step A: Concat into temp MP4 (requires re-encode to AAC since mp4 container needs it)
    concat_file = build_concat_list(audio_files, str(work))
    result_a = run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c:a", "aac", "-b:a", "96k", "-ar", "44100",
        "-threads", "0",
        "-movflags", "+faststart",
        "-loglevel", "error",
        str(temp_mp4),
    ], label="OPUS→MP4 (AAC re-encode)")

    if concat_file.exists():
        os.unlink(str(concat_file))

    if result_a.returncode != 0:
        dur_probe = probe_duration(str(temp_mp4)) or 30.0
        return {
            "success": False,
            "output_path": None,
            "duration": dur_probe,
            "concat_result": result_a,
            "embed_result": None,
            "chapters_embedded": [],
        }

    # Step B: Get actual audio duration and compute per-chunk timestamps
    if total_duration_sec is None:
        dur = probe_duration(str(temp_mp4))
        if not dur or dur < 1.0:
            dur = 30.0
    else:
        dur = float(total_duration_sec)

    # Compute per-chunk durations from the actual audio files for accurate chapter times
    chunk_durations = build_chunk_durations(audio_files)

    metadata_text = build_ffmetadata(chapters, chunk_durations, dur)
    with open(str(chapters_file), "w") as f:
        f.write(metadata_text + "\n")

    # Step C: Embed chapters into M4B (no re-encode!)
    result_b = run([
        "ffmpeg", "-y",
        "-i", str(temp_mp4),
        "-i", str(chapters_file),
        "-map", "0:a",
        "-map_chapters", "1",
        "-c", "copy",
        "-loglevel", "error",
        str(output_m4b),
    ], label="Embed chapters → M4B")

    # Verify if chapters were actually written (ffmpeg 6.x on Ubuntu silently drops them)
    chapters_found = probe_chapters(str(output_m4b)) or []

    # Cleanup temp files, keep output
    for f_path in [temp_mp4, chapters_file]:
        try:
            if Path(f_path).exists():
                os.unlink(str(f_path))
        except OSError:
            pass

    return {
        "success": result_b.returncode == 0 and dur > 1.0,
        "output_path": output_m4b,
        "duration": dur,
        "concat_result": result_a,
        "embed_result": result_b,
        "chapters_embedded": chapters_found if chapters_found else [],
    }


def convert_mp3(audio_files: list[str], work_dir: str):
    """Concatenate opus → combined.mp3 (re-encode with libmp3lame)."""
    concat_file = build_concat_list(audio_files, work_dir)

    result = run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c:a", "libmp3lame",
        "-b:a", "128k", "-ac", "1",  # mono for audiobook
        "-loglevel", "error",
        str(Path(work_dir) / "combined.mp3"),
    ], label="OPUS→MP3 (re-encode)")

    if concat_file.exists():
        os.unlink(str(concat_file))
    return result, Path(work_dir) / "combined.mp3"


# ─── Module-level singleton for job manager (uses project storage directory) ──

# Go up 4 levels from download_service.py (services → app → backend → project root)
_DEFAULT_JOBS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "storage" / "download_jobs"
_default_cache_base = Path(__file__).resolve().parent.parent.parent.parent / "storage" / "audiobooks"


def get_job_manager():
    """Get or create the default job manager singleton."""
    key = "_download_job_manager_singleton"
    mgr = getattr(sys.modules[__name__], key, None)
    if mgr is None:
        from app.services.job_manager import JobManager  # Avoid circular import at module level
        mgr = JobManager(_DEFAULT_JOBS_DIR, cache_base_dir=_default_cache_base)
        setattr(sys.modules[__name__], key, mgr)
    return mgr


# ─── Chapter Data Pipeline for M4B ──────────────────────────────────────


def _prefer_dir_with_audio(cache_dir: Path) -> Path:
    """When multiple cache dirs match by normalized name, prefer the one with audio files.

    Scans sibling directories and returns whichever has the most .opus or .m4a files.
    Falls back to the provided dir if no siblings have audio data either.
    """
    parent = cache_dir.parent
    stem_prefix = "_stream_cache_"
    # Extract our normalized name from this directory's pattern
    m = _re.match(r'^_stream_cache_(.+?)_[a-fA-F0-9]{8,}$', cache_dir.name)
    if not m:
        return cache_dir  # can't parse - just use what we have

    norm_ebook = _re.sub(r'[^a-z0-9]', '', m.group(1).lower())

    best_count = -1
    best_dir: Path | None = cache_dir

    for sibling in sorted(parent.glob("_stream_cache_*")):
        if not sibling.is_dir():
            continue
        sm = _re.match(r'^_stream_cache_(.+?)_[a-fA-F0-9]{8,}$', sibling.name)
        if not sm:
            continue
        s_norm = _re.sub(r'[^a-z0-9]', '', sm.group(1).lower())
        # Only compare siblings that match the same normalized name
        if norm_ebook != s_norm:
            continue

        audio_count = 0
        for ext in ("opus", "m4a"):
            model_path = sibling / "OminiVoice"
            if not model_path.exists():
                # Try all subdirs to find any voice dir with audio
                for mp in sibling.iterdir():
                    if mp.is_dir():
                        audio_count += len(list(mp.glob(f"*/{ext}"))) + len(list(mp.glob(ext)))
            else:
                audio_count += len(list(model_path.glob("*/*" + ext))) + len(list(model_path.glob(ext)))

        # Prefer directories with more audio files; break ties by hash (higher = newer)
        if audio_count > best_count or (
            audio_count == best_count and sibling.name > cache_dir.name
        ):
            best_count = audio_count
            best_dir = sibling

    return best_dir  # type: ignore[return-value]


def _find_stream_cache_match(
    ebook_stem: str,
    cache_base_dir: Path,
) -> Path | None:
    """Find the best-matching stream-cache JSON for an ebook stem.

    The cache directory naming convention is::
        storage/stream_cache/<ebook_name>_<hash>.json

    To avoid matching similar-named books (e.g. ``Pride_and_Prejudice`` vs
    ``Pride_and_Prejudice_(1)``), we:
      1. Strip the trailing ``_<hex_hash>`` from each cache filename.
      2. Normalize both names to lowercase-alphanumeric for comparison.
      3. Require an **exact** match or a prefix where no extra words follow
         our name (rejects ``Pride and Prejudice 1 ...`` when looking for
         ``Pride_and_Prejudice``).
    When multiple candidates have the same normalized name, prefer the one
    with actual chunk data.
    """
    import re

    # Normalized: lowercase + only alphanumeric characters
    norm_ebook = re.sub(r'[^a-z0-9]', '', ebook_stem.lower())

    stream_cache_dir = cache_base_dir.parent / "stream_cache"
    if not stream_cache_dir.exists():
        return None

    candidates: list[tuple[Path, str]] = []  # (path, base_name_without_hash)

    for f in sorted(stream_cache_dir.glob("*.json")):
        if "_with_images" in f.stem:
            continue

        stem_no_ext = f.stem  # e.g. ``Pride if New Game Plus b1a63a7e76b2``

        # Strip trailing _<hex_hash> (hash is ≥8 hex chars)
        m = re.match(r'^(.+?)_([a-fA-F0-9]{8,})$', stem_no_ext.strip())
        if not m:
            continue  # no hash pattern - skip

        name_part = m.group(1).strip()  # e.g. ``Pride if New Game Plus`` or
                                        #               ``Pride if New Game Plus 1``
        norm_base = re.sub(r'[^a-z0-9]', '', name_part.lower())

        candidates.append((f, norm_base))

    all_matches: list[Path] = []
    best_prefix: Path | None = None

    for f, norm_base in sorted(candidates, key=lambda x: (-len(x[1]), str(x[0]))):
        # 1) Exact match after normalization — collect candidates, prefer one with data
        if norm_ebook == norm_base:
            all_matches.append(f)
            continue

        # 2) Prefix match - our ebook name is a prefix of the cache base name.
        #    But reject if extra alphanumeric characters follow (those indicate
        #    a different file, e.g. ``(1)`` variant).
        if norm_base.startswith(norm_ebook):
            remainder = norm_base[len(norm_ebook):]
            if remainder == "":
                best_prefix = f  # exact prefix with nothing after → perfect match
            # If the cache entry has extra chars, only accept if our ebook name
            # is *longer* than (or equal to) the base - meaning this candidate
            # is a broader/more generic match and shouldn't win over an exact.
            # In practice we want strict prefix: our name fully contained at start.

    # If multiple JSONs matched by normalized name, prefer the one with more chunk data (latest version)
    if all_matches:
        best_with_data: Path | None = None
        max_chunks = -1
        for m in sorted(all_matches):
            try:
                with open(m) as fj:
                    d = json.load(fj)
                chunks_count = len(d.get("chunks", []))
                if chunks_count > 0 and chunks_count > max_chunks:
                    best_with_data = m
                    max_chunks = chunks_count
            except Exception:
                continue
        return best_with_data or all_matches[0]

    # Fall back to prefix match, then any candidate with data
    if best_prefix:
        return best_prefix

    for f in sorted(all_matches):
        try:
            with open(f) as fj:
                d = json.load(fj)
            if len(d.get("chunks", [])) > 0:
                return f
        except Exception:
            continue
    return all_matches[0] if all_matches else best


def _find_audio_cache_dir(
    ebook_stem: str,
    cache_base_dir: Path,
) -> Path | None:
    """Find the audio-cache directory for an ebook stem.

    Cache dirs are named ``_stream_cache_<ebook_name>_<hash>/``. Uses the same
    precise matching logic as ``_find_stream_cache_match()`` so that
    ``Pride_and_Prejudice.epub`` doesn't match the ``(1)`` variant's directory.

    When multiple directories have the same normalized name (e.g., different
    ebook versions), prefers the one with actual audio files.
    """
    import re

    norm_ebook = re.sub(r'[^a-z0-9]', '', ebook_stem.lower())

    candidates: list[tuple[Path, str]] = []  # (path, base_name_without_hash)

    for f in sorted(cache_base_dir.glob("_stream_cache_*")):
        if not f.is_dir():
            continue

        # Strip leading _stream_cache_ and trailing _<hash>
        m = re.match(r'^_stream_cache_(.+?)_([a-fA-F0-9]{8,})$', f.name)
        if not m:
            continue  # doesn't follow naming convention - skip

        name_part = m.group(1).strip()
        norm_base = re.sub(r'[^a-z0-9]', '', name_part.lower())
        candidates.append((f, norm_base))

    for f, norm_base in sorted(candidates, key=lambda x: (-len(x[1]), str(x[0]))):
        if norm_ebook == norm_base:
            # Multiple dirs may match by normalized name. Prefer the one with audio files.
            return _prefer_dir_with_audio(f)

        if norm_base.startswith(norm_ebook) and norm_base[len(norm_ebook):] == "":
            return f  # exact prefix → perfect match

    return None


def load_chapters_for_conversion(ebook_path: str, cache_base_dir: Path):
    """Load chapters and timing data for M4B chapter embedding.

    Returns (chapters_list, chunk_time_index_dict).

    Priority:
      1. Real book chapters from stream_cache JSON ``chapters`` field (has actual
         EPUB spine filenames like ``Chapter_003.xhtml``, correct start_chunk/end_chunk,
         and character offsets - this gives proper chapter names in the M4B).
      2. Synthetic "Section N" chapters split evenly across chunks if no real
         chapters are available but chunk data exists.
    """
    stream_cache_json = _find_stream_cache_match(
        Path(ebook_path).stem,
        cache_base_dir,
    )

    chunks_data: list = []
    chunk_time_index: dict[int, float] = {}
    chapters_list: list[dict] = []

    if stream_cache_json and stream_cache_json.exists():
        try:
            with open(stream_cache_json) as f:
                data = json.load(f)

            chunks_data = list(data.get("chunks", []) or [])

            # Build chunk_time_index from cached data if available
            cti_raw = data.get("chunk_time_index")
            if isinstance(cti_raw, list):
                for entry in cti_raw:
                    if isinstance(entry, dict) and "start_idx" in entry and "duration" in entry:
                        chunk_time_index[entry["start_idx"]] = float(entry["duration"])

            # ★ PRIORITY 1: Use real book chapters from the stream cache.
            # These come from parsing the EPUB's NCX/toc during streaming,
            # so they have actual chapter names (e.g. "Chapter_03.xhtml")
            # and correct start_chunk/end_chunk indices for accurate timestamps.
            raw_chapters = data.get("chapters", [])
            if isinstance(raw_chapters, list) and len(raw_chapters) > 0:
                for ch in raw_chapters:
                    chapters_list.append({
                        "name": ch.get("name", f"Section {len(chapters_list) + 1}"),
                        "start_chunk": int(ch.get("start_chunk", 0)),
                        "end_chunk": int(ch.get("end_chunk", len(chunks_data) - 1)),
                    })
                logger.info(
                    "[DOWNLOAD] Loaded %d real book chapters from stream cache JSON",
                    len(chapters_list),
                )
            else:
                raise KeyError("No 'chapters' key in cached data")

        except Exception as e:
            logger.warning(
                "[DOWNLOAD] Failed to load chapters from stream cache JSON: %s. "
                "Will fall back to synthetic chapter split.",
                e,
            )

    # ★ FALLBACK: No real chapters available - create even splits across chunks
    if not chapters_list and len(chunks_data) > 0:
        total_chunks = len(chunks_data)
        num_chapters = max(3, min(total_chunks // 4 + 2, 15))
        step = (total_chunks - 1) / max(num_chapters - 1, 1) if num_chapters > 1 else total_chunks

        for ci in range(num_chapters):
            start_chunk_idx = int(round(ci * step)) if num_chapters > 1 else 0
            end_chunk_idx = min(int(round((ci + 1) * step)), total_chunks - 1)
            if ci == num_chapters - 1:
                end_chunk_idx = total_chunks - 1

            chapters_list.append({
                "name": f"Section {ci + 1}",
                "start_chunk": start_chunk_idx,
                "end_chunk": end_chunk_idx,
            })
        logger.info(
            "[DOWNLOAD] Using %d synthetic chapter splits (no real chapters found)",
            len(chapters_list),
        )

    if not chapters_list:
        logger.warning("[DOWNLOAD] No chapter data available - M4B will have no chapters")
    else:
        logger.info(
            "[DOWNLOAD] Final chapter list: %d entries, chunk_time_index has %d keys",
            len(chapters_list),
            len(chunk_time_index),
        )
    return chapters_list, chunk_time_index


# ─── Orchestrator: bridges routes and converters with REAL progress tracking ──


def _run_download_job(
    job_id: str,
    ebook_path: str,
    model_name: str,
    voice: str,
    format_type: str,
    cache_base_dir: Path,
    jobs_manager,
):
    """Background thread function - runs the full conversion with REAL progress tracking.

    Uses ffmpeg's own stderr output (time= lines) to calculate actual percentage
    during re-encoding operations. For concat-copy mode (OPUS), estimates from file count.
    
    Progress flow:
      1. Loading audio files     → 5%
      2. Found N files           → 10%  
      3a. OPUS concat copy       → 10-97% estimated by output size vs input total
      3b. M4B AAC re-encode      → 10-85% from ffmpeg time= lines (real progress)
         Chapter embedding        → 86-97% fast, no real-time data available
      3c. MP3 re-encode          → 10-97% from ffmpeg time= lines (real progress)
      4. Move to final path       → 98-100%
    """
    import subprocess as _sp

    work_dir = None

    def update_job(pct: int, msg: str):
        """Safely update job state."""
        try:
            jobs_manager.update_job(job_id, progress_pct=pct, message=msg)
        except KeyError:
            pass  # Job no longer exists

    def _probe_total_duration(audio_files: list[str]) -> float | None:
        """Probe the first few audio files to estimate total duration."""
        sample = min(5, len(audio_files))
        if sample == 0:
            return None
        durs = [probe_duration(f) for f in audio_files[:sample]]
        valid = [d for d in durs if d and d > 1]
        if not valid:
            return None
        avg_dur = sum(valid) / len(valid)
        total_est = round(avg_dur * len(audio_files), 2)
        return total_est

    def _run_ffmpeg_read_stderr(cmd: list, timeout_sec: int = 3600):
        """Run ffmpeg with piped stderr and yield (line_str, matched_time_or_None)."""
        proc = _sp.Popen(
            cmd, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True,
        )
        try:
            while True:
                line = proc.stderr.readline()
                if not line and proc.poll() is not None:
                    break
                m_time = _FFMPEG_TIME_RE.search(line.strip())
                yield line.strip(), m_time.group(1) if m_time else None
        except Exception:
            pass

    try:
        # ═══════ PHASE 1: Load audio files ═══════
        update_job(5, "Loading audio files...")
        
        base_cache = _find_audio_cache_dir(Path(ebook_path).stem, cache_base_dir)
        
        found_files: list[Path] = []
        if base_cache:
            model_path = base_cache / model_name
            voice_path = model_path / voice
            # Broader pattern catches both legacy position-based and new hash-named files
            for ext in ("opus", "m4a"):
                found_files.extend(voice_path.glob(f"*.{ext}"))

        # CAS: sort audio files by chunk-index from parsed ebook metadata,
        # not by filename pattern (hashes have no embedded positions)
        hash_to_idx_map = _load_chunk_hash_to_index(ebook_path, cache_base_dir)
        audio_sorted = _sort_audio_files_by_index(found_files, hash_to_idx_map)

        if not audio_sorted:
            update_job(0, None)  # clears progress
            jobs_manager.update_job(job_id, status="failed",
                                    error_message="No cached audio files found")
            return

        str_audio = [str(f) for f in audio_sorted]
        
        # Estimate total duration from first few input files (used by re-encode % calc)
        est_total_dur = _probe_total_duration(str_audio)  # type: ignore[assignment]

        update_job(10, f"Found {len(str_audio)} audio files ({format_type.upper()} conversion)")

        import tempfile as _tfm
        work_dir = str(_tfm.mkdtemp(prefix=f"dld_{job_id}_"))

        output_path: Path | None = None
        success = False

        # ═══════ PHASE 2: Run conversion ═══════
        
        if format_type == "opus":
            # ── OPUS concat (copy mode) — no re-encode, ffmpeg emits NO time= lines ──
            update_job(15, "Concatenating OPUS chunks...")

            concat_file = build_concat_list(str_audio, work_dir)
            out_local = Path(work_dir) / "combined.opus"

            # Use -loglevel warning so ffmpeg emits file-opening lines we can count.
            proc = _sp.Popen(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", str(concat_file),
                 "-c", "copy", "-f", "opus",
                 "-loglevel", "warning", str(out_local)],
                stdout=_sp.PIPE, stderr=_sp.PIPE, text=True)

            # For concat-copy: estimate progress by output file size vs expected total input size.
            input_total_size = sum(
                os.path.getsize(f) for f in str_audio[:min(10, len(str_audio))]
            ) if str_audio else 1

            last_reported_pct = -1
            while True:
                line = proc.stderr.readline()
                if not line and proc.poll() is not None:
                    break

                # Probe output file size for progress estimate (every few lines to avoid stat overhead)
                try:
                    if out_local.exists():
                        cur_size = out_local.stat().st_size
                        pct = min(94, int((cur_size / max(input_total_size, 1)) * 85))
                        # Only update every ~2% to avoid excessive polling
                        if pct != last_reported_pct and (pct % 3 == 0 or pct >= 90):
                            last_reported_pct = pct
                            try:
                                jobs_manager.update_job(job_id, progress_pct=pct,
                                                        message=f"Concatenating OPUS... {cur_size / 1024:.0f} KB")
                            except KeyError:
                                break
                except OSError:
                    pass
            
            # Wait for OPUS concat to finish and check result
            proc.wait()
            if not out_local.exists() or out_local.stat().st_size < 100:
                logger.error("[DOWNLOAD] OPUS concat failed (exit=%s, output missing/too small)",
                           proc.returncode)
        
        elif format_type == "m4b":
            # ── M4B: AAC re-encode (HAS time=) + chapter embed (fast copy, NO time=) ──

            update_job(5, "Loading chapter metadata...")
            
            try:
                chapters_list, chunk_time_index = load_chapters_for_conversion(ebook_path, cache_base_dir)  # type: ignore[misc]
            except Exception as e:
                logger.warning("[DOWNLOAD] Failed to load M4B chapters: %s", e)
                chapters_list = []

            work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="m4b_"))
            temp_mp4 = work / "temp_combined.mp4"
            ch_file = work / "chapters.txt"
            out_m4b = work / "combined.m4b"

            # Step A: AAC re-encode (this is the slow part — ~70% of total time)
            concat_file = build_concat_list(str_audio, str(work))

            update_job(15, "Encoding to MP4 (AAC)...")

            proc_aac = _sp.Popen(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", str(concat_file),
                 "-c:a", "aac", "-b:a", "96k", "-ar", "44100",
                 "-threads", "0",
                 "-movflags", "+faststart",
                 "-loglevel", "error", str(temp_mp4)],
                stdout=_sp.PIPE, stderr=_sp.PIPE, text=True)

            # Read stderr in real-time for time= progress (or fallback to size-based estimate)
            last_pct = 15
            input_size_est = sum(os.path.getsize(f) for f in str_audio[:min(5, len(str_audio))]) if str_audio else 0
            aac_stderr_lines: list[str] = []
            while True:
                line_raw = proc_aac.stderr.readline()
                if not line_raw and proc_aac.poll() is not None:
                    break
                stripped = line_raw.strip()
                m_time = _FFMPEG_TIME_RE.search(stripped)
                pct = -1  # sentinel: no update needed
                
                if m_time and est_total_dur and est_total_dur > 0:
                    # Primary: real time-based progress from ffmpeg output
                    cur_sec = _parse_hms(m_time.group(1))
                    pct_raw = (cur_sec / max(est_total_dur, 1)) * 85.0
                    pct = min(85, int(pct_raw))
                elif input_size_est > 0 and temp_mp4.exists():
                    # Fallback: estimate from output file size growth when duration unknown
                    cur_size = temp_mp4.stat().st_size
                    pct = min(82, int((cur_size / max(input_size_est, 1)) * 75))
                
                if pct > last_pct and (pct % 3 == 0 or pct >= 80):
                    last_pct = pct
                    display_time = m_time.group(1) if m_time else f"{temp_mp4.stat().st_size // 1024} KB"
                    try:
                        jobs_manager.update_job(job_id, progress_pct=pct,
                                                message=f"Encoding to MP4... {display_time}")
                    except KeyError:
                        break
                aac_stderr_lines.append(stripped)

            proc_aac.wait()
            os.unlink(str(concat_file)) if concat_file.exists() else None

            result_a_rc = proc_aac.returncode

            if result_a_rc != 0 or not temp_mp4.exists():
                # AAC encoding failed — clean up and fail early
                for fp in [temp_mp4, ch_file]:
                    try: Path(fp).unlink(missing_ok=True)
                    except OSError: pass
                
                error_msg = f"ffmpeg exit {result_a_rc}" if result_a_rc else "AAC re-encode failed"
                # Try to extract ffmpeg stderr from collected lines for diagnostics
                if aac_stderr_lines:
                    last_err = [l for l in reversed(aac_stderr_lines) 
                               if any(kw in l.lower() for kw in ('error', 'failed', 'invalid'))]
                    if last_err:
                        error_msg += f": {last_err[0][:200]}"

                jobs_manager.update_job(job_id, status="failed", progress_pct=35,
                                        message=f"M4B conversion failed - AAC re-encode",
                                        error_message=error_msg)
                return

            # Step B: Compute per-chunk durations and build chapter metadata
            actual_dur = probe_duration(str(temp_mp4)) or (est_total_dur if est_total_dur else 30.0)
            chunk_durations = build_chunk_durations(str_audio)
            meta_text = build_ffmetadata(chapters_list, chunk_durations, actual_dur)

            with open(str(ch_file), "w") as f:
                f.write(meta_text + "\n")

            # Step C: Embed chapters (copy mode — fast, ~10% of time)
            update_job(86, "Embedding chapter metadata...")

            try:
                proc_ch = _sp.Popen(
                    ["ffmpeg", "-y", "-i", str(temp_mp4), "-i", str(ch_file),
                     "-map", "0:a", "-map_chapters", "1",
                     "-c", "copy", "-loglevel", "error", str(out_m4b)],
                    stdout=_sp.PIPE, stderr=_sp.PIPE, text=True)
                proc_ch.wait()
            except Exception as e:
                logger.error("[DOWNLOAD] Chapter embedding error: %s", e)

            chapters_found = probe_chapters(str(out_m4b)) if out_m4b.exists() else []

            # Clean up temp files (keep final output for now — moved in phase 3)
            try:
                Path(temp_mp4).unlink(missing_ok=True)
            except OSError: pass
            try:
                Path(ch_file).unlink(missing_ok=True)
            except OSError: pass

            # Even if chapter embedding failed on some ffmpeg versions, audio might be OK.
            embed_rc = proc_ch.returncode if 'proc_ch' in dir() else 0
            success = True  # Audio is always good (AAC succeeded above)
            out_m4b_path = out_m4b if out_m4b.exists() and out_m4b.stat().st_size > 100 else None

        elif format_type == "mp3":
            # ── MP3 re-encode (HAS time= lines) ──
            update_job(15, "Re-encoding to MP3...")

            concat_file = build_concat_list(str_audio, work_dir)
            out_mp3 = Path(work_dir) / "combined.mp3"

            proc_mp3 = _sp.Popen(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", str(concat_file),
                 "-c:a", "libmp3lame", "-b:a", "128k", "-ac", "1",
                 "-loglevel", "error", str(out_mp3)],
                stdout=_sp.PIPE, stderr=_sp.PIPE, text=True)

            # Read stderr in real-time for time= progress (or fallback to size-based estimate)
            last_pct = 15
            input_size_est_mp3 = sum(os.path.getsize(f) for f in str_audio[:min(5, len(str_audio))]) if str_audio else 0
            while True:
                line_raw = proc_mp3.stderr.readline()
                if not line_raw and proc_mp3.poll() is not None:
                    break
                stripped = line_raw.strip()
                m_time = _FFMPEG_TIME_RE.search(stripped)
                pct = -1  # sentinel: no update needed
                
                if m_time and est_total_dur and est_total_dur > 0:
                    # Primary: real time-based progress from ffmpeg output
                    cur_sec = _parse_hms(m_time.group(1))
                    pct = min(95, int((cur_sec / max(est_total_dur, 1)) * 100))
                elif input_size_est_mp3 > 0 and out_mp3.exists():
                    # Fallback: estimate from output file size growth
                    cur_size = out_mp3.stat().st_size
                    pct = min(94, int((cur_size / max(input_size_est_mp3, 1)) * 90))
                
                if pct > last_pct and (pct % 3 == 0 or pct >= 90):
                    last_pct = pct
                    display_time = m_time.group(1) if m_time else f"{out_mp3.stat().st_size // 1024} KB"
                    try:
                        jobs_manager.update_job(job_id, progress_pct=pct,
                                                message=f"Re-encoding to MP3... {display_time}")
                    except KeyError:
                        break

            proc_mp3.wait()
            os.unlink(str(concat_file)) if concat_file.exists() else None

            success = proc_mp3.returncode == 0 and out_mp3.exists() and out_mp3.stat().st_size > 100
        
        # ═══════ PHASE 3: Move output or report failure ═══════
        
        try:
            if format_type == "m4b":
                output_path = out_m4b_path
            elif format_type == "opus" and 'out_local' in dir() and out_local.exists():
                output_path = out_local
            elif format_type == "mp3" and 'out_mp3' in dir() and out_mp3.exists():
                output_path = out_mp3

            # Compute the actual content hash of the ebook to match directory naming
            import hashlib as _hashlib
            full_ebook = Path(ebook_path).resolve()
            if not full_ebook.exists():
                full_ebook = cache_base_dir.parent / "ebooks" / ebook_path  
            file_hash = _hashlib.md5(full_ebook.read_bytes()).hexdigest()[:12] if full_ebook.exists() else ""

            safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in Path(ebook_path).stem)[:50]
            combined_path = cache_base_dir / f"_stream_cache_{safe_stem}_{file_hash}" / model_name / voice / f"combined.{format_type}"
            combined_path.parent.mkdir(parents=True, exist_ok=True)

            if output_path and os.path.exists(str(output_path)):
                shutil.move(str(output_path), str(combined_path))
                jobs_manager.update_job(
                    job_id, status="ready", progress_pct=100,
                    message=f"Conversion complete ({format_type.upper()})",
                    output_file=str(combined_path),
                )
                logger.info("[DOWNLOAD] Job %s completed: %.2f MB", 
                           job_id, combined_path.stat().st_size / (1024*1024))

            else:
                error_msg = f"{format_type.upper()} conversion failed — output file missing"
                
                # Try to extract useful error from ffmpeg stderr if available
                if format_type == "opus":
                    try:
                        rc_str = str(proc.returncode) if 'proc' in dir() and hasattr(proc, 'returncode') else "?"
                        error_msg += f" (ffmpeg exit {rc_str})"
                    except Exception: pass

                jobs_manager.update_job(
                    job_id, status="failed", progress_pct=50,
                    message=f"{format_type.upper()} conversion failed",
                    error_message=error_msg,
                )
        finally:
            # Cleanup temp work dir (move already happened above if successful)
            pass

    except Exception as e:
        logger.error("[DOWNLOAD] Job %s fatal error: %s\n%s", job_id, e,
                     __import__("traceback").format_exc())
        try:
            jobs_manager.update_job(job_id, status="failed", progress_pct=0,
                                    error_message=f"Fatal error: {str(e)[:500]}")
        except KeyError:
            pass

    finally:
        if work_dir and os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)


