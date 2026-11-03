# BeatCut

A Mac desktop app that cuts video clips to the beat of a song and outputs a vertical (1080x1920, 30 fps) video for TikTok-style posts.

Beats come from librosa. The timeline builder assigns clip segments to beats, and the compositor turns the whole edit (cuts, scaling, text, color grade) into a single FFmpeg filter graph, encoded with VideoToolbox, rather than processing frames in Python. Color grading has a few presets plus `.cube` / `.3dl` LUT support. The UI is HTML in a pywebview window.

## Run

```
brew install ffmpeg
pip install -r requirements.txt
python run.py
```

## Layout

- `app/beat_detector.py` beat and onset detection
- `app/timeline.py` beats to clip segments
- `app/color_grade.py`, `app/lut.py` grading
- `app/text_fx.py` text overlays
- `app/compositor.py` FFmpeg render
- `app/gui.py` window and Python/UI bridge
- `static/index.html` UI
