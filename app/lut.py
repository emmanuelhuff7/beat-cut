"""LUT (Look-Up Table) color grading via FFmpeg."""

import os
import subprocess
import shutil


def apply_lut(input_path: str, lut_path: str, output_path: str) -> str:
    """
    Apply a .cube or .3dl LUT to a video file using FFmpeg's lut3d filter.

    Args:
        input_path: Path to the input video.
        lut_path: Path to the .cube/.3dl LUT file.
        output_path: Where to write the graded video.

    Returns:
        Path to the output file.

    Raises:
        FileNotFoundError: If ffmpeg is not installed or files don't exist.
        RuntimeError: If ffmpeg fails.
    """
    from app.compositor import _find_ffmpeg
    ffmpeg = _find_ffmpeg()

    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input video not found: {input_path}")
    if not os.path.exists(lut_path):
        raise FileNotFoundError(f"LUT file not found: {lut_path}")

    cmd = [
        ffmpeg,
        "-y",
        "-i", input_path,
        "-vf", f"lut3d='{lut_path}'",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg LUT failed:\n{result.stderr}")

    return output_path


def list_bundled_luts(luts_dir: str) -> list[dict]:
    """
    List available LUT files in the luts directory.

    Returns:
        List of dicts with name and path for each LUT file.
    """
    luts = []
    if not os.path.isdir(luts_dir):
        return luts

    for f in sorted(os.listdir(luts_dir)):
        if f.lower().endswith((".cube", ".3dl")):
            luts.append({
                "name": os.path.splitext(f)[0],
                "path": os.path.join(luts_dir, f),
            })

    return luts
