"""py2app build config — creates BeatCut.app"""

from setuptools import setup

APP = ["run.py"]
DATA_FILES = [
    ("static", ["static/index.html"]),
]
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "BeatCut.icns",
    "plist": {
        "CFBundleName": "BeatCut",
        "CFBundleDisplayName": "BeatCut",
        "CFBundleIdentifier": "com.beatcut.app",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0",
        "NSHighResolutionCapable": True,
    },
    "packages": [
        "app",
        "webview",
        "librosa",
        "PIL",
        "numpy",
        "soundfile",
        "scipy",
        "sklearn",
        "joblib",
        "lazy_loader",
        "audioread",
        "decorator",
        "pooch",
        "numba",
        "llvmlite",
    ],
    "includes": [
        "app.beat_detector",
        "app.timeline",
        "app.compositor",
        "app.lut",
        "app.text_fx",
        "app.color_grade",
        "app.gui",
    ],
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
