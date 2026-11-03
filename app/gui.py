"""BeatCut — Mac desktop app using pywebview for a native window with HTML UI."""

import json
import os
import sys
import threading
from pathlib import Path

import webview

from app.beat_detector import detect_beats
from app.timeline import build_timeline
from app.compositor import render
from app.lut import list_bundled_luts
from app.color_grade import get_presets


def _find_app_dir() -> Path:
    """Resolve the app root in both normal and py2app-bundled contexts."""
    # py2app bundles set this to Contents/Resources
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent.parent / "Resources"
    return Path(__file__).parent.parent


APP_DIR = _find_app_dir()
LUTS_DIR = APP_DIR / "luts"
OUTPUTS_DIR = APP_DIR / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)
STATIC_DIR = APP_DIR / "static"


class Api:
    """Python backend exposed to the JS frontend."""

    def __init__(self):
        self.window = None
        self.video_paths: list[str] = []
        self.audio_path: str | None = None
        self.lut_path: str | None = None
        self.rendering = False

    def pick_videos(self):
        result = self.window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=True,
            file_types=(
                "Video Files (*.mp4;*.mov;*.avi;*.mkv;*.webm)",
                "All Files (*.*)",
            ),
        )
        if result:
            for p in result:
                if p not in self.video_paths:
                    self.video_paths.append(p)
        return [os.path.basename(p) for p in self.video_paths]

    def remove_video(self, index: int):
        if 0 <= index < len(self.video_paths):
            self.video_paths.pop(index)
        return [os.path.basename(p) for p in self.video_paths]

    def pick_audio(self):
        result = self.window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=(
                "Audio Files (*.mp3;*.wav;*.flac;*.aac;*.m4a;*.ogg)",
                "All Files (*.*)",
            ),
        )
        if result and len(result) > 0:
            self.audio_path = result[0]
            return os.path.basename(self.audio_path)
        return None

    def pick_lut(self):
        result = self.window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("LUT Files (*.cube;*.3dl)", "All Files (*.*)"),
        )
        if result and len(result) > 0:
            self.lut_path = result[0]
            return os.path.basename(self.lut_path)
        return None

    def clear_lut(self):
        self.lut_path = None

    def get_bundled_luts(self):
        return list_bundled_luts(str(LUTS_DIR))

    def set_bundled_lut(self, name: str):
        luts = {l["name"]: l["path"] for l in list_bundled_luts(str(LUTS_DIR))}
        self.lut_path = luts.get(name)

    def get_grade_presets(self):
        return get_presets()

    def analyze_beats(self, start_time: float, end_time: float | None):
        """Run beat detection and return beat times for the timeline UI."""
        if not self.audio_path:
            return {"error": "No audio file selected"}
        analysis = detect_beats(self.audio_path, sensitivity=1.0)
        beats = analysis["beat_times"]
        duration = analysis["duration"]
        tempo = analysis["tempo"]
        sections = analysis["sections"]

        # Filter to the requested range
        if end_time is None or end_time <= 0:
            end_time = duration
        beats = [b for b in beats if start_time <= b <= end_time]

        return {
            "beats": beats,
            "duration": duration,
            "tempo": tempo,
            "sections": sections,
            "range_start": start_time,
            "range_end": end_time,
        }

    def start_render(self, settings: dict):
        if self.rendering:
            return {"error": "Already rendering"}
        if not self.video_paths:
            return {"error": "Add at least one video"}
        if not self.audio_path:
            return {"error": "Add a music file"}

        self.rendering = True
        thread = threading.Thread(
            target=self._render_worker, args=(settings,), daemon=True
        )
        thread.start()
        return {"status": "started"}

    def _render_worker(self, settings: dict):
        try:
            # Check if user provided a custom timeline from the editor
            custom_assignments = settings.get("timeline_assignments")  # list of video indices per beat
            range_start = settings.get("range_start", 0.0)
            range_end = settings.get("range_end")

            self._emit("progress", {"pct": 0.02, "msg": "Analyzing beats..."})
            analysis = detect_beats(self.audio_path, sensitivity=1.0)

            beats = analysis["beat_times"]
            duration = analysis["duration"]

            # Apply song range
            if range_end and range_end > 0:
                duration = min(range_end, duration)
            beats = [b for b in beats if range_start <= b <= duration]

            self._emit("progress", {"pct": 0.04, "msg": "Building timeline..."})
            timeline = build_timeline(
                beat_times=beats,
                video_paths=self.video_paths,
                duration=duration,
                sections=analysis["sections"],
                cut_mode=settings.get("cut_mode", "every_beat"),
                shuffle=settings.get("shuffle", False),
            )

            # Override video assignments if user customized the timeline
            if custom_assignments and len(custom_assignments) == len(timeline):
                for i, vid_idx in enumerate(custom_assignments):
                    if 0 <= vid_idx < len(self.video_paths):
                        timeline[i]["video_path"] = self.video_paths[vid_idx]
                        timeline[i]["video_index"] = vid_idx

            # Auto-sync lyrics to beats
            text_overlays = []
            lyrics_raw = settings.get("lyrics_text", "").strip()
            if lyrics_raw and beats:
                lines = [l for l in lyrics_raw.split("\n") if l.strip()]
                if lines:
                    position = settings.get("lyrics_position", "bottom")
                    font_size = settings.get("lyrics_size", 72)
                    color = settings.get("lyrics_color", "white")

                    # Distribute lines evenly across the beat timestamps
                    # Group beats into chunks — one chunk per lyric line
                    beats_per_line = max(1, len(beats) // len(lines))
                    for i, line in enumerate(lines):
                        start_idx = i * beats_per_line
                        end_idx = min((i + 1) * beats_per_line, len(beats) - 1)

                        if start_idx >= len(beats):
                            break

                        start_t = beats[start_idx]
                        # End at next line's start, or song end
                        if i + 1 < len(lines) and (i + 1) * beats_per_line < len(beats):
                            end_t = beats[(i + 1) * beats_per_line]
                        else:
                            end_t = duration

                        text_overlays.append({
                            "text": line.strip(),
                            "start": start_t,
                            "end": end_t,
                            "position": position,
                            "font_size": font_size,
                            "color": color,
                        })

            base = os.path.splitext(os.path.basename(self.audio_path))[0]
            output_path = str(OUTPUTS_DIR / f"beatcut_{base}.mp4")

            def progress_cb(pct, msg):
                self._emit("progress", {"pct": pct, "msg": msg})

            # Color grading settings
            grade_preset = settings.get("grade_preset") or None
            grade_adjustments = {}
            for key in ("brightness", "contrast", "saturation", "gamma", "temperature", "tint"):
                if key in settings and settings[key] is not None:
                    grade_adjustments[key] = settings[key]

            render(
                timeline=timeline,
                audio_path=self.audio_path,
                output_path=output_path,
                text_overlays=text_overlays if text_overlays else None,
                lut_path=self.lut_path,
                grade_preset=grade_preset,
                grade_adjustments=grade_adjustments if grade_adjustments else None,
                progress_callback=progress_cb,
            )

            self._emit("done", {"path": output_path})

        except Exception as e:
            self._emit("error", {"msg": str(e)})
        finally:
            self.rendering = False

    def _emit(self, event: str, data: dict):
        """Send an event to the JS frontend."""
        if self.window:
            js = f"window.onPyEvent('{event}', {json.dumps(data)})"
            self.window.evaluate_js(js)

    def reveal_in_finder(self, path: str):
        os.system(f'open -R "{path}"')


def main():
    api = Api()
    html_file = STATIC_DIR / "index.html"
    html_url = html_file.as_uri()

    window = webview.create_window(
        "BeatCut",
        url=html_url,
        js_api=api,
        width=760,
        height=920,
        min_size=(680, 700),
    )
    api.window = window
    webview.start(debug=False)


if __name__ == "__main__":
    main()
