"""Unit tests for the offline rendering stage (``pipeline/render.py``).

Render is stage 3 of the offline pipeline: given a filled typed result it
produces the final meme portrait. The real renderer sends a meme-template
image plus a per-template fill prompt to OpenAI's gpt-image-2 image model.

**No test here touches the network or spends on a real image call.** The
OpenAI image API is mocked throughout. Coverage:

* :func:`render` builds the correct per-template fill prompt, containing
  the typed result's slot text;
* with a mocked API, ``render`` returns a :class:`PIL.Image.Image` and
  writes a PNG file when given a path;
* a missing ``OPENAI_API_KEY`` triggers the Pillow fallback — no crash;
* an API failure triggers the Pillow fallback — no crash;
* an unrecognised result type raises a clear :class:`RenderError`;
* the three real reference template images exist.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from agent.state import ArcResult, CompassResult, IcebergResult, TwoButtonsResult
from pipeline.render import (
    HEIGHT,
    REFERENCE_DIR,
    WIDTH,
    RenderError,
    build_prompt,
    render,
)


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
        impossibility="Every month spent deciding is itself the decision, and both doors close.",
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
# Mock OpenAI image client
# ---------------------------------------------------------------------------


def _fake_png_b64() -> str:
    """A real, decodable 1x1 PNG encoded as base64 — stand-in for a render."""
    import base64

    buf = io.BytesIO()
    Image.new("RGB", (WIDTH, HEIGHT), (123, 45, 67)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


class _MockImageData:
    def __init__(self, b64: str) -> None:
        self.b64_json = b64


class _MockImageResponse:
    def __init__(self, b64: str) -> None:
        self.data = [_MockImageData(b64)]


class _MockImages:
    """Stands in for ``client.images``; records the edit() call arguments."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict] = []

    def edit(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("simulated gpt-image-2 API failure")
        return _MockImageResponse(_fake_png_b64())


class MockOpenAIClient:
    """A minimal mock OpenAI client exposing ``.images.edit(...)``."""

    def __init__(self, fail: bool = False) -> None:
        self.images = _MockImages(fail=fail)


# ---------------------------------------------------------------------------
# Reference template images exist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", ["iceberg.jpg", "two_buttons.jpg", "arc.jpg"])
def test_reference_image_exists(filename):
    """Each real reference meme-template jpg is present and openable."""
    path = REFERENCE_DIR / filename
    assert path.exists(), f"missing reference image: {path}"
    with Image.open(path) as img:
        img.verify()


# ---------------------------------------------------------------------------
# Per-template fill prompts contain the result's slot text
# ---------------------------------------------------------------------------


def test_iceberg_prompt_contains_all_layers():
    """The iceberg prompt embeds all four depth-layer slot texts verbatim."""
    r = sample_iceberg()
    prompt = build_prompt(r)
    assert r.surface in prompt
    assert r.first_layer in prompt
    assert r.second_layer in prompt
    assert r.abyss in prompt
    assert "iceberg" in prompt.lower()


def test_two_buttons_prompt_contains_labels_and_caption():
    """The two-buttons prompt embeds both button labels and the impossibility."""
    r = sample_two_buttons()
    prompt = build_prompt(r)
    assert r.button_a_label in prompt
    assert r.button_b_label in prompt
    assert r.impossibility in prompt
    assert "LEFT" in prompt and "RIGHT" in prompt


def test_arc_prompt_contains_all_panels():
    """The arc prompt embeds all four ascending-panel captions verbatim."""
    r = sample_arc()
    prompt = build_prompt(r)
    assert r.before in prompt
    assert r.catalyst in prompt
    assert r.middle in prompt
    assert r.after in prompt


def test_compass_prompt_contains_poles_and_caption():
    """The compass prompt embeds the four pole labels and the axes caption."""
    r = sample_compass()
    prompt = build_prompt(r)
    for pole in (*r.axis_1_poles, *r.axis_2_poles):
        assert pole in prompt
    assert r.why_these_axes in prompt


def test_build_prompt_rejects_unknown_type():
    """build_prompt raises RenderError for a non-typed-result object."""
    with pytest.raises(RenderError):
        build_prompt(object())


# ---------------------------------------------------------------------------
# render() with a mocked OpenAI client
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_render_returns_image_with_mock_client(name):
    """With a mocked gpt-image-2 client, render() returns a PIL Image."""
    client = MockOpenAIClient()
    img = render(SAMPLES[name](), client=client)
    assert isinstance(img, Image.Image)
    assert img.width > 0 and img.height > 0
    # The mock client's edit() was actually called once.
    assert len(client.images.calls) == 1


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_render_sends_correct_prompt_to_api(name):
    """render() passes the per-template fill prompt (with slot text) to edit()."""
    client = MockOpenAIClient()
    result = SAMPLES[name]()
    render(result, client=client)
    call = client.images.calls[0]
    assert call["prompt"] == build_prompt(result)
    assert call["quality"] == "high"
    # gpt-image-2 does NOT accept input_fidelity — it must not be sent.
    assert "input_fidelity" not in call


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_render_writes_png_file_with_mock_client(tmp_path, name):
    """When given a path, render() writes a non-empty PNG and returns the path."""
    client = MockOpenAIClient()
    out = tmp_path / f"{name}.png"
    returned = render(SAMPLES[name](), out_path=out, client=client)
    assert returned == out
    assert out.exists()
    assert out.stat().st_size > 0
    with Image.open(out) as img:
        img.verify()


def test_render_creates_missing_output_dir(tmp_path):
    """render() creates parent directories for the output path."""
    client = MockOpenAIClient()
    out = tmp_path / "nested" / "deep" / "portrait.png"
    render(sample_arc(), out_path=out, client=client)
    assert out.exists()


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_render_produces_valid_png_bytes(name):
    """The image render() returns encodes to non-empty, valid PNG bytes."""
    client = MockOpenAIClient()
    img = render(SAMPLES[name](), client=client)
    buffer = io.BytesIO()
    img.save(buffer, "PNG")
    data = buffer.getvalue()
    assert len(data) > 0
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "output is not a PNG"


# ---------------------------------------------------------------------------
# Graceful degradation — missing key and API failure both fall back
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_missing_api_key_triggers_pillow_fallback(monkeypatch, name):
    """No OPENAI_API_KEY -> Pillow fallback render, no crash, valid image."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # No client injected and no key -> get_openai_client returns None.
    img = render(SAMPLES[name]())
    assert isinstance(img, Image.Image)
    assert img.size == (WIDTH, HEIGHT)


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_api_failure_triggers_pillow_fallback(name):
    """A gpt-image-2 API failure -> Pillow fallback render, no crash."""
    client = MockOpenAIClient(fail=True)
    img = render(SAMPLES[name](), client=client)
    assert isinstance(img, Image.Image)
    assert img.size == (WIDTH, HEIGHT)
    # The failing call was still attempted.
    assert len(client.images.calls) == 1


def test_fallback_writes_png_file(tmp_path, monkeypatch):
    """The fallback path also honours the out_path write contract."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = tmp_path / "fallback.png"
    returned = render(sample_iceberg(), out_path=out)
    assert returned == out
    assert out.exists()
    with Image.open(out) as img:
        img.verify()


def test_fallback_handles_long_text(monkeypatch):
    """Slot values at the char ceiling do not crash the Pillow fallback."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = IcebergResult(
        surface="x" * 120,
        first_layer=("word " * 24).strip()[:120],
        second_layer="y" * 120,
        abyss=("deep " * 24).strip()[:120],
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
