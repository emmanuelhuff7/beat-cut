<div align="center">

# ✂️ BeatCut

**Drop in a song and some clips — get a beat-synced, color-graded vertical video.**

![Python](https://img.shields.io/badge/python-3.13-3776ab?style=flat-square&logo=python&logoColor=white)
![macOS](https://img.shields.io/badge/macOS-desktop%20app-000000?style=flat-square&logo=apple&logoColor=white)
![FFmpeg](https://img.shields.io/badge/FFmpeg-VideoToolbox-007808?style=flat-square&logo=ffmpeg&logoColor=white)

</div>

---

## ✨ Features

- 🥁 **Beat detection** — [librosa](https://librosa.org) finds beats and energy peaks, with an adjustable sensitivity
- 🎞️ **Auto timeline** — maps beats to clip segments, shaped for TikTok (1080×1920 @ 30 fps)
- 🎨 **Color grading** — one-click presets plus `.cube` / `.3dl` LUT support via FFmpeg `lut3d`
- 🔤 **Text overlays** — Helvetica Neue titles rendered to transparent PNGs
- ⚡ **Single-pass render** — the whole edit compiles to one FFmpeg `filter_complex` and encodes with macOS **VideoToolbox** hardware acceleration, instead of decoding frames in Python
- 🖥️ **Native window** — HTML UI inside a [pywebview](https://pywebview.flowrl.com) desktop app

## 🚀 Quick start

```bash
brew install ffmpeg
pip install -r requirements.txt
python run.py
```

## 🗂️ Project layout

| Module | Role |
|--------|------|
| `app/beat_detector.py` | Beat & onset timestamps from audio |
| `app/timeline.py` | Beats → clip segments |
| `app/color_grade.py` | Auto-grade analysis and presets |
| `app/lut.py` | LUT application |
| `app/text_fx.py` | Text overlay rendering |
| `app/compositor.py` | One-pass FFmpeg render |
| `app/gui.py` | pywebview app + Python ↔ UI bridge |
| `static/index.html` | The UI |

---

<div align="center"><sub>Made by <a href="https://github.com/emmanuelhuff7">@emmanuelhuff7</a></sub></div>
