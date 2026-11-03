"""Auto color grading — analyze video and apply corrections via FFmpeg filters."""

import os
import shutil
import subprocess
import json

# Grading presets that users can pick from.
# Each is a set of FFmpeg filter parameters applied via the eq + colorbalance filters.
GRADE_PRESETS = {
    "auto": {
        "label": "Auto Enhance",
        "description": "Analyzes your footage and auto-corrects exposure, contrast, and saturation",
    },
    "cinematic_warm": {
        "label": "Cinematic Warm",
        "description": "Warm tones, lifted blacks, slight desaturation — filmic look",
        "eq": {"brightness": 0.03, "contrast": 1.15, "saturation": 0.9, "gamma": 1.05},
        "colorbalance": {"rs": 0.08, "gs": -0.02, "bs": -0.06, "rh": 0.05, "bh": -0.03},
    },
    "cinematic_cool": {
        "label": "Cinematic Cool",
        "description": "Cool blue-teal shadows, warm highlights — blockbuster look",
        "eq": {"brightness": 0.0, "contrast": 1.2, "saturation": 0.85, "gamma": 1.0},
        "colorbalance": {"rs": -0.04, "gs": -0.02, "bs": 0.1, "rh": 0.06, "gh": 0.02, "bh": -0.04},
    },
    "vibrant": {
        "label": "Vibrant Pop",
        "description": "Boosted saturation and contrast — punchy social media look",
        "eq": {"brightness": 0.02, "contrast": 1.25, "saturation": 1.4, "gamma": 0.95},
        "colorbalance": {},
    },
    "desaturated": {
        "label": "Muted / Desaturated",
        "description": "Low saturation, soft contrast — moody editorial feel",
        "eq": {"brightness": 0.0, "contrast": 1.05, "saturation": 0.5, "gamma": 1.1},
        "colorbalance": {"rs": 0.02, "bs": 0.02},
    },
    "golden_hour": {
        "label": "Golden Hour",
        "description": "Warm golden tint, soft glow — sunset vibes",
        "eq": {"brightness": 0.04, "contrast": 1.1, "saturation": 1.15, "gamma": 1.08},
        "colorbalance": {"rs": 0.12, "gs": 0.04, "bs": -0.08, "rm": 0.05, "gm": 0.02, "bm": -0.04},
    },
    "bw_film": {
        "label": "Black & White Film",
        "description": "Classic B&W with grain texture and lifted blacks",
        "eq": {"brightness": 0.02, "contrast": 1.3, "saturation": 0.0, "gamma": 1.05},
        "colorbalance": {},
    },
}


def get_presets() -> list[dict]:
    """Return list of available grading presets for the UI."""
    return [
        {"id": k, "label": v["label"], "description": v["description"]}
        for k, v in GRADE_PRESETS.items()
    ]


def apply_grade(input_path: str, output_path: str, preset_id: str, adjustments: dict | None = None) -> str:
    """
    Apply a color grade to a video.

    Args:
        input_path: Source video path.
        output_path: Destination path.
        preset_id: One of the GRADE_PRESETS keys, or "auto".
        adjustments: Optional manual overrides:
            - brightness: float (-0.5 to 0.5, default 0)
            - contrast: float (0.5 to 2.0, default 1)
            - saturation: float (0 to 3.0, default 1)
            - gamma: float (0.5 to 2.0, default 1)
            - temperature: float (-1 to 1, negative=cool positive=warm)
            - tint: float (-1 to 1, negative=green positive=magenta)

    Returns:
        Path to the graded output.
    """
    from app.compositor import _find_ffmpeg
    ffmpeg = _find_ffmpeg()

    if preset_id == "auto":
        return _auto_grade(ffmpeg, input_path, output_path, adjustments)

    preset = GRADE_PRESETS.get(preset_id, {})
    eq_params = dict(preset.get("eq", {}))
    cb_params = dict(preset.get("colorbalance", {}))

    # Apply user adjustments on top of preset
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
            cb_params["gm"] = cb_params.get("gm", 0) - tint * 0.05

    filters = _build_filter_chain(eq_params, cb_params)
    return _run_ffmpeg(ffmpeg, input_path, output_path, filters)


