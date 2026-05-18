"""Pure data layer for the sorting-hat interview kiosk.

This module defines the typed state carried through a single interview and
the four typed probe results — one per locked meme template:

* ``IcebergResult``      — depth        (Iceberg)
* ``TwoButtonsResult``   — tension      (Two Buttons)
* ``CompassResult``      — position     (2x2 Compass)
* ``ArcResult``          — trajectory   (Anakin/Padmé Arc)

The four templates are orthogonal by construction and locked: no fifth
template, no sub-categories. Everything downstream — the live probes, the
offline ``classify``/``fill`` pipeline, the renderer — depends on these
types being correct and stable.

There is no LiveKit dependency and no I/O here. These are plain pydantic
models so that char limits and the compass position range are validated at
construction time.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Template identifiers
# ---------------------------------------------------------------------------

# Canonical signal-weight field names on ``InterviewState``, mapped to the
# short template label the rest of the system uses. The ``InterviewState``
# leading-template helper returns one of these labels (or ``None``).
TEMPLATE_SIGNALS: dict[str, str] = {
    "iceberg_signal": "iceberg",
    "two_buttons_signal": "two_buttons",
    "compass_signal": "compass",
    "arc_signal": "arc",
}


# ---------------------------------------------------------------------------
# Typed probe results — one per template
# ---------------------------------------------------------------------------


class IcebergResult(BaseModel):
    """Result of the Iceberg probe — the *depth* template.

    Four layers descending from what a person shows the world down to what
    they barely admit to themselves. Each layer is a short phrase or
    sentence; the renderer stacks them, so each is capped near 120 chars.
    """

    surface: str = Field(
        ...,
        max_length=120,
        description="What the person shows the world. Max ~120 chars.",
    )
    first_layer: str = Field(
        ...,
        max_length=120,
        description="What's just under the surface, lightly hidden. Max ~120 chars.",
    )
    second_layer: str = Field(
        ...,
        max_length=120,
        description="What's deeper, rarely spoken aloud. Max ~120 chars.",
    )
    abyss: str = Field(
        ...,
        max_length=120,
        description="The bottom layer — barely admitted, even privately. Max ~120 chars.",
    )


class TwoButtonsResult(BaseModel):
    """Result of the Two Buttons probe — the *tension* template.

    Two genuinely seductive options the person is torn between, plus the
    reason they cannot simply have both. Button labels stay short (~4 words,
    capped at 40 chars); each seduction is a single sentence (~160 chars).
    """

    button_a_label: str = Field(
        ...,
        max_length=40,
        description="Short label for option A — roughly 4 words. Max 40 chars.",
    )
    button_a_seduction: str = Field(
        ...,
        max_length=160,
        description="One sentence on why option A is tempting. Max ~160 chars.",
    )
    button_b_label: str = Field(
        ...,
        max_length=40,
        description="Short label for option B — roughly 4 words. Max 40 chars.",
    )
    button_b_seduction: str = Field(
        ...,
        max_length=160,
        description="One sentence on why option B is tempting. Max ~160 chars.",
    )
    impossibility: str = Field(
        ...,
        max_length=200,
        description="Why the person cannot simply have both. Max ~200 chars.",
    )


class CompassResult(BaseModel):
    """Result of the Compass probe — the *position* template.

    Two orthogonal axes, each defined by a pair of opposing poles, with the
    person located on each axis in the range -1.0 .. 1.0 (0.0 is dead
    centre). ``why_these_axes`` explains why these particular axes capture
    this person.
    """

    axis_1_poles: tuple[str, str] = Field(
        ...,
        description="The two opposing poles of axis 1, as (negative, positive).",
    )
    axis_1_position: float = Field(
        ...,
        ge=-1.0,
        le=1.0,
        description="Position on axis 1 in -1.0 .. 1.0 (0.0 is centre).",
    )
    axis_2_poles: tuple[str, str] = Field(
        ...,
        description="The two opposing poles of axis 2, as (negative, positive).",
    )
    axis_2_position: float = Field(
        ...,
        ge=-1.0,
        le=1.0,
        description="Position on axis 2 in -1.0 .. 1.0 (0.0 is centre).",
    )
    why_these_axes: str = Field(
        ...,
        max_length=300,
        description="Why these two axes capture this person. Max ~300 chars.",
    )

    @field_validator("axis_1_poles", "axis_2_poles")
    @classmethod
    def _poles_are_nonempty(cls, value: tuple[str, str]) -> tuple[str, str]:
        """Each axis must name exactly two non-empty poles."""
        if len(value) != 2:
            raise ValueError("each axis must have exactly two poles")
        if any(not pole.strip() for pole in value):
            raise ValueError("axis poles must be non-empty strings")
        return value


class ArcResult(BaseModel):
    """Result of the Arc probe — the *trajectory* template.

    The Anakin/Padmé four-panel arc: a before-state, the catalyst that
    cracked it open, the middle the person is living now, and where it is
    heading. Each panel is a single sentence (capped at 200 chars).
    """

    before: str = Field(
        ...,
        max_length=200,
        description="One sentence: how things were before. Max ~200 chars.",
    )
    catalyst: str = Field(
        ...,
        max_length=200,
        description="One sentence: the event that changed things. Max ~200 chars.",
    )
    middle: str = Field(
        ...,
        max_length=200,
        description="One sentence: the in-between the person is living now. Max ~200 chars.",
    )
    after: str = Field(
        ...,
        max_length=200,
        description="One sentence: where the trajectory is heading. Max ~200 chars.",
    )


# ---------------------------------------------------------------------------
# Interview state
# ---------------------------------------------------------------------------


class InterviewState(BaseModel):
    """Mutable state carried through a single interview.

    Holds the running signal weights the background classifier nudges as it
    listens, the base-question progress counter, the chosen template once
    the sort lands, the four optional typed results, and the transcript log.

    A fresh ``InterviewState()`` is valid: every field has a default, so a
    new interview starts with all signals at 0.0 and no results.
    """

    # --- Signal weights -----------------------------------------------------
    # The background classifier raises these mid-interview. The leading
    # weight decides which probe the supervisor invokes.
    iceberg_signal: float = Field(
        default=0.0, description="Accumulated evidence for the Iceberg template."
    )
    two_buttons_signal: float = Field(
        default=0.0, description="Accumulated evidence for the Two Buttons template."
    )
    compass_signal: float = Field(
        default=0.0, description="Accumulated evidence for the Compass template."
    )
    arc_signal: float = Field(
        default=0.0, description="Accumulated evidence for the Arc template."
    )

    # --- Progress -----------------------------------------------------------
    base_questions_completed: int = Field(
        default=0,
        ge=0,
        description="How many of the base interview questions are done.",
    )

    # --- Outcome ------------------------------------------------------------
    chosen_template: Optional[str] = Field(
        default=None,
        description="The template the interview sorted into, once decided.",
    )

    # --- Typed results — at most one is populated per interview -------------
    iceberg_result: Optional[IcebergResult] = Field(default=None)
    two_buttons_result: Optional[TwoButtonsResult] = Field(default=None)
    compass_result: Optional[CompassResult] = Field(default=None)
    arc_result: Optional[ArcResult] = Field(default=None)

    # --- Transcript ---------------------------------------------------------
    transcript_log: list = Field(
        default_factory=list,
        description="Ordered record of interview turns.",
    )

    def record_turn(self, speaker: str, text: str) -> None:
        """Append one interview turn to :attr:`transcript_log`.

        ``speaker`` is a free-form label (``"interviewer"`` /
        ``"interviewee"`` in practice); ``text`` is the spoken/transcribed
        content. An empty or whitespace-only ``text`` is a no-op so a stray
        empty transcript event never pads the log.

        Each turn is stored as a plain ``{"speaker", "text"}`` dict — JSON-
        serialisable with no custom encoder, so the incremental transcript
        writer and the offline pipeline both read it directly.
        """
        if not text or not text.strip():
            return
        self.transcript_log.append({"speaker": str(speaker), "text": str(text)})

    def transcript_turns(self) -> list[tuple[str, str]]:
        """Return the transcript as ``(speaker, text)`` pairs for the pipeline.

        :func:`pipeline.classify.wrap_transcript_xml` consumes exactly this
        shape. Entries already in pair form are passed through; dict entries
        (the shape :meth:`record_turn` writes) are converted.
        """
        turns: list[tuple[str, str]] = []
        for entry in self.transcript_log:
            if isinstance(entry, dict):
                turns.append((str(entry.get("speaker", "")), str(entry.get("text", ""))))
            elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                turns.append((str(entry[0]), str(entry[1])))
        return turns

    def signal_weights(self) -> dict[str, float]:
        """Return the four signal weights keyed by short template label."""
        return {
            label: getattr(self, field)
            for field, label in TEMPLATE_SIGNALS.items()
        }

    def leading_template(self) -> Optional[str]:
        """Return the short label of the template with the highest signal.

        Returns ``None`` when every signal is still at its 0.0 default —
        i.e. when there is no evidence to lead on yet. Ties are broken by
        the fixed declaration order in :data:`TEMPLATE_SIGNALS`.
        """
        weights = self.signal_weights()
        best_label, best_weight = max(
            weights.items(), key=lambda item: item[1]
        )
        if best_weight <= 0.0:
            return None
        return best_label
