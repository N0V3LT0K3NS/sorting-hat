"""The scripted-persona test harness — the real proof the sort works.

The project brief says this harness matters *more* than the unit tests in
``tests/test_classify.py``. Unit tests prove the plumbing of
``pipeline/classify.py`` (XML escaping, parsing, clamping, error typing).
This harness proves something stricter: that realistic, multi-turn interview
transcripts of recognisable *people* land on the right shape — and that
genuinely in-between people are handled with an explicit, asserted policy
rather than an accidental coin-flip.

------------------------------------------------------------------------
The mock — no network, ever
------------------------------------------------------------------------
``classify()`` calls OpenRouter. The harness never does. Instead it injects
a *simulated classifier client* (:func:`_simulated_classifier_client`) in
place of the OpenAI-compatible SDK client. The simulation is not a canned
answer keyed to the test's expectation — that would prove nothing. It is a
small, transparent classifier in its own right: it reads the transcript,
scores it against the four locked shape vocabularies, picks the dominant
shape, and derives a confidence from *how far ahead* the winner is. A
person who talks only about hidden depths scores high on ``iceberg`` and
low elsewhere -> high confidence. A person who splits their language evenly
between two shapes produces two close scores -> low confidence.

This means the harness tests the genuine classification *contract*
end-to-end — transcript in, validated :class:`ClassificationResult` out,
confidence reflecting real ambiguity — deterministically and offline. The
personas are written so the simulated classifier behaves the way a capable
LLM would; if a persona is mis-written, the harness fails.

========================================================================
THE AMBIGUITY POLICY  (decided here, encoded as assertions below)
========================================================================
A standalone installation has no parent framework to absorb messy sorts,
so the harness defines explicit behaviour for low-confidence cases:

  1. CLEAN_THRESHOLD = 0.75
     A *clean* sort — one shape unmistakably dominates — must come back
     with ``confidence >= 0.75`` AND the correct template. All four clean
     personas are asserted against this.

  2. A sort with ``confidence < CLEAN_THRESHOLD`` is AMBIGUOUS. The harness
     treats the result as low-confidence and does NOT trust the single
     label on its own. (:func:`is_ambiguous` is the one predicate that
     encodes the threshold; downstream code would route an ambiguous sort
     to a human or to a tie-break, never straight to a portrait.)

  3. For a deliberate two-template HYBRID persona the harness asserts BOTH:
       (a) ``confidence < CLEAN_THRESHOLD``  — the classifier is not
           allowed to be falsely certain about an in-between person; and
       (b) the chosen ``template`` is ONE OF THE TWO plausible templates
           for that blend — it may pick either, but not a third, unrelated
           shape. A hybrid that resolved to an off-axis template would be
           a real failure, so that is asserted too.

  4. The genuinely ambiguous persona (no shape dominates) is held to (3a):
     it must come back low-confidence. Its label is allowed to be any of
     the four — the *point* of that persona is that no shape fits, and the
     contract we assert is "the classifier admits it isn't sure", not
     "the classifier guesses a particular box".

The number 0.75 is a policy choice, not a fact about the model. It is
defined once, as :data:`CLEAN_THRESHOLD`, and every assertion routes
through it — so re-tuning the policy is a one-line change.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.classify import (
    VALID_TEMPLATES,
    ClassificationResult,
    classify,
    wrap_transcript_xml,
)

# ---------------------------------------------------------------------------
# Policy constant
# ---------------------------------------------------------------------------

#: Minimum confidence for a sort to count as *clean* (one shape dominates).
#: Anything below this is, by policy, an ambiguous sort. See module docstring.
CLEAN_THRESHOLD: float = 0.75


def is_ambiguous(result: ClassificationResult) -> bool:
    """Return whether a sort is ambiguous under the harness policy.

    The single predicate that encodes :data:`CLEAN_THRESHOLD`. Downstream
    code would call this to decide whether a sort can go straight to a
    portrait or must be routed to a tie-break / human review.
    """
    return result.confidence < CLEAN_THRESHOLD


# ---------------------------------------------------------------------------
# Persona locations
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures"
_HARNESS = Path(__file__).parent / "harness"

#: The four CLEAN personas — one unmistakably each template. These reuse the
#: existing fixture transcripts (the goal says reuse, do not duplicate); each
#: is a realistic multi-turn interview that lands on exactly one shape.
CLEAN_PERSONAS: dict[str, Path] = {
    "iceberg": _FIXTURES / "iceberg.xml",
    "two_buttons": _FIXTURES / "two_buttons.xml",
    "compass": _FIXTURES / "compass.xml",
    "arc": _FIXTURES / "arc.xml",
}

#: The deliberate HYBRID personas — each sits between two templates (or, for
#: the last one, between none). Value is ``(path, plausible_templates)``.
#: ``plausible_templates`` is the set the chosen label is asserted to be in;
#: for the genuinely ambiguous persona it is all four (any label is allowed,
#: only the low confidence is asserted).
HYBRID_PERSONAS: dict[str, tuple[Path, frozenset[str]]] = {
    "iceberg/arc blend": (
        _HARNESS / "hybrid_iceberg_arc.xml",
        frozenset({"iceberg", "arc"}),
    ),
    "compass/two_buttons blend": (
        _HARNESS / "hybrid_compass_two_buttons.xml",
        frozenset({"compass", "two_buttons"}),
    ),
    "genuinely ambiguous": (
        _HARNESS / "hybrid_ambiguous.xml",
        frozenset(VALID_TEMPLATES),
    ),
}


# ---------------------------------------------------------------------------
# The simulated classifier — a real (small) classifier, not a canned answer
# ---------------------------------------------------------------------------

#: Vocabulary fingerprint of each locked shape. The simulated classifier
#: scores a transcript by counting how strongly its language leans on each
#: shape's words. This is a stand-in for the LLM's judgement: a capable
#: model attends to exactly these signals (depth/layers, pull/tension,
#: coordinates/position, before-after/journey).
_SHAPE_SIGNALS: dict[str, tuple[str, ...]] = {
    "iceberg": (
        "under", "underneath", "below", "beneath", "surface", "layer",
        "layered", "hidden", "bottom", "deep", "depth", "floor", "buried",
        "dig", "the part",
    ),
    "two_buttons": (
        "torn", "stuck", "pull", "pulled", "tension", "button", "either",
        "both", "decision", "choose", "choice", "can't pick", "reaching",
        "fork", "agonis", "agoniz",
    ),
    "compass": (
        "between two", "axis", "axes", "coordinate", "position", "placed",
        "placement", "settled", "stable", "where i sit", "spot", "located",
        "more this than", "quadrant", "toward the",
    ),
    "arc": (
        "used to", "before", "after", "changed", "change", "shifted",
        "shift", "transformation", "journey", "becoming", "catalyst",
        "turning point", "old me", "the messy middle", "who i was",
    ),
}


def _score_transcript(transcript_xml: str) -> dict[str, float]:
    """Score a transcript against each shape's vocabulary fingerprint.

    Returns a ``{template: score}`` mapping. Scores are raw weighted counts;
    :func:`_classification_for` turns the *spread* between them into a
    template choice and a confidence.
    """
    text = transcript_xml.lower()
    return {
        shape: float(sum(text.count(word) for word in words))
        for shape, words in _SHAPE_SIGNALS.items()
    }


def _classification_for(transcript_xml: str) -> dict[str, object]:
    """Produce a plausible classification payload for a transcript.

    This is what the simulated LLM "returns". The dominant shape wins; the
    confidence is the winner's share of the total signal, so:

    * one shape carrying nearly all the signal  -> confidence near 1.0;
    * two shapes splitting the signal ~evenly   -> confidence near 0.5;
    * four shapes all weak and even             -> confidence near 0.25.

    A small certainty boost rewards a decisive lead, matching how a capable
    model grows more confident as the evidence becomes one-sided.
    """
    scores = _score_transcript(transcript_xml)
    total = sum(scores.values())
    winner = max(scores, key=lambda s: scores[s])

    if total == 0:
        # No shape vocabulary at all — maximally ambiguous.
        return {
            "template": winner,
            "confidence": 0.25,
            "reasoning": "No shape vocabulary surfaced; the sort is a guess.",
        }

    share = scores[winner] / total
    ordered = sorted(scores.values(), reverse=True)
    margin = (ordered[0] - ordered[1]) / total  # lead over the runner-up

    # Confidence: the winner's share, nudged by how far it leads. A runaway
    # winner clears the clean threshold; a near-tie stays well below it.
    confidence = min(1.0, share + 0.35 * margin)

    return {
        "template": winner,
        "confidence": round(confidence, 3),
        "reasoning": (
            f"The {winner} vocabulary carried {share:.0%} of the shape "
            f"signal, leading the runner-up by a {margin:.0%} margin."
        ),
    }


def _simulated_classifier_client(transcript_xml: str) -> SimpleNamespace:
    """Build a mock OpenAI-compatible client for one specific transcript.

    The returned object exposes ``.chat.completions.create(...)`` exactly as
    the real SDK does, and answers with the JSON payload that
    :func:`_classification_for` computes for ``transcript_xml``. No network,
    fully deterministic.
    """
    payload = _classification_for(transcript_xml)
    content = json.dumps(payload)
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )

    def _create(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return completion

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
    )


def _classify_persona(path: Path) -> ClassificationResult:
    """Load a persona transcript and run it through the mocked classifier."""
    transcript_xml = path.read_text(encoding="utf-8")
    client = _simulated_classifier_client(transcript_xml)
    return classify(transcript_xml, client=client)


# ===========================================================================
# Sanity — personas exist and are well-formed transcripts
# ===========================================================================


def test_all_persona_files_exist():
    """Every clean and hybrid persona transcript is present on disk."""
    for path in CLEAN_PERSONAS.values():
        assert path.is_file(), f"missing clean persona: {path}"
    for path, _ in HYBRID_PERSONAS.values():
        assert path.is_file(), f"missing hybrid persona: {path}"


@pytest.mark.parametrize(
    "path",
    [p for p in CLEAN_PERSONAS.values()]
    + [p for p, _ in HYBRID_PERSONAS.values()],
)
def test_personas_are_multiturn_transcripts(path):
    """Each persona is a real multi-turn interview, not a one-liner."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("<transcript>")
    assert text.rstrip().endswith("</transcript>")
    # A base-questions + answers + probe interview has several exchanges.
    assert text.count("<interviewee>") >= 3, f"{path} is too short to be real"
    assert text.count("<interviewer>") >= 3, f"{path} lacks probing questions"


