"""Offline rendering — stage 3 of the pipeline: ``render(typed_result) -> png``.

Stage 1 ``classify`` picks a template; stage 2 ``fill`` returns one of the
four typed result models from :mod:`agent.state`; this stage turns that
result into the final meme portrait PNG.

The renderer is importable and standalone — no LiveKit. It takes any of
``IcebergResult`` / ``TwoButtonsResult`` / ``CompassResult`` / ``ArcResult``
and dispatches to a template-specific fill.

**Render approach.** A real meme-template image (``assets/templates/reference/``)
plus a per-template text prompt is sent to OpenAI's ``gpt-image-2`` image
model via the ``images/edits`` endpoint. The model fills the legible meme
text into the template art and returns a genuine filled meme — far better
than compositing text onto a placeholder base.

**Graceful degradation.** Matching the pipeline style (``classify.py`` /
``fill.py``): importing this module never fails and never touches the
network. If ``OPENAI_API_KEY`` is missing or the API call fails,
:func:`render` falls back to a minimal Pillow text render and logs a
warning — render must never hard-crash the offline pipeline. A typed
:class:`RenderError` is raised only for truly unrecoverable cases (an
unknown result type).
"""

from __future__ import annotations

import base64
import io
import logging
import os
from pathlib import Path
from typing import Optional, Union

from PIL import Image, ImageDraw, ImageFont

from agent.state import ArcResult, CompassResult, IcebergResult, TwoButtonsResult

logger = logging.getLogger("sorting-hat.render")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Real meme-template images — the inputs to gpt-image-2's images/edits call.
REFERENCE_DIR = (
    Path(__file__).resolve().parent.parent / "assets" / "templates" / "reference"
)

#: Environment variable holding the OpenAI API key (powers gpt-image-2).
API_KEY_ENV = "OPENAI_API_KEY"

#: Default render image model. Overridable via ``RENDER_MODEL`` env var.
DEFAULT_RENDER_MODEL = "gpt-image-2"

#: Portrait output size requested from gpt-image-2 (a size it accepts).
RENDER_SIZE = "1024x1536"

#: Pillow-fallback canvas dimensions (portrait), used only when the API path
#: is unavailable. ``test_render`` asserts against these.
WIDTH = 1024
HEIGHT = 1536

#: Reference meme-template filename per typed-result model. The compass has
#: no photographic reference — its base is drawn with Pillow at call time.
_REFERENCE_FILES = {
    "IcebergResult": "iceberg.jpg",
    "TwoButtonsResult": "two_buttons.jpg",
    "ArcResult": "arc.jpg",
}

TypedResult = Union[IcebergResult, TwoButtonsResult, CompassResult, ArcResult]


class RenderError(Exception):
    """Raised when a result cannot be rendered at all (unknown result type)."""


# ---------------------------------------------------------------------------
# Per-template fill prompts
# ---------------------------------------------------------------------------
#
# Each prompt tells gpt-image-2 exactly what text to place where, while
# preserving the original meme art and layout — only adding text. The prompt
# is built from the typed result's fields so the slot text is verbatim.

_PROMPT_PREAMBLE = (
    "Edit this meme template image. Keep the original artwork, composition, "
    "colors and layout exactly as they are — do not redraw the scene. Only "
    "ADD legible text into the template. Use a bold, clean meme font (Impact "
    "style where appropriate). Every piece of text must be sharp, correctly "
    "spelled, and easy to read.\n\n"
)


def _build_iceberg_prompt(r: IcebergResult) -> str:
    """Prompt: four depth layers on the iceberg, white legible text."""
    return _PROMPT_PREAMBLE + (
        "This is the iceberg meme. Place white text with a subtle dark "
        "outline at four depths:\n"
        f'- On the visible iceberg tip ABOVE the waterline: "{r.surface}"\n'
        f'- Just BELOW the waterline (first underwater layer): "{r.first_layer}"\n'
        f'- DEEPER underwater (mid layer): "{r.second_layer}"\n'
        f'- At the very BOTTOM, the darkest depths (the abyss): "{r.abyss}"\n'
        "Text descends with depth; keep each layer clearly separated."
    )


def _build_two_buttons_prompt(r: TwoButtonsResult) -> str:
    """Prompt: a label on each button, impossibility as a bottom caption."""
    return _PROMPT_PREAMBLE + (
        "This is the 'two buttons' / sweating-decision meme. The top panel "
        "shows two red buttons.\n"
        f'- Write on/above the LEFT button: "{r.button_a_label}"\n'
        f'- Write on/above the RIGHT button: "{r.button_b_label}"\n'
        "- Add a bold white meme caption with a black outline across the "
        f'BOTTOM of the whole image: "{r.impossibility}"\n'
        "Keep the sweating man and the buttons exactly as drawn."
    )


