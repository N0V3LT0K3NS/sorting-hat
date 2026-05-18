"""Offline rendering — stage 3 of the pipeline: ``render(typed_result) -> png``.

Stage 1 ``classify`` picks a template; stage 2 ``fill`` returns one of the
four typed result models from :mod:`agent.state`; this stage composites that
result's slot text onto the matching base meme image and produces the final
portrait PNG.

The renderer is importable and standalone — no LiveKit, no network, no LLM.
It takes any of ``IcebergResult`` / ``TwoButtonsResult`` / ``CompassResult``
/ ``ArcResult`` and dispatches to a template-specific layout.

The four base images in ``assets/templates/`` are **placeholder bases**
(see ``assets/templates/_generate_bases.py``). Real meme art can replace
them later — same filenames, same 1080x1350 portrait dimensions — without
changing this module: the layout regions below are expressed as fractions
of the canvas, so they track the base image whatever its pixels are.

Text is wrapped to fit its layout region; the per-field char limits in
``agent.state`` keep slot values short, but wrapping is still done here so
that long values degrade gracefully (shrink-to-fit) rather than overflow.
A scalable default font is used — no system font path is assumed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from PIL import Image, ImageDraw, ImageFont

from agent.state import ArcResult, CompassResult, IcebergResult, TwoButtonsResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "assets" / "templates"

# Canvas dimensions — must match the generated base images.
WIDTH = 1080
HEIGHT = 1350

# Base image filename per typed-result model.
_BASE_FILES = {
    "IcebergResult": "iceberg.png",
    "TwoButtonsResult": "two_buttons.png",
    "CompassResult": "compass.png",
    "ArcResult": "arc.png",
}

TypedResult = Union[IcebergResult, TwoButtonsResult, CompassResult, ArcResult]


class RenderError(Exception):
    """Raised when a result cannot be rendered (bad type or missing base)."""


# ---------------------------------------------------------------------------
# Font + text helpers
# ---------------------------------------------------------------------------


def _font(size: int) -> ImageFont.FreeTypeFont:
    """Return a scalable default font at ``size`` px — no system path needed."""
    return ImageFont.load_default(size=size)


# Punctuation the LLM commonly produces that the bitmap-derived default font
# has no glyph for — rendering them as tofu boxes. Mapped to ASCII-safe
# equivalents so the portrait is always legible without bundling a font.
_GLYPH_FALLBACKS = {
    "—": " - ",   # em dash
    "–": "-",     # en dash
    "‘": "'",     # left single quote
    "’": "'",     # right single quote / apostrophe
    "“": '"',     # left double quote
    "”": '"',     # right double quote
    "…": "...",   # ellipsis
    " ": " ",     # non-breaking space
}


def _sanitize(text: str) -> str:
    """Replace non-ASCII punctuation the default font cannot render.

    The default Pillow font lacks glyphs for em dashes, curly quotes, and
    ellipses, which the interviewer/slot-filling LLMs produce freely. Left
    unmapped these draw as missing-glyph boxes. Mapping to ASCII keeps every
    portrait legible without sourcing, licensing, and bundling a TTF.
    """
    for bad, good in _GLYPH_FALLBACKS.items():
        text = text.replace(bad, good)
    return text


def _wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """Greedy word-wrap ``text`` so each line fits within ``max_width`` px.

    A single word longer than ``max_width`` is hard-broken character-by-
    character so it can never overflow the region.
    """
    words = _sanitize(text or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            if draw.textlength(word, font=font) > max_width and not current:
                # A lone over-long word: hard-break it.
                piece = ""
                for ch in word:
                    if draw.textlength(piece + ch, font=font) <= max_width or not piece:
                        piece += ch
                    else:
                        lines.append(piece)
                        piece = ch
                current = piece
            else:
                current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int],
    *,
    max_size: int,
    min_size: int = 14,
    line_spacing: float = 1.15,
) -> tuple[list[str], ImageFont.FreeTypeFont, int]:
    """Find the largest font size at which ``text`` wraps to fit ``box``.

    ``box`` is ``(max_width, max_height)`` in px. Returns the wrapped lines,
    the chosen font, and the per-line pixel height. Never raises on long
    text: it shrinks to ``min_size`` and accepts a slight overflow rather
    than failing.
    """
    max_width, max_height = box
    size = max_size
    while size >= min_size:
        font = _font(size)
        lines = _wrap(draw, text, font, max_width)
        line_h = int(size * line_spacing)
        if line_h * len(lines) <= max_height:
            return lines, font, line_h
        size -= 2
    font = _font(min_size)
    lines = _wrap(draw, text, font, max_width)
    return lines, font, int(min_size * line_spacing)


def _draw_block(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    line_h: int,
    origin: tuple[int, int],
    fill,
    *,
    centre_width: Optional[int] = None,
    shadow: Optional[tuple] = None,
) -> int:
    """Draw wrapped ``lines`` from ``origin``; return the y after the block.

    If ``centre_width`` is given, each line is centred within that width
    starting at ``origin[0]``. If ``shadow`` is given, a 2px offset shadow
    is drawn in that colour first (used for white-on-image iceberg text).
    """
    x0, y = origin
    for line in lines:
        x = x0
        if centre_width is not None:
            w = draw.textlength(line, font=font)
            x = x0 + (centre_width - w) / 2
        if shadow is not None:
            draw.text((x + 2, y + 2), line, font=font, fill=shadow)
        draw.text((x, y), line, font=font, fill=fill)
        y += line_h
    return y


# ---------------------------------------------------------------------------
# Base-image loading
# ---------------------------------------------------------------------------


def _load_base(result: TypedResult) -> Image.Image:
    """Load and return a fresh RGB copy of the base image for ``result``."""
    name = type(result).__name__
    filename = _BASE_FILES.get(name)
    if filename is None:
        raise RenderError(
            f"no base image mapping for result type {name!r}; "
            f"expected one of {sorted(_BASE_FILES)}"
        )
    path = TEMPLATES_DIR / filename
    if not path.exists():
        raise RenderError(
            f"base image {path} is missing — run "
            f"assets/templates/_generate_bases.py to generate it"
        )
    return Image.open(path).convert("RGB")


# ---------------------------------------------------------------------------
# Per-template layouts
# ---------------------------------------------------------------------------

_WHITE = (255, 255, 255)
_SHADOW = (0, 0, 0)
_INK = (24, 24, 26)


def _render_iceberg(result: IcebergResult) -> Image.Image:
    """Four slot texts at four descending depths; white text with shadow."""
    img = _load_base(result)
    draw = ImageDraw.Draw(img)

    waterline = int(HEIGHT * 0.18)
    band_h = (HEIGHT - waterline) // 4
    side_pad = 90
    box_w = WIDTH - 2 * side_pad

    layers = [
        result.surface,
        result.first_layer,
        result.second_layer,
        result.abyss,
    ]
    # Surface text largest, shrinking with depth.
    max_sizes = [46, 42, 38, 34]
    for i, (text, max_size) in enumerate(zip(layers, max_sizes)):
        band_top = waterline + i * band_h
        lines, font, line_h = _fit_text(
            draw, text, (box_w, band_h - 40), max_size=max_size
        )
        block_h = line_h * len(lines)
        y = band_top + (band_h - block_h) // 2
        _draw_block(
            draw, lines, font, line_h, (side_pad, y), _WHITE,
            centre_width=box_w, shadow=_SHADOW,
        )
    return img


def _render_two_buttons(result: TwoButtonsResult) -> Image.Image:
    """Labels on the buttons, seductions beside them, impossibility as caption."""
    img = _load_base(result)
    draw = ImageDraw.Draw(img)

    btn_w, btn_h = 420, 150
    gap = 60
    btn_y = int(HEIGHT * 0.12)
    left_x = (WIDTH - 2 * btn_w - gap) // 2

    labels = [result.button_a_label, result.button_b_label]
    seductions = [result.button_a_seduction, result.button_b_seduction]
    for i, (label, seduction) in enumerate(zip(labels, seductions)):
        x0 = left_x + i * (btn_w + gap)
        # Label centred on the button face.
        lines, font, line_h = _fit_text(
            draw, label, (btn_w - 40, btn_h - 24), max_size=34
        )
        block_h = line_h * len(lines)
        y = btn_y + (btn_h - block_h) // 2
        _draw_block(
            draw, lines, font, line_h, (x0 + 20, y), _WHITE,
            centre_width=btn_w - 40, shadow=_SHADOW,
        )
        # Seduction beneath the button, within that button's column.
        s_lines, s_font, s_lh = _fit_text(
            draw, seduction, (btn_w, 150), max_size=26
        )
        _draw_block(
            draw, s_lines, s_font, s_lh, (x0, btn_y + btn_h + 24), _INK,
            centre_width=btn_w,
        )

    # Impossibility caption on the dark bottom strip.
    strip_top = int(HEIGHT * 0.46)
    cap_pad = 70
    lines, font, line_h = _fit_text(
        draw,
        result.impossibility,
        (WIDTH - 2 * cap_pad, HEIGHT - strip_top - 100),
        max_size=44,
    )
    block_h = line_h * len(lines)
    y = strip_top + (HEIGHT - strip_top - block_h) // 2
    _draw_block(
        draw, lines, font, line_h, (cap_pad, y), _WHITE,
        centre_width=WIDTH - 2 * cap_pad,
    )
    return img


def _render_compass(result: CompassResult) -> Image.Image:
    """Axis poles labelled, a dot plotted at the position, caption beneath."""
    img = _load_base(result)
    draw = ImageDraw.Draw(img)

    margin = 110
    plot_top = margin
    plot_size = WIDTH - 2 * margin
    plot_bottom = plot_top + plot_size
    left, right = margin, WIDTH - margin
    cx = (left + right) // 2
    cy = (plot_top + plot_bottom) // 2

    pole_font = _font(28)

    def _pole(text: str, anchor: tuple[int, int], align: str) -> None:
        lines = _wrap(draw, text, pole_font, 360)
        line_h = int(28 * 1.15)
        x0, y = anchor
        for k, line in enumerate(lines):
            w = draw.textlength(line, font=pole_font)
            if align == "center":
                x = x0 - w / 2
            elif align == "right":
                x = x0 - w
            else:
                x = x0
            draw.text((x, y + k * line_h), line, font=pole_font, fill=_INK)

    # axis_1 is horizontal (negative -> left, positive -> right);
    # axis_2 is vertical (negative -> bottom, positive -> top).
    a1_neg, a1_pos = result.axis_1_poles
    a2_neg, a2_pos = result.axis_2_poles
    _pole(a2_pos, (cx, plot_top - 44), "center")
    _pole(a2_neg, (cx, plot_bottom + 12), "center")
    _pole(a1_neg, (left - 16, cy - 14), "right")
    _pole(a1_pos, (right + 16, cy - 14), "left")

    # Plot the dot. position in -1..1; +1 axis_2 maps to plot_top.
    px = left + (result.axis_1_position + 1.0) / 2.0 * plot_size
    py = plot_bottom - (result.axis_2_position + 1.0) / 2.0 * plot_size
    r = 22
    draw.ellipse(
        [px - r, py - r, px + r, py + r],
        fill=(232, 88, 84),
        outline=(20, 20, 22),
        width=4,
    )

    # why_these_axes caption beneath the plot.
    cap_pad = 80
    lines, font, line_h = _fit_text(
        draw,
        result.why_these_axes,
        (WIDTH - 2 * cap_pad, HEIGHT - plot_bottom - 80),
        max_size=34,
    )
    _draw_block(
        draw, lines, font, line_h, (cap_pad, plot_bottom + 60), _INK,
        centre_width=WIDTH - 2 * cap_pad,
    )
    return img


def _render_arc(result: ArcResult) -> Image.Image:
    """Four panel captions, read left-to-right then top-to-bottom."""
    img = _load_base(result)
    draw = ImageDraw.Draw(img)

    pad = 24
    cols, rows = 2, 2
    panel_w = (WIDTH - pad * (cols + 1)) // cols
    panel_h = (HEIGHT - pad * (rows + 1)) // rows
    cap_h = int(panel_h * 0.32)

    panels = [result.before, result.catalyst, result.middle, result.after]
    for r in range(rows):
        for c in range(cols):
            idx = r * cols + c
            x0 = pad + c * (panel_w + pad)
            y0 = pad + r * (panel_h + pad)
            cap_top = y0 + panel_h - cap_h
            inner_pad = 20
            lines, font, line_h = _fit_text(
                draw,
                panels[idx],
                (panel_w - 2 * inner_pad, cap_h - 2 * inner_pad),
                max_size=28,
            )
            block_h = line_h * len(lines)
            y = cap_top + (cap_h - block_h) // 2
            _draw_block(
                draw, lines, font, line_h, (x0 + inner_pad, y), _INK,
                centre_width=panel_w - 2 * inner_pad,
            )
    return img


_RENDERERS = {
    "IcebergResult": _render_iceberg,
    "TwoButtonsResult": _render_two_buttons,
    "CompassResult": _render_compass,
    "ArcResult": _render_arc,
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def render(
    typed_result: TypedResult,
    out_path: Optional[Union[str, Path]] = None,
) -> Union[Image.Image, Path]:
    """Composite ``typed_result``'s slot text onto its base meme image.

    ``typed_result`` is any of the four typed result models from
    :mod:`agent.state`. The matching base image and template-specific
    layout are selected automatically.

    Returns a :class:`PIL.Image.Image` when ``out_path`` is ``None``;
    otherwise writes a PNG to ``out_path`` and returns that path.

    Raises :class:`RenderError` for an unrecognised result type or a
    missing base image.
    """
    name = type(typed_result).__name__
    renderer = _RENDERERS.get(name)
    if renderer is None:
        raise RenderError(
            f"cannot render result of type {name!r}; expected one of "
            f"{sorted(_RENDERERS)}"
        )

    img = renderer(typed_result)

    if out_path is None:
        return img
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path