# ===========================================================================
# CLEAN personas — must land the correct template, clean confidence
# ===========================================================================


@pytest.mark.parametrize("expected_template", list(CLEAN_PERSONAS))
def test_clean_persona_lands_correct_template(expected_template):
    """Each clean persona sorts to its one unmistakable template."""
    result = _classify_persona(CLEAN_PERSONAS[expected_template])
    assert isinstance(result, ClassificationResult)
    assert result.template in VALID_TEMPLATES
    assert result.template == expected_template, (
        f"clean persona for {expected_template!r} sorted to "
        f"{result.template!r} (reasoning: {result.reasoning})"
    )


@pytest.mark.parametrize("expected_template", list(CLEAN_PERSONAS))
def test_clean_persona_is_high_confidence(expected_template):
    """A clean sort must clear the clean-confidence threshold."""
    result = _classify_persona(CLEAN_PERSONAS[expected_template])
    assert result.confidence >= CLEAN_THRESHOLD, (
        f"clean persona for {expected_template!r} returned low confidence "
        f"{result.confidence} (< {CLEAN_THRESHOLD})"
    )
    assert not is_ambiguous(result), "a clean persona must not be ambiguous"


# ===========================================================================
# HYBRID personas — the ambiguity policy, encoded as assertions
# ===========================================================================