def _build_arc_prompt(r: ArcResult) -> str:
    """Prompt: four ascending captions on the expanding-brain panels."""
    return _PROMPT_PREAMBLE + (
        "This is the expanding-brain meme: four rows, each with a blank "
        "caption area on the LEFT and a brain image on the RIGHT, escalating "
        "from a plain brain (top) to a glowing cosmic brain (bottom).\n"
        f'- Top row caption (the "before" state): "{r.before}"\n'
        f'- Second row caption (the catalyst): "{r.catalyst}"\n'
        f'- Third row caption (the middle): "{r.middle}"\n'
        f'- Bottom row caption (the "after" state): "{r.after}"\n'
        "Place black text in each left caption box; bottom-to-top escalates."
    )


def _build_compass_prompt(r: CompassResult) -> str:
    """Prompt: axis-pole labels, a marker at the position, a caption."""
    a1_neg, a1_pos = r.axis_1_poles
    a2_neg, a2_pos = r.axis_2_poles
    return _PROMPT_PREAMBLE + (
        "This is a 2x2 political-compass-style quadrant grid with a "
        "horizontal and a vertical axis.\n"
        f'- Label the LEFT end of the horizontal axis: "{a1_neg}"\n'
        f'- Label the RIGHT end of the horizontal axis: "{a1_pos}"\n'
        f'- Label the BOTTOM end of the vertical axis: "{a2_neg}"\n'
        f'- Label the TOP end of the vertical axis: "{a2_pos}"\n'
        "- Draw a single clearly marked dot/marker located at horizontal "
        f"position {r.axis_1_position:+.2f} and vertical position "
        f"{r.axis_2_position:+.2f} (both on a -1.0 to +1.0 scale, 0 is "
        "centre).\n"
        f'- Add a caption beneath the grid: "{r.why_these_axes}"'
    )


_PROMPT_BUILDERS = {
    "IcebergResult": _build_iceberg_prompt,
    "TwoButtonsResult": _build_two_buttons_prompt,
    "ArcResult": _build_arc_prompt,
    "CompassResult": _build_compass_prompt,
}


def build_prompt(typed_result: TypedResult) -> str:
    """Return the gpt-image-2 fill prompt for ``typed_result``.

    The prompt embeds the result's slot text verbatim and instructs the
    model where to place it. Raises :class:`RenderError` for an unknown
    result type.
    """
    name = type(typed_result).__name__
    builder = _PROMPT_BUILDERS.get(name)
    if builder is None:
        raise RenderError(
            f"cannot render result of type {name!r}; expected one of "
            f"{sorted(_PROMPT_BUILDERS)}"
        )
    return builder(typed_result)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Template-image inputs
# ---------------------------------------------------------------------------


def _compass_base() -> Image.Image:
    """Draw a clean 2x2 colored-quadrant compass base with axis arrows.

    The compass has no photographic reference image, so its template is
    generated with Pillow at call time — a colored 2x2 grid with axis
    arrows, which gpt-image-2 then labels and marks.
    """
    img = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    margin = 120
    grid_top = margin
    grid_size = WIDTH - 2 * margin
    grid_bottom = grid_top + grid_size
    left, right = margin, WIDTH - margin
    cx = (left + right) // 2
    cy = (grid_top + grid_bottom) // 2

    quadrants = [
        ((left, grid_top, cx, cy), (210, 226, 245)),       # top-left
        ((cx, grid_top, right, cy), (214, 240, 220)),      # top-right
        ((left, cy, cx, grid_bottom), (245, 224, 214)),    # bottom-left
        ((cx, cy, right, grid_bottom), (242, 238, 208)),   # bottom-right
    ]
    for box, color in quadrants:
        draw.rectangle(box, fill=color)
    draw.rectangle((left, grid_top, right, grid_bottom), outline=(30, 30, 32), width=4)

    # Axis arrows through the centre.
    draw.line((left - 30, cy, right + 30, cy), fill=(30, 30, 32), width=5)
    draw.line((cx, grid_top - 30, cx, grid_bottom + 30), fill=(30, 30, 32), width=5)
    a = 18
    draw.polygon(
        [(right + 30, cy), (right + 30 - a, cy - a), (right + 30 - a, cy + a)],
        fill=(30, 30, 32),
    )
    draw.polygon(
        [(cx, grid_top - 30), (cx - a, grid_top - 30 + a), (cx + a, grid_top - 30 + a)],
        fill=(30, 30, 32),
    )
    return img


