"""Replay harness integration smoke test."""

from __future__ import annotations

import asyncio
from pathlib import Path

from scripts.replay_interview import replay_transcript

_FIXTURE_TRANSCRIPT_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "replay_sample_transcript.json"
)


def test_replay_harness_advances_diagnostic_machine() -> None:
    """Stub replay should complete base questions, route, and land a result."""
    report = asyncio.run(
        replay_transcript(_FIXTURE_TRANSCRIPT_PATH, stub_classifier=True)
    )

    assert report.total_turns_replayed > 0
    assert report.base_questions_completed == 5
    assert report.base_questions_done is True
    assert report.chosen_template is not None
    assert report.probe_result_landed is True
    assert report.routing_done is True
