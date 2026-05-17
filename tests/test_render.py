"""Unit tests for the offline rendering stage (``pipeline/render.py``).

No test in this file touches the network or an LLM. Rendering is stage 3 of
the offline pipeline: given a filled typed result it composites slot text
onto the matching base meme image and produces a portrait PNG.

Coverage:
* :func:`render` produces a valid, non-empty, sensibly-sized PNG for each of
  the four templates, given a sample filled result;
* ``render`` returns a :class:`PIL.Image.Image` when no path is given and
  writes a PNG file when a path is given;
* long slot values (at the char-limit ceiling and beyond) do not crash
  rendering — text wraps and shrinks to fit;
* an unrecognised result type raises a clear :class:`RenderError`;
* the four base images exist in ``assets/templates/``.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from agent.state import ArcResult, CompassResult, IcebergResult, TwoButtonsResult
from pipeline.render import HEIGHT, TEMPLATES_DIR, WIDTH, RenderError, render


# ---------------------------------------------------------------------------
# Sample filled results — one per template
# ---------------------------------------------------------------------------


def sample_iceberg() -> IcebergResult:
    return IcebergResult(
        surface="The reliable one who always has it handled.",
        first_layer="Quietly keeps score of who shows up for whom.",
        second_layer="Afraid that being needed is the only reason to stay.",
        abyss="Suspects the competence is a wall, not a gift.",
    )


def sample_two_buttons() -> TwoButtonsResult:
    return TwoButtonsResult(
        button_a_label="Stay and build",
        button_a_seduction="The work is finally compounding and walking now wastes a decade.",
        button_b_label="Leave and breathe",
        button_b_seduction="A clean exit is the only thing that has felt honest in months.",
        impossibility="Every month spent deciding is itself the decision, and both doors quietly close.",
    )


def sample_compass() -> CompassResult:
    return CompassResult(
        axis_1_poles=("Improvises", "Plans everything"),
        axis_1_position=-0.4,
        axis_2_poles=("Seeks approval", "Indifferent to approval"),
        axis_2_position=0.65,
        why_these_axes=(
            "How this person moves through the world is governed by spontaneity "
            "versus control, and by how much other people's regard steers them."
        ),
    )


def sample_arc() -> ArcResult:
    return ArcResult(
        before="A steady life that looked complete from the outside.",
        catalyst="One offhand comment that could not be unheard.",
        middle="Living in the gap between the old certainty and no new one.",
        after="Heading toward a smaller life that is actually theirs.",
    )


SAMPLES = {
    "iceberg": sample_iceberg,
    "two_buttons": sample_two_buttons,
    "compass": sample_compass,
    "arc": sample_arc,
}


# ---------------------------------------------------------------------------
# Base images exist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename", ["iceberg.png", "compass.png", "two_buttons.png", "arc.png"]
)
def test_base_image_exists(filename):
    """Each of the four base template PNGs is present and openable."""
    path = TEMPLATES_DIR / filename
    assert path.exists(), f"missing base image: {path}"
    with Image.open(path) as img:
        img.verify()


# ---------------------------------------------------------------------------
# render() produces a valid PNG for each template
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_render_returns_image(name):
    """render() returns a PIL Image of the canvas dimensions for each template."""
    result = SAMPLES[name]()
    img = render(result)
    assert isinstance(img, Image.Image)
    assert img.size == (WIDTH, HEIGHT)
    assert img.width > 0 and img.height > 0


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_render_produces_valid_png_bytes(name):
    """The rendered image encodes to non-empty, valid PNG bytes."""
    img = render(SAMPLES[name]())
    buffer = io.BytesIO()
    img.save(buffer, "PNG")
    data = buffer.getvalue()
    assert len(data) > 0
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "output is not a PNG"

    # Round-trip: the bytes re-open as a sensible image.
    reopened = Image.open(io.BytesIO(data))
    reopened.load()
    assert reopened.format == "PNG"
    assert reopened.size == (WIDTH, HEIGHT)


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_render_writes_png_file(tmp_path, name):
    """When given a path, render() writes a non-empty PNG and returns the path."""
    out = tmp_path / f"{name}.png"
    returned = render(SAMPLES[name](), out_path=out)
    assert returned == out
    assert out.exists()
    assert out.stat().st_size > 0
    with Image.open(out) as img:
        img.verify()


def test_render_creates_missing_output_dir(tmp_path):
    """render() creates parent directories for the output path."""
    out = tmp_path / "nested" / "deep" / "portrait.png"
    render(sample_arc(), out_path=out)
    assert out.exists()


# ---------------------------------------------------------------------------
# Long text does not crash rendering
# ---------------------------------------------------------------------------


def test_render_iceberg_long_text():
    """Iceberg layers at the 120-char ceiling render without crashing."""
    long = "x" * 120
    spaced = ("word " * 24).strip()[:120]
    result = IcebergResult(
        surface=long,
        first_layer=spaced,
        second_layer=long,
        abyss=spaced,
    )
    img = render(result)
    assert img.size == (WIDTH, HEIGHT)


def test_render_two_buttons_long_text():
    """Two Buttons fields at their char ceilings render without crashing."""
    result = TwoButtonsResult(
        button_a_label="a" * 40,
        button_a_seduction="seduction " * 16,
        button_b_label="longwordwithoutanyspaces" + "z" * 16,
        button_b_seduction="b" * 160,
        impossibility="impossible " * 18,
    )
    img = render(result)
    assert img.size == (WIDTH, HEIGHT)


def test_render_compass_long_text_and_extremes():
    """Compass with long poles, a 300-char caption and extreme positions."""
    result = CompassResult(
        axis_1_poles=("a very long pole name " * 3, "another long pole label here"),
        axis_1_position=-1.0,
        axis_2_poles=("p" * 30, "q" * 30),
        axis_2_position=1.0,
        why_these_axes="why " * 75,
    )
    img = render(result)
    assert img.size == (WIDTH, HEIGHT)


def test_render_arc_long_text():
    """Arc panels at the 200-char ceiling render without crashing."""
    long = "y" * 200
    spaced = ("panel " * 33).strip()[:200]
    result = ArcResult(before=long, catalyst=spaced, middle=long, after=spaced)
    img = render(result)
    assert img.size == (WIDTH, HEIGHT)


def test_render_compass_dot_within_bounds_at_extremes():
    """A corner position still renders (dot stays a finite image, no crash)."""
    for x in (-1.0, 1.0):
        for y in (-1.0, 1.0):
            result = CompassResult(
                axis_1_poles=("L", "R"),
                axis_1_position=x,
                axis_2_poles=("D", "U"),
                axis_2_position=y,
                why_these_axes="corner case",
            )
            img = render(result)
            assert img.size == (WIDTH, HEIGHT)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_render_rejects_unknown_type():
    """An object that is not a typed result raises a clear RenderError."""
    with pytest.raises(RenderError):
        render(object())


def test_render_rejects_plain_dict():
    """A dict is not a typed result model and is rejected."""
    with pytest.raises(RenderError):
        render({"surface": "x", "first_layer": "y"})