def _template_image(typed_result: TypedResult) -> tuple[Image.Image, str]:
    """Return the template image for ``typed_result`` and a JPEG/PNG label.

    For iceberg / two_buttons / arc this is the real reference jpg; for the
    compass it is the Pillow-drawn quadrant base. Raises :class:`RenderError`
    for an unknown result type.
    """
    name = type(typed_result).__name__
    if name == "CompassResult":
        return _compass_base(), "compass.png"
    filename = _REFERENCE_FILES.get(name)
    if filename is None:
        raise RenderError(
            f"cannot render result of type {name!r}; expected one of "
            f"{sorted(_PROMPT_BUILDERS)}"
        )
    path = REFERENCE_DIR / filename
    if not path.exists():
        raise RenderError(f"reference template image is missing: {path}")
    return Image.open(path).convert("RGB"), filename


# ---------------------------------------------------------------------------
# gpt-image-2 render path
# ---------------------------------------------------------------------------


def resolve_render_model() -> str:
    """Return the render model — ``RENDER_MODEL`` env, else the default."""
    return os.environ.get("RENDER_MODEL") or DEFAULT_RENDER_MODEL


def get_openai_client(api_key: Optional[str] = None):
    """Build an ``openai`` SDK client for the images API.

    The key comes from ``api_key`` or the ``OPENAI_API_KEY`` environment
    variable. Returns ``None`` when no key is available — the signal for
    :func:`render` to use the Pillow fallback. The SDK is imported lazily so
    importing this module never requires it and never touches the network.
    """
    key = api_key or os.environ.get(API_KEY_ENV)
    if not key:
        return None
    from openai import OpenAI

    return OpenAI(api_key=key)


