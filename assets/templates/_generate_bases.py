"""Generate the four base meme-template PNGs the renderer composites onto.

These are **placeholder bases** — clean, neutral, correctly-proportioned
layouts that evoke each meme's structure. They are deliberately not art.
Real meme template art can be dropped in over them later (same filenames,
same dimensions) without touching ``pipeline/render.py``: the renderer's
per-template layout geometry is what binds text to image, not the pixels.

Run this script to regenerate the bases reproducibly::

    uv run python assets/templates/_generate_bases.py

The geometry constants here are the single source of truth — ``render.py``
imports its layout regions from :mod:`pipeline.render`, which is kept in
sync with the proportions drawn below.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# All four bases share one portrait canvas — a meme *portrait*, not a joke.
WIDTH = 1080
HEIGHT = 1350

TEMPLATES_DIR = Path(__file__).resolve().parent


def _font(size: int) -> ImageFont.FreeTypeFont:
    """A scalable default font — no system font path is assumed."""
    return ImageFont.load_default(size=size)


def _centred(draw: ImageDraw.ImageDraw, xy, text, font, fill) -> None:
    """Draw ``text`` centred horizontally on the point ``xy``."""
    x, y = xy
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text((x - w / 2, y), text, font=font, fill=fill)


# ---------------------------------------------------------------------------
# Iceberg — an iceberg shape over a waterline, four descending depth bands.
# ---------------------------------------------------------------------------


def make_iceberg() -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), (10, 22, 40))
    draw = ImageDraw.Draw(img)

    waterline = int(HEIGHT * 0.18)
    # Sky band above the waterline.
    draw.rectangle([0, 0, WIDTH, waterline], fill=(150, 196, 224))

    # Four ocean depth bands, darkening as they descend.
    band_top = waterline
    band_h = (HEIGHT - waterline) // 4
    shades = [(36, 88, 132), (24, 66, 104), (16, 46, 78), (8, 24, 46)]
    for i, shade in enumerate(shades):
        top = band_top + i * band_h
        bottom = HEIGHT if i == 3 else top + band_h
        draw.rectangle([0, top, WIDTH, bottom], fill=shade)

    # The iceberg: a bright wedge straddling the waterline.
    cx = WIDTH // 2
    tip_y = int(HEIGHT * 0.04)
    draw.polygon(
        [(cx, tip_y), (cx - 210, waterline), (cx + 210, waterline)],
        fill=(236, 244, 250),
    )
    draw.polygon(
        [
            (cx - 210, waterline),
            (cx + 210, waterline),
            (cx + 360, HEIGHT - 60),
            (cx - 360, HEIGHT - 60),
        ],
        fill=(208, 226, 238),
    )
    # Faint waterline rule.
    draw.line([0, waterline, WIDTH, waterline], fill=(245, 250, 252), width=4)
    return img


# ---------------------------------------------------------------------------
# Two Buttons — two buttons, a figure area, a caption strip.
# ---------------------------------------------------------------------------


def make_two_buttons() -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), (240, 240, 238))
    draw = ImageDraw.Draw(img)

    # Top half: the two sweating-buttons panel.
    panel_bottom = int(HEIGHT * 0.46)
    draw.rectangle([0, 0, WIDTH, panel_bottom], fill=(252, 252, 250))

    btn_w, btn_h = 420, 150
    gap = 60
    btn_y = int(HEIGHT * 0.12)
    left_x = (WIDTH - 2 * btn_w - gap) // 2
    for i in range(2):
        x0 = left_x + i * (btn_w + gap)
        draw.rounded_rectangle(
            [x0, btn_y, x0 + btn_w, btn_y + btn_h],
            radius=22,
            fill=(232, 88, 84),
            outline=(40, 40, 40),
            width=5,
        )

    # Figure area — a neutral panel where the deciding figure would sit.
    fig_top = btn_y + btn_h + 60
    draw.rectangle([0, fig_top, WIDTH, panel_bottom], fill=(224, 224, 220))
    _centred(
        draw,
        (WIDTH // 2, fig_top + (panel_bottom - fig_top) // 2 - 24),
        "( deciding )",
        _font(40),
        (150, 150, 146),
    )

    # Caption strip across the bottom half.
    draw.rectangle([0, panel_bottom, WIDTH, HEIGHT], fill=(20, 20, 22))
    return img


# ---------------------------------------------------------------------------
# Compass — a 2x2 grid with axis lines and labelled pole positions.
# ---------------------------------------------------------------------------


def make_compass() -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), (248, 248, 246))
    draw = ImageDraw.Draw(img)

    # A square plot, centred, with a caption strip beneath.
    margin = 110
    plot_top = margin
    plot_size = WIDTH - 2 * margin
    plot_bottom = plot_top + plot_size
    left, right = margin, WIDTH - margin

    # Four quadrants in faint distinct tints.
    cx = (left + right) // 2
    cy = (plot_top + plot_bottom) // 2
    tints = [
        ((left, plot_top, cx, cy), (224, 232, 240)),
        ((cx, plot_top, right, cy), (232, 240, 228)),
        ((left, cy, cx, plot_bottom), (240, 232, 228)),
        ((cx, cy, right, plot_bottom), (236, 230, 240)),
    ]
    for box, tint in tints:
        draw.rectangle(list(box), fill=tint)

    # Outer frame + axis cross-hairs.
    draw.rectangle([left, plot_top, right, plot_bottom], outline=(40, 40, 40), width=4)
    draw.line([left, cy, right, cy], fill=(70, 70, 70), width=3)
    draw.line([cx, plot_top, cx, plot_bottom], fill=(70, 70, 70), width=3)
    return img


# ---------------------------------------------------------------------------
# Arc — four panels left-to-right (the Anakin/Padmé four-panel arc).
# ---------------------------------------------------------------------------


def make_arc() -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), (16, 16, 20))
    draw = ImageDraw.Draw(img)

    # 2x2 grid of panels — read left-to-right, top row then bottom row.
    pad = 24
    cols, rows = 2, 2
    panel_w = (WIDTH - pad * (cols + 1)) // cols
    panel_h = (HEIGHT - pad * (rows + 1)) // rows
    tints = [(58, 70, 92), (74, 84, 70), (96, 78, 64), (54, 50, 70)]
    for r in range(rows):
        for c in range(cols):
            idx = r * cols + c
            x0 = pad + c * (panel_w + pad)
            y0 = pad + r * (panel_h + pad)
            # Image area (top ~70%) and a caption band (bottom ~30%).
            cap_h = int(panel_h * 0.32)
            draw.rectangle(
                [x0, y0, x0 + panel_w, y0 + panel_h - cap_h], fill=tints[idx]
            )
            draw.rectangle(
                [x0, y0 + panel_h - cap_h, x0 + panel_w, y0 + panel_h],
                fill=(236, 236, 232),
            )
    return img


BUILDERS = {
    "iceberg.png": make_iceberg,
    "two_buttons.png": make_two_buttons,
    "compass.png": make_compass,
    "arc.png": make_arc,
}


def generate_all(out_dir: Path = TEMPLATES_DIR) -> None:
    """Write all four base PNGs into ``out_dir``."""
    for name, builder in BUILDERS.items():
        path = out_dir / name
        builder().save(path, "PNG")
        print(f"wrote {path}")


if __name__ == "__main__":
    generate_all()