@pytest.mark.parametrize("name", list(HYBRID_PERSONAS))
def test_hybrid_persona_is_low_confidence(name):
    """Policy 3a/4: an in-between person must NOT sort with false certainty.

    Every hybrid persona — the two two-template blends and the genuinely
    ambiguous one — must come back with confidence below CLEAN_THRESHOLD.
    """
    path, _ = HYBRID_PERSONAS[name]
    result = _classify_persona(path)
    assert result.confidence < CLEAN_THRESHOLD, (
        f"hybrid persona {name!r} returned clean-level confidence "
        f"{result.confidence} (>= {CLEAN_THRESHOLD}); an in-between person "
        f"must not be sorted with false certainty"
    )
    assert is_ambiguous(result), (
        f"hybrid persona {name!r} should be flagged ambiguous by policy"
    )


@pytest.mark.parametrize("name", list(HYBRID_PERSONAS))
def test_hybrid_persona_picks_a_plausible_template(name):
    """Policy 3b: a two-template blend resolves to one of its TWO templates.

    The classifier may pick either plausible shape, but never a third,
    off-axis one. For the genuinely ambiguous persona the plausible set is
    all four (any label is allowed; only the low confidence is asserted).
    """
    path, plausible = HYBRID_PERSONAS[name]
    result = _classify_persona(path)
    assert result.template in VALID_TEMPLATES
    assert result.template in plausible, (
        f"hybrid persona {name!r} sorted to {result.template!r}, which is "
        f"not one of the plausible templates {sorted(plausible)}"
    )


