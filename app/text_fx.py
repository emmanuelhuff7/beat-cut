"""Text FX — Helvetica Neue text overlays rendered to transparent PNGs."""

import os
from PIL import Image, ImageDraw, ImageFont

TIKTOK_WIDTH = 1080
TIKTOK_HEIGHT = 1920

# macOS ships Helvetica Neue here
HELVETICA_NEUE_PATHS = [
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Helvetica Neue.ttf",
    "/Library/Fonts/HelveticaNeue.ttf",
]


def _find_font(bold: bool = False) -> str:
    """Find Helvetica Neue on the system."""
    for p in HELVETICA_NEUE_PATHS:
        if os.path.exists(p):
            return p
    # Fallback — Pillow default
    return ""


def create_text_image(
    text: str,
    font_size: int = 72,
    color: str = "white",
    stroke_color: str = "black",
    stroke_width: int = 2,
    bold: bool = True,
    max_width: int = TIKTOK_WIDTH - 100,
) -> Image.Image:
    """
    Render text to a transparent PIL Image using Helvetica Neue.
    """
    font_path = _find_font(bold=bold)

    if font_path:
        # For .ttc files, index 0 is Regular, index 1 is often Bold
        font_index = 1 if bold and font_path.endswith(".ttc") else 0
        try:
            font = ImageFont.truetype(font_path, font_size, index=font_index)
        except (OSError, IndexError):
            font = ImageFont.truetype(font_path, font_size, index=0)
    else:
        font = ImageFont.load_default()

    # Measure text with wrapping
    dummy = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(dummy)

    # Word-wrap
    lines = _wrap_text(draw, text, font, max_width)
    line_heights = []
    line_widths = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])

    total_height = sum(line_heights) + (len(lines) - 1) * 10
    img_width = max(line_widths) + stroke_width * 2 + 20
    img_height = total_height + stroke_width * 2 + 20

    # Render
    img = Image.new("RGBA", (img_width, img_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    y_offset = 10
    for i, line in enumerate(lines):
        x = (img_width - line_widths[i]) // 2
        draw.text(
            (x, y_offset),
            line,
            font=font,
            fill=color,
            stroke_fill=stroke_color,
            stroke_width=stroke_width,
        )
        y_offset += line_heights[i] + 10

    return img


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    """Simple word-wrap for text."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


def save_text_png(
    text: str,
    output_path: str,
    font_size: int = 72,
    color: str = "white",
    stroke_color: str = "black",
    stroke_width: int = 2,
    bold: bool = True,
) -> str:
    """Render text to a transparent PNG file. Returns the output path."""
    img = create_text_image(
        text,
        font_size=font_size,
        color=color,
        stroke_color=stroke_color,
        stroke_width=stroke_width,
        bold=bold,
    )
    img.save(output_path, "PNG")
    return output_path
