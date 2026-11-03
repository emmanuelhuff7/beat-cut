"""Timeline builder — maps beat timestamps to video clip segments."""

import random


# TikTok vertical format
TIKTOK_WIDTH = 1080
TIKTOK_HEIGHT = 1920
TIKTOK_FPS = 30


def build_timeline(
    beat_times: list[float],
    video_paths: list[str],
    duration: float,
    sections: list[dict] | None = None,
    cut_mode: str = "every_beat",
    shuffle: bool = False,
) -> list[dict]:
    """
    Create a timeline of video segments synced to beat timestamps.

    Args:
        beat_times: List of beat timestamps in seconds from beat_detector.
        video_paths: List of source video file paths.
        duration: Total audio duration.
        sections: Optional section info from beat_detector (for adaptive cutting).
        cut_mode: How to cut videos:
            - "every_beat": Switch video on every beat
            - "every_2": Switch every 2 beats
            - "every_4": Switch every 4 beats (bar-level)
            - "adaptive": Cut faster on high-energy sections, slower on low
        shuffle: If True, randomize video order instead of round-robin.

    Returns:
        List of segment dicts: {video_path, start_time, end_time, video_offset}
    """
    if not beat_times or not video_paths:
        return []

    # Determine cut points based on mode
    if cut_mode == "adaptive" and sections:
        cut_points = _adaptive_cuts(beat_times, sections)
    elif cut_mode == "every_2":
        cut_points = beat_times[::2]
    elif cut_mode == "every_4":
        cut_points = beat_times[::4]
    else:  # every_beat
        cut_points = list(beat_times)

    # Ensure we start at 0 and end at duration
    if not cut_points or cut_points[0] > 0.05:
        cut_points.insert(0, 0.0)
    if cut_points[-1] < duration - 0.1:
        cut_points.append(duration)

    # Build video rotation order
    n_videos = len(video_paths)
    if shuffle:
        order = list(range(n_videos))
        random.shuffle(order)
    else:
        order = list(range(n_videos))

    # Track how much of each video we've used (for offsetting into the source)
    video_offsets = [0.0] * n_videos

    segments = []
    for i in range(len(cut_points) - 1):
        start = cut_points[i]
        end = cut_points[i + 1]
        seg_duration = end - start

        # Round-robin through videos
        vid_idx = order[i % len(order)]
        video_path = video_paths[vid_idx]

        segments.append({
            "video_path": video_path,
            "video_index": vid_idx,
            "start_time": start,
            "end_time": end,
            "duration": seg_duration,
            "video_offset": video_offsets[vid_idx],
        })

        video_offsets[vid_idx] += seg_duration

    return segments


def _adaptive_cuts(beat_times: list[float], sections: list[dict]) -> list[float]:
    """
    High-energy sections cut every beat; mid every 2; low every 4.
    """
    cut_points = []
    for section in sections:
        label = section.get("label", "mid")
        s_start, s_end = section["start"], section["end"]

        # Get beats within this section
        section_beats = [b for b in beat_times if s_start <= b < s_end]

        if label == "high":
            step = 1  # every beat
        elif label == "mid":
            step = 2  # every 2 beats
        else:
            step = 4  # every 4 beats

        cut_points.extend(section_beats[::step])

    # Deduplicate and sort
    cut_points = sorted(set(cut_points))
    return cut_points