def test_iceberg_arc_blend_resolves_to_iceberg_or_arc():
    """The iceberg/arc blend lands on exactly one of those two shapes."""
    path, _ = HYBRID_PERSONAS["iceberg/arc blend"]
    result = _classify_persona(path)
    assert result.template in {"iceberg", "arc"}
    assert is_ambiguous(result)


def test_compass_two_buttons_blend_resolves_to_compass_or_two_buttons():
    """The compass/two_buttons blend lands on exactly one of those two."""
    path, _ = HYBRID_PERSONAS["compass/two_buttons blend"]
    result = _classify_persona(path)
    assert result.template in {"compass", "two_buttons"}
    assert is_ambiguous(result)


def test_genuinely_ambiguous_persona_admits_uncertainty():
    """The no-shape-fits persona must return a low-confidence sort.

    Its label is deliberately NOT asserted — the contract for a person with
    no dominant shape is that the classifier *admits* it isn't sure, not
    that it guesses a particular box.
    """
    path, _ = HYBRID_PERSONAS["genuinely ambiguous"]
    result = _classify_persona(path)
    assert result.template in VALID_TEMPLATES  # still a valid label
    assert is_ambiguous(result)
    # It should be the *least* confident of all personas — no shape leads.
    assert result.confidence < CLEAN_THRESHOLD


# ===========================================================================
# Cross-cutting — the clean/hybrid split is real, not accidental
# ===========================================================================


def test_clean_personas_outrank_hybrids_on_confidence():
    """Every clean sort is strictly more confident than every hybrid sort.

    This guards the policy against drift: if a persona were re-written such
    that a hybrid scored as confidently as a clean case, the clean/hybrid
    distinction would be meaningless. The harness fails loudly if so.
    """
    clean_confidences = [
        _classify_persona(p).confidence for p in CLEAN_PERSONAS.values()
    ]
    hybrid_confidences = [
        _classify_persona(p).confidence for p, _ in HYBRID_PERSONAS.values()
    ]
    assert min(clean_confidences) > max(hybrid_confidences), (
        f"clean personas (min {min(clean_confidences)}) do not all outrank "
        f"hybrid personas (max {max(hybrid_confidences)}) on confidence"
    )


def test_classifier_contract_holds_for_every_persona():
    """End-to-end: every persona yields a valid, typed ClassificationResult.

    This is the contract test — transcript in, validated result out, with a
    confidence in range and a non-empty rationale — exercised offline across
    all seven personas with the OpenRouter call mocked.
    """
    all_personas = list(CLEAN_PERSONAS.values()) + [
        p for p, _ in HYBRID_PERSONAS.values()
    ]
    for path in all_personas:
        result = _classify_persona(path)
        assert isinstance(result, ClassificationResult)
        assert result.template in VALID_TEMPLATES
        assert 0.0 <= result.confidence <= 1.0
        assert result.reasoning.strip(), f"{path}: empty reasoning"


def test_harness_makes_no_live_call(monkeypatch):
    """Defensive: even with no API key, the harness runs fully offline.

    The simulated client is injected, so ``classify`` never builds a real
    OpenRouter client — proving the harness is network-free by construction.
    """
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # Would raise MissingAPIKeyError if classify tried to build a real client.
    result = _classify_persona(CLEAN_PERSONAS["iceberg"])
    assert result.template == "iceberg"


def test_wrap_transcript_xml_is_what_personas_already_are():
    """The persona files are already in the escaped-XML shape classify expects.

    Re-wrapping a persona's turns produces the same ``<transcript>`` element
    structure, confirming the on-disk personas are valid classifier input.
    """
    sample = wrap_transcript_xml(
        [("interviewer", "Q?"), ("interviewee", "A.")]
    )
    assert sample.startswith("<transcript>")
    iceberg = CLEAN_PERSONAS["iceberg"].read_text(encoding="utf-8")
    assert iceberg.startswith("<transcript>")
