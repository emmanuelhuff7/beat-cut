"""
Video compositor — single-pass FFmpeg rendering with hardware acceleration.

Instead of decoding frames to Python (moviepy), this builds one FFmpeg
filter_complex that handles all cuts, scaling, cropping, text, and color
grading in a single native process. Uses macOS VideoToolbox for HW encoding.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading

TIKTOK_WIDTH = 1080
TIKTOK_HEIGHT = 1920
TIKTOK_FPS = 30

# Common ffmpeg locations on macOS — shutil.which() fails inside .app bundles
# because PATH is stripped
FFMPEG_SEARCH_PATHS = [
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/usr/bin/ffmpeg",
]


def _find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for p in FFMPEG_SEARCH_PATHS:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    raise FileNotFoundError(
        "ffmpeg not found. Install with: brew install ffmpeg"
    )


def render(
    timeline: list[dict],
    audio_path: str,
    output_path: str,
    text_overlays: list[dict] | None = None,
    lut_path: str | None = None,
    grade_preset: str | None = None,
    grade_adjustments: dict | None = None,
    progress_callback=None,
) -> str:
    ffmpeg = _find_ffmpeg()

    if not timeline:
        raise ValueError("No timeline segments to render. Check that beats were detected in the audio.")

    if progress_callback:
        progress_callback(0.0, "Probing source videos...")

    # 1. Probe all unique source videos for duration/resolution
    unique_videos = list(dict.fromkeys(seg["video_path"] for seg in timeline))
    probe_cache = {}
    for vp in unique_videos:
        probe_cache[vp] = _probe(ffmpeg, vp)

    # 2. Compute total output duration for progress tracking
    total_duration = timeline[-1]["end_time"] if timeline else 0.0

    if progress_callback:
        progress_callback(0.02, "Building render graph...")

    # 3. Build the FFmpeg filter_complex and input list
    # num_video_inputs + 1 audio = total fixed inputs before text PNGs
    num_fixed_inputs = len(unique_videos) + 1
    inputs, filter_complex, last_label, text_png_paths = _build_filtergraph(
        timeline, probe_cache, text_overlays, total_duration,
        grade_preset, grade_adjustments, lut_path,
        num_video_inputs=num_fixed_inputs,
    )

    # 4. Build the full command
    cmd = [ffmpeg, "-y"]

    # Input files: all unique videos + the audio + text PNGs
    for vp in unique_videos:
        cmd += ["-i", vp]
    audio_input_idx = len(unique_videos)
    cmd += ["-i", audio_path]
    for png_path in text_png_paths:
        cmd += ["-i", png_path]

    # Filter complex
    cmd += ["-filter_complex", filter_complex]

    # Map video from filter output, audio from the audio input
    cmd += ["-map", f"[{last_label}]"]
    cmd += ["-map", f"{audio_input_idx}:a"]

    # Try hardware encoder first, fall back to fast software
    hw_available = _check_videotoolbox(ffmpeg)
    if hw_available:
        cmd += ["-c:v", "h264_videotoolbox", "-q:v", "65"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "18"]

    cmd += [
        "-c:a", "aac", "-b:a", "192k",
        "-r", str(TIKTOK_FPS),
        "-t", f"{total_duration:.3f}",
        "-movflags", "+faststart",
        "-shortest",
    ]

    # Progress pipe
    progress_path = tempfile.mktemp(suffix=".log")
    cmd += ["-progress", progress_path]
    cmd += [output_path]

    if progress_callback:
        progress_callback(0.05, f"Rendering ({('HW' if hw_available else 'SW')} encode)...")

    # 5. Run FFmpeg with real-time progress parsing
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Monitor progress in a thread
    if progress_callback and total_duration > 0:
        stop_event = threading.Event()
        monitor = threading.Thread(
            target=_monitor_progress,
            args=(progress_path, total_duration, progress_callback, stop_event),
            daemon=True,
        )
        monitor.start()

    stdout, stderr = proc.communicate()

    if progress_callback and total_duration > 0:
        stop_event.set()
        monitor.join(timeout=2)

    # Clean up progress file and temp text PNGs
    if os.path.exists(progress_path):
        os.remove(progress_path)
    for png_path in text_png_paths:
        if os.path.exists(png_path):
            os.remove(png_path)

    if proc.returncode != 0:
        err = stderr.decode(errors="replace")[-800:]
        raise RuntimeError(f"FFmpeg render failed:\n{err}")

    if progress_callback:
        progress_callback(1.0, "Done!")

    return output_path


def _probe(ffmpeg: str, path: str) -> dict:
    """Get video metadata via ffprobe."""
    ffprobe = ffmpeg.replace("ffmpeg", "ffprobe")
    cmd = [
        ffprobe, "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration,r_frame_rate",
        "-show_entries", "format=duration",
        "-of", "json",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)

    stream = data.get("streams", [{}])[0]
    fmt = data.get("format", {})

    # Duration from stream or format
    dur = float(stream.get("duration", 0) or fmt.get("duration", 0))
    w = int(stream.get("width", 1920))
    h = int(stream.get("height", 1080))

    return {"width": w, "height": h, "duration": dur}


def _build_filtergraph(
    timeline: list[dict],
    probe_cache: dict,
    text_overlays: list[dict] | None,
    total_duration: float,
    grade_preset: str | None,
    grade_adjustments: dict | None,
    lut_path: str | None,
    num_video_inputs: int = 0,
) -> tuple[list[str], str, str, list[str]]:
    """
    Build an FFmpeg filter_complex string that does everything in one pass:
    trim → scale → crop → concat → grade → text (via PNG overlay)

    Returns (unique_videos, filter_complex, last_label, text_png_paths).
    text_png_paths are temp PNG files that must be added as extra -i inputs
    AFTER the video and audio inputs.
    """
    unique_videos = list(dict.fromkeys(seg["video_path"] for seg in timeline))
    video_idx_map = {vp: i for i, vp in enumerate(unique_videos)}

    filter_parts = []
    concat_inputs = []

    for i, seg in enumerate(timeline):
        vp = seg["video_path"]
        idx = video_idx_map[vp]
        info = probe_cache[vp]

        src_dur = info["duration"]
        offset = seg["video_offset"]
        seg_dur = seg["duration"]

        # Handle looping: wrap offset within source duration
        actual_offset = offset % src_dur if src_dur > 0 else 0

        # If segment would overrun source, we take what's available
        avail = src_dur - actual_offset
        trim_dur = min(seg_dur, avail) if avail > 0 else seg_dur
        end_t = actual_offset + trim_dur

        # Build scale+crop filter for 9:16
        scale_crop = _scale_crop_filter(info["width"], info["height"])

        seg_label = f"s{i}"

        # Trim the source, then scale/crop
        filt = (
            f"[{idx}:v]trim=start={actual_offset:.4f}:end={end_t:.4f},"
            f"setpts=PTS-STARTPTS,{scale_crop},"
            f"setsar=1,fps={TIKTOK_FPS}"
            f"[{seg_label}]"
        )
        filter_parts.append(filt)

        concat_inputs.append(f"[{seg_label}]")

    # Concat all segments
    n = len(concat_inputs)
    concat_label = "merged"
    concat_str = "".join(concat_inputs) + f"concat=n={n}:v=1:a=0[{concat_label}]"
    filter_parts.append(concat_str)

    current_label = concat_label

    # Color grading (applied in the same pass)
    grade_filter = _build_grade_filter(grade_preset, grade_adjustments, lut_path)
    if grade_filter:
        next_label = "graded"
        filter_parts.append(f"[{current_label}]{grade_filter}[{next_label}]")
        current_label = next_label

    # Text overlays via Pillow PNG + overlay filter
    text_png_paths = []
    if text_overlays:
        png_paths, txt_filters = _build_text_overlays(
            text_overlays, total_duration, num_video_inputs, current_label,
        )
        if txt_filters:
            text_png_paths = png_paths
            filter_parts.extend(txt_filters)
            # The last overlay label is "txt{N-1}"
            current_label = f"txt{len(png_paths) - 1}"

    full_filter = ";\n".join(filter_parts)
    return unique_videos, full_filter, current_label, text_png_paths


def _scale_crop_filter(src_w: int, src_h: int) -> str:
    """Build FFmpeg scale+crop filter to fill 1080x1920."""
    target_ratio = TIKTOK_WIDTH / TIKTOK_HEIGHT  # 0.5625
    src_ratio = src_w / src_h if src_h > 0 else 1.0

    if src_ratio > target_ratio:
        # Wider than 9:16 — scale to height, crop width
        return (
            f"scale=-2:{TIKTOK_HEIGHT}:flags=bilinear,"
            f"crop={TIKTOK_WIDTH}:{TIKTOK_HEIGHT}"
        )
    else:
        # Taller — scale to width, crop height
        return (
            f"scale={TIKTOK_WIDTH}:-2:flags=bilinear,"
            f"crop={TIKTOK_WIDTH}:{TIKTOK_HEIGHT}"
        )


def _build_grade_filter(
    preset_id: str | None,
    adjustments: dict | None,
    lut_path: str | None,
) -> str:
    """Build the FFmpeg filter string for color grading."""
    if lut_path and os.path.exists(lut_path):
        return f"lut3d='{lut_path}'"

    if not preset_id:
        return ""

    from app.color_grade import GRADE_PRESETS

    if preset_id == "auto":
        # For the single-pass pipeline, auto uses a sensible default correction
        # (the full signal analysis requires a pre-scan which we avoid for speed)
        eq_params = {"contrast": 1.1, "saturation": 1.1, "gamma": 1.05}
        cb_params = {}
    else:
        preset = GRADE_PRESETS.get(preset_id, {})
        eq_params = dict(preset.get("eq", {}))
        cb_params = dict(preset.get("colorbalance", {}))

    # Apply user adjustments
    if adjustments:
        if "brightness" in adjustments:
            eq_params["brightness"] = eq_params.get("brightness", 0) + adjustments["brightness"]
        if "contrast" in adjustments:
            eq_params["contrast"] = eq_params.get("contrast", 1) * adjustments["contrast"]
        if "saturation" in adjustments:
            eq_params["saturation"] = eq_params.get("saturation", 1) * adjustments["saturation"]
        if "gamma" in adjustments:
            eq_params["gamma"] = eq_params.get("gamma", 1) * adjustments["gamma"]
        if "temperature" in adjustments:
            temp = adjustments["temperature"]
            cb_params["rs"] = cb_params.get("rs", 0) + temp * 0.1
            cb_params["bs"] = cb_params.get("bs", 0) - temp * 0.1
        if "tint" in adjustments:
            tint = adjustments["tint"]
            cb_params["gs"] = cb_params.get("gs", 0) - tint * 0.08

    parts = []
    if eq_params:
        eq_str = ":".join(f"{k}={v:.4f}" for k, v in eq_params.items())
        parts.append(f"eq={eq_str}")
    if cb_params:
        cb_str = ":".join(f"{k}={v:.4f}" for k, v in cb_params.items())
        parts.append(f"colorbalance={cb_str}")

    return ",".join(parts)


def _build_text_overlays(
    overlays: list[dict],
    total_duration: float,
    num_fixed_inputs: int,
    video_label: str,
) -> tuple[list[str], list[str]]:
    """
    Render each text overlay to a transparent PNG via Pillow, then build
    FFmpeg overlay filters to composite them at the right times.

    Args:
        overlays: list of text overlay dicts (text, start, end, position, etc.)
        total_duration: total video duration
        num_fixed_inputs: number of FFmpeg inputs before the text PNGs
                          (= num_unique_videos + 1 audio)
        video_label: the current filter label to chain from

    Returns:
        (png_paths, filter_parts) — png_paths are temp files to add as -i,
        filter_parts are the overlay filter strings to append.
    """
    from app.text_fx import save_text_png

    png_paths = []
    filter_parts = []
    current_label = video_label

    for i, ov in enumerate(overlays):
        text = ov.get("text", "").strip()
        if not text:
            continue

        size = ov.get("font_size", 72)
        color = ov.get("color", "white")
        position = ov.get("position", "center")
        start = ov.get("start", 0)
        end = ov.get("end") or total_duration

        # Render text to a temp PNG
        png_path = tempfile.mktemp(suffix=f"_txt{i}.png")
        save_text_png(
            text,
            png_path,
            font_size=size,
            color=color,
            stroke_color="black",
            stroke_width=2,
            bold=True,
        )
        png_paths.append(png_path)

        # FFmpeg input index for this PNG
        input_idx = num_fixed_inputs + len(png_paths) - 1

        # Position: center the PNG horizontally, place vertically
        if position == "top":
            x = f"(W-w)/2"
            y = "120"
        elif position == "bottom":
            x = f"(W-w)/2"
            y = f"{TIKTOK_HEIGHT - 300}"
        else:
            x = f"(W-w)/2"
            y = f"(H-h)/2"

        out_label = f"txt{i}"
        filt = (
            f"[{current_label}][{input_idx}:v]overlay={x}:{y}"
            f":enable='between(t,{start:.3f},{end:.3f})'"
            f"[{out_label}]"
        )
        filter_parts.append(filt)
        current_label = out_label

    return png_paths, filter_parts


def _check_videotoolbox(ffmpeg: str) -> bool:
    """Check if h264_videotoolbox encoder is available."""
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5,
        )
        return "h264_videotoolbox" in result.stdout
    except Exception:
        return False


def _monitor_progress(
    progress_path: str,
    total_duration: float,
    callback,
    stop_event: threading.Event,
):
    """
    Read FFmpeg's -progress file in real time and emit accurate percentages.
    FFmpeg writes key=value pairs, and out_time_us gives microseconds encoded.
    """
    total_us = total_duration * 1_000_000

    while not stop_event.is_set():
        stop_event.wait(0.4)  # Poll every 400ms

        if not os.path.exists(progress_path):
            continue

        try:
            with open(progress_path, "r") as f:
                content = f.read()
        except OSError:
            continue

        # Find the last out_time_us value
        matches = re.findall(r"out_time_us=(\d+)", content)
        if matches:
            current_us = int(matches[-1])
            pct = min(current_us / total_us, 0.99) if total_us > 0 else 0

            # Map to 5%-99% range (0-5% is setup, 100% is done)
            display_pct = 0.05 + pct * 0.94
            elapsed_s = current_us / 1_000_000
            remaining_s = (total_duration - elapsed_s) / (pct or 0.001) * (1 - pct)

            if remaining_s < 60:
                eta = f"~{int(remaining_s)}s left"
            else:
                eta = f"~{int(remaining_s / 60)}m {int(remaining_s % 60)}s left"

            callback(
                display_pct,
                f"Rendering {pct * 100:.0f}% — {elapsed_s:.1f}s / {total_duration:.1f}s ({eta})",
            )

        # Check for "progress=end"
        if "progress=end" in content:
            break
