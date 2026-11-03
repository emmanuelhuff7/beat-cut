"""Beat detection using librosa — extracts beat timestamps and energy peaks from audio."""

import librosa
import numpy as np


def detect_beats(audio_path: str, sensitivity: float = 1.0) -> dict:
    """
    Analyze an audio file and return beat/onset timing information.

    Args:
        audio_path: Path to the audio file (wav, mp3, etc.)
        sensitivity: Multiplier for onset detection threshold.
                     Lower = more beats detected, higher = fewer.

    Returns:
        dict with keys:
            - beat_times: list of beat timestamps in seconds
            - onset_times: list of onset timestamps (transients/hits)
            - duration: total audio duration in seconds
            - tempo: estimated BPM
            - energy_curve: list of (time, energy) tuples for the full track
            - sections: list of dicts with start/end/type (verse/chorus heuristic)
    """
    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    duration = librosa.get_duration(y=y, sr=sr)

    # Beat tracking
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()

    # Onset detection (more granular than beats — picks up transients)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onset_frames = librosa.onset.onset_detect(
        y=y, sr=sr, onset_envelope=onset_env, delta=0.1 * sensitivity
    )
    onset_times = librosa.frames_to_time(onset_frames, sr=sr).tolist()

    # Energy curve (RMS) — useful for UI waveform display
    rms = librosa.feature.rms(y=y)[0]
    rms_times = librosa.frames_to_time(np.arange(len(rms)), sr=sr)
    energy_curve = list(zip(rms_times.tolist(), (rms / rms.max()).tolist()))

    # Section detection heuristic using spectral contrast changes
    sections = _detect_sections(y, sr, beat_times, duration)

    return {
        "beat_times": beat_times,
        "onset_times": onset_times,
        "duration": duration,
        "tempo": float(np.atleast_1d(tempo)[0]),
        "energy_curve": energy_curve,
        "sections": sections,
    }


def _detect_sections(y, sr, beat_times: list, duration: float) -> list:
    """
    Rough section detection by analyzing spectral changes over time.
    Labels sections as 'low', 'mid', or 'high' energy.
    """
    hop_length = 512
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)
    # Self-similarity via recurrence matrix
    rec = librosa.segment.recurrence_matrix(chroma, mode="affinity", sym=True)
    # Spectral clustering to find ~6 segments
    try:
        bound_frames = librosa.segment.agglomerative(rec, k=min(6, len(beat_times) // 4 or 2))
    except Exception:
        # Fallback: split evenly into 4 sections
        n = 4
        bound_frames = np.linspace(0, librosa.time_to_frames(duration, sr=sr), n + 1, dtype=int)

    bound_times = librosa.frames_to_time(bound_frames, sr=sr, hop_length=hop_length).tolist()

    # Compute mean energy per section
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    rms_times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop_length)

    sections = []
    for i in range(len(bound_times) - 1):
        start, end = bound_times[i], bound_times[i + 1]
        mask = (rms_times >= start) & (rms_times < end)
        mean_energy = float(rms[mask].mean()) if mask.any() else 0.0
        sections.append({"start": start, "end": end, "energy": mean_energy})

    # Normalize energy and label
    if sections:
        max_e = max(s["energy"] for s in sections) or 1.0
        for s in sections:
            norm = s["energy"] / max_e
            s["label"] = "high" if norm > 0.66 else ("mid" if norm > 0.33 else "low")

    return sections