def _auto_grade(ffmpeg: str, input_path: str, output_path: str, adjustments: dict | None = None) -> str:
    """
    Analyze the video's luminance/color stats and auto-correct.
    Uses ffprobe to read signalstats, then builds corrective filters.
    """
    # Sample a few frames to get average brightness/saturation stats
    probe_cmd = [
        ffmpeg.replace("ffmpeg", "ffprobe"),
        "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate",
        "-of", "json",
        input_path,
    ]

    try:
        result = subprocess.run(probe_cmd, capture_output=True, text=True)
        probe_data = json.loads(result.stdout)
    except Exception:
        probe_data = {}

    # Use ffmpeg's signalstats to analyze brightness
    stats_cmd = [
        ffmpeg,
        "-i", input_path,
        "-vf", "signalstats,metadata=print:file=-",
        "-frames:v", "30",  # sample first 30 frames
        "-f", "null", "-",
    ]

    avg_y = 128  # default mid-gray
    avg_sat = 100
    try:
        result = subprocess.run(stats_cmd, capture_output=True, text=True, timeout=30)
        output = result.stderr + result.stdout

        # Parse YAVG (luminance average) and SATAVG (saturation average)
        y_vals = []
        sat_vals = []
        for line in output.split("\n"):
            if "lavfi.signalstats.YAVG" in line:
                try:
                    val = float(line.split("=")[-1].strip())
                    y_vals.append(val)
                except (ValueError, IndexError):
                    pass
            if "lavfi.signalstats.SATAVG" in line:
                try:
                    val = float(line.split("=")[-1].strip())
                    sat_vals.append(val)
                except (ValueError, IndexError):
                    pass

        if y_vals:
            avg_y = sum(y_vals) / len(y_vals)
        if sat_vals:
            avg_sat = sum(sat_vals) / len(sat_vals)
    except (subprocess.TimeoutExpired, Exception):
        pass

    # Build corrections based on analysis
    eq_params = {}
    cb_params = {}

    # Brightness correction: target ~120-130 YAVG
    target_y = 125
    if avg_y < 80:
        # Underexposed — boost brightness and gamma
        eq_params["brightness"] = min(0.15, (target_y - avg_y) / 500)
        eq_params["gamma"] = min(1.4, 1.0 + (target_y - avg_y) / 300)
    elif avg_y > 170:
        # Overexposed — pull down
        eq_params["brightness"] = max(-0.1, (target_y - avg_y) / 500)
        eq_params["gamma"] = max(0.8, 1.0 + (target_y - avg_y) / 400)

    # Always add a slight contrast boost for punch
    eq_params["contrast"] = 1.1

    # Saturation: if very flat, boost; if oversaturated, pull back
    if avg_sat < 50:
        eq_params["saturation"] = 1.3
    elif avg_sat > 150:
        eq_params["saturation"] = 0.85
    else:
        eq_params["saturation"] = 1.1  # mild boost

    # Apply user adjustments on top
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
            cb_params["rs"] = temp * 0.1
            cb_params["bs"] = -temp * 0.1
        if "tint" in adjustments:
            tint = adjustments["tint"]
            cb_params["gs"] = -tint * 0.08

    filters = _build_filter_chain(eq_params, cb_params)
    return _run_ffmpeg(ffmpeg, input_path, output_path, filters)


def _build_filter_chain(eq_params: dict, cb_params: dict) -> str:
    """Build an FFmpeg filter string from eq and colorbalance parameters."""
    parts = []

    # eq filter
    if eq_params:
        eq_parts = []
        for k, v in eq_params.items():
            eq_parts.append(f"{k}={v:.4f}")
        parts.append("eq=" + ":".join(eq_parts))

    # colorbalance filter
    if cb_params:
        cb_parts = []
        for k, v in cb_params.items():
            cb_parts.append(f"{k}={v:.4f}")
        if cb_parts:
            parts.append("colorbalance=" + ":".join(cb_parts))

    return ",".join(parts) if parts else "null"


def _run_ffmpeg(ffmpeg: str, input_path: str, output_path: str, vf: str) -> str:
    """Run ffmpeg with a video filter chain."""
    cmd = [
        ffmpeg,
        "-y",
        "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg grading failed:\n{result.stderr[-500:]}")

    return output_path