def _render_via_openai(
    typed_result: TypedResult,
    *,
    api_key: Optional[str] = None,
    client: Optional[object] = None,
) -> Optional[Image.Image]:
    """Render ``typed_result`` with gpt-image-2; return the image or ``None``.

    Sends the template image + the per-template fill prompt to the OpenAI
    ``images/edits`` endpoint (``model=gpt-image-2``, ``quality=high``,
    ``size=1024x1536``). gpt-image-2 does not accept ``input_fidelity`` —
    that parameter is deliberately not sent.

    Returns the filled PIL image on success. Returns ``None`` (with a logged
    warning) when no API key is available or the call fails — the caller
    then uses the Pillow fallback. Never raises on an API failure.
    """
    active_client = client if client is not None else get_openai_client(api_key)
    if active_client is None:
        logger.warning(
            "%s is not set — gpt-image-2 render disabled, using the Pillow "
            "fallback render.",
            API_KEY_ENV,
        )
        return None

    prompt = build_prompt(typed_result)
    template_img, filename = _template_image(typed_result)

    buffer = io.BytesIO()
    template_img.save(buffer, "PNG")
    buffer.seek(0)
    buffer.name = filename

    try:
        response = active_client.images.edit(  # type: ignore[attr-defined]
            model=resolve_render_model(),
            image=buffer,
            prompt=prompt,
            quality="high",
            size=RENDER_SIZE,
        )
    except Exception as exc:  # network / API / SDK failure
        logger.warning(
            "gpt-image-2 render call failed (%s) — using the Pillow "
            "fallback render.",
            exc,
        )
        return None

    try:
        b64 = response.data[0].b64_json  # type: ignore[attr-defined]
        raw = base64.b64decode(b64)
        return Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as exc:
        logger.warning(
            "gpt-image-2 response had no usable image (%s) — using the "
            "Pillow fallback render.",
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Minimal Pillow fallback render
# ---------------------------------------------------------------------------
#
# Used only when the gpt-image-2 path is unavailable. It is intentionally
# simple — a legible text card on the template image — not a full meme. Its
# job is to never crash the offline pipeline, not to look great.

_GLYPH_FALLBACKS = {
    "—": " - ", "–": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", " ": " ",
}


def _sanitize(text: str) -> str:
    """Replace non-ASCII punctuation the default Pillow font cannot render."""
    for bad, good in _GLYPH_FALLBACKS.items():
        text = text.replace(bad, good)
    return text


def _slot_lines(typed_result: TypedResult) -> list[str]:
    """Return the typed result's slot texts as labelled lines for the card."""
    name = type(typed_result).__name__
    if name == "IcebergResult":
        r = typed_result  # type: ignore[assignment]
        return [
            f"SURFACE: {r.surface}",
            f"FIRST LAYER: {r.first_layer}",
            f"SECOND LAYER: {r.second_layer}",
            f"ABYSS: {r.abyss}",
        ]
    if name == "TwoButtonsResult":
        r = typed_result  # type: ignore[assignment]
        return [
            f"LEFT BUTTON: {r.button_a_label}",
            f"RIGHT BUTTON: {r.button_b_label}",
            f"{r.impossibility}",
        ]
    if name == "ArcResult":
        r = typed_result  # type: ignore[assignment]
        return [
            f"BEFORE: {r.before}",
            f"CATALYST: {r.catalyst}",
            f"MIDDLE: {r.middle}",
            f"AFTER: {r.after}",
        ]
    if name == "CompassResult":
        r = typed_result  # type: ignore[assignment]
        return [
            f"AXIS 1: {r.axis_1_poles[0]} <-> {r.axis_1_poles[1]}  ({r.axis_1_position:+.2f})",
            f"AXIS 2: {r.axis_2_poles[0]} <-> {r.axis_2_poles[1]}  ({r.axis_2_position:+.2f})",
            f"{r.why_these_axes}",
        ]
    raise RenderError(f"cannot render result of type {name!r}")


def _wrap(draw, text, font, max_width) -> list[str]:
    """Greedy word-wrap ``text`` so each line fits within ``max_width`` px."""
    words = _sanitize(text or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _render_fallback(typed_result: TypedResult) -> Image.Image:
    """Composite the result's slot text onto its template image with Pillow.

    The minimal degraded render: the template image with a translucent card
    of legible slot text. Never raises except for an unknown result type.
    """
    name = type(typed_result).__name__
    if name == "CompassResult":
        img = _compass_base()
    else:
        img, _ = _template_image(typed_result)
        img = img.resize((WIDTH, HEIGHT))

    draw = ImageDraw.Draw(img, "RGBA")
    font = ImageFont.load_default(size=30)
    pad = 50
    max_w = WIDTH - 2 * pad

    wrapped: list[str] = []
    for line in _slot_lines(typed_result):
        wrapped.extend(_wrap(draw, line, font, max_w))
        wrapped.append("")
    if wrapped and wrapped[-1] == "":
        wrapped.pop()

    line_h = 40
    card_h = line_h * len(wrapped) + 2 * pad
    card_top = (HEIGHT - card_h) // 2
    draw.rectangle(
        (pad // 2, card_top, WIDTH - pad // 2, card_top + card_h),
        fill=(0, 0, 0, 200),
    )
    y = card_top + pad
    for line in wrapped:
        draw.text((pad, y), line, font=font, fill=(255, 255, 255))
        y += line_h
    return img


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def render(
    typed_result: TypedResult,
    out_path: Optional[Union[str, Path]] = None,
    *,
    api_key: Optional[str] = None,
    client: Optional[object] = None,
) -> Union[Image.Image, Path]:
    """Render ``typed_result`` into a meme portrait.

    ``typed_result`` is any of the four typed result models from
    :mod:`agent.state`. The matching real meme template and a per-template
    fill prompt are sent to OpenAI's ``gpt-image-2`` image model, which
    fills the meme text into the template art.

    Returns a :class:`PIL.Image.Image` when ``out_path`` is ``None``;
    otherwise writes a PNG to ``out_path`` and returns that path.

    Graceful degradation: if ``OPENAI_API_KEY`` is missing or the API call
    fails, a minimal Pillow text render is produced instead and a warning is
    logged — render never hard-crashes the offline pipeline.

    ``client`` injects a pre-built (or mock) OpenAI-compatible client;
    otherwise one is built from ``api_key`` / ``OPENAI_API_KEY``.

    Raises :class:`RenderError` only for an unrecognised result type.
    """
    if type(typed_result).__name__ not in _PROMPT_BUILDERS:
        raise RenderError(
            f"cannot render result of type {type(typed_result).__name__!r}; "
            f"expected one of {sorted(_PROMPT_BUILDERS)}"
        )

    img = _render_via_openai(typed_result, api_key=api_key, client=client)
    if img is None:
        img = _render_fallback(typed_result)

    if out_path is None:
        return img
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path
