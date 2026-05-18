#!/usr/bin/env python
"""Replay a saved interview transcript through the live diagnostic wiring.

This is a local smoke-test harness for the during-interview machinery:
background turn classification, base-question progress, and supervisor probe
routing. It uses no LiveKit room, no microphone, and no audio stack.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.classifier import API_KEY_ENV, SIGNAL_NAMES
from agent.interviewer import (
    INTERVIEWEE_ROLE,
    INTERVIEWER_ROLE,
    TEMPLATE_ORDER,
    InterviewerAgent,
    ProbeOutcome,
)
from agent.state import (
    ArcResult,
    CompassResult,
    IcebergResult,
    InterviewState,
    TwoButtonsResult,
)

DEFAULT_TRANSCRIPT_PATH = ROOT / "sessions" / "interview-26a2ba" / "transcript.json"


@dataclass(frozen=True)
class ReplayReport:
    """Summary of one transcript replay."""

    transcript_path: Path
    classifier_mode: str
    total_turns_replayed: int
    interviewee_turns: int
    interviewer_turns: int
    signal_weights: dict[str, float]
    base_questions_completed: int
    base_questions_done: bool
    chosen_template: str | None
    probe_result_landed: bool
    routing_done: bool
    probes_attempted: tuple[str, ...]
    routing_outcome_template: str | None
    warnings: tuple[str, ...] = ()


async def stub_classify_turn(user_response: str) -> dict[str, float]:
    """Deterministic local classifier used for offline and CI replay.

    It is intentionally simple: keyword hints nudge the same four signal
    weights that the real classifier returns. The harness still exercises
    InterviewerAgent.on_user_turn(), its task callback, and apply_scores().
    """
    text = user_response.lower()
    keyword_groups = {
        "iceberg": (
            "known",
            "notice",
            "realize",
            "don't realize",
            "beneath",
            "under",
            "private",
            "family",
            "accent",
            "defend",
        ),
        "two_buttons": (
            "tension",
            "contradiction",
            "pull",
            "but",
            "either",
            "or",
            "both",
            "choice",
            "choose",
        ),
        "compass": (
            "optimize",
            "rank",
            "highest",
            "value",
            "stand",
            "direction",
            "right now",
            "important",
            "priority",
        ),
        "arc": (
            "changed",
            "change",
            "realization",
            "realized",
            "before",
            "after",
            "used to",
            "now",
            "happened",
        ),
    }

    scores: dict[str, float] = {}
    for signal in SIGNAL_NAMES:
        hits = sum(1 for keyword in keyword_groups[signal] if keyword in text)
        scores[signal] = min(1.0, 0.05 + (0.18 * hits)) if hits else 0.0
    return scores


def _iceberg_result() -> IcebergResult:
    return IcebergResult(
        surface="The instantly recognizable detail people attach to first.",
        first_layer="A local identity that asks to be defended, not flattened.",
        second_layer="Family and place sit underneath the public shorthand.",
        abyss="Being misread feels like losing the people carried in the voice.",
    )


def _two_buttons_result() -> TwoButtonsResult:
    return TwoButtonsResult(
        button_a_label="Keep representing home",
        button_a_seduction="It honors the family and place that shaped the voice.",
        button_b_label="Let people misread",
        button_b_seduction="It would be easier to stop correcting every assumption.",
        impossibility="Correcting people protects identity, but it also keeps reopening the same friction.",
    )


def _compass_result() -> CompassResult:
    return CompassResult(
        axis_1_poles=("blend in", "represent home"),
        axis_1_position=0.72,
        axis_2_poles=("let it pass", "correct the record"),
        axis_2_position=0.58,
        why_these_axes="The transcript keeps returning to recognition, origin, and what is worth correcting.",
    )


def _arc_result() -> ArcResult:
    return ArcResult(
        before="At first the recognizable trait was just something people noticed.",
        catalyst="Repeated misreadings turned it into something that needed defending.",
        middle="The person now treats the trait as family and place made audible.",
        after="The likely arc is owning the signal without needing every stranger to understand it.",
    )


_RESULT_BUILDERS = {
    "iceberg": _iceberg_result,
    "two_buttons": _two_buttons_result,
    "compass": _compass_result,
    "arc": _arc_result,
}


def _make_no_audio_probe_runners(log: list[str]):
    """Return probe runners that complete locally with valid typed results."""

    def _make_runner(template: str):
        async def _runner() -> ProbeOutcome:
            log.append(template)
            return ProbeOutcome(
                template=template,
                result=_RESULT_BUILDERS[template](),
                thin=False,
            )

        return _runner

    return {template: _make_runner(template) for template in TEMPLATE_ORDER}


def _load_transcript(path: Path) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON list of transcript turns")

    transcript: list[dict[str, str]] = []
    for idx, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"turn {idx} must be an object")
        speaker = _normalize_speaker(str(item.get("speaker", "")))
        text = str(item.get("text", ""))
        if speaker not in {INTERVIEWEE_ROLE, INTERVIEWER_ROLE}:
            raise ValueError(
                f"turn {idx} has unsupported speaker {item.get('speaker')!r}"
            )
        transcript.append({"speaker": speaker, "text": text})
    return transcript


def _normalize_speaker(speaker: str) -> str:
    normalized = speaker.strip().lower()
    if normalized == "user":
        return INTERVIEWEE_ROLE
    if normalized == "assistant":
        return INTERVIEWER_ROLE
    return normalized


async def _drain_classifier_task(task: asyncio.Task | None) -> None:
    if task is None:
        return
    await task
    # Let InterviewerAgent._on_classifier_done run and absorb the result.
    await asyncio.sleep(0)


async def replay_transcript(
    transcript_path: Path = DEFAULT_TRANSCRIPT_PATH,
    *,
    stub_classifier: bool = False,
    print_report: bool = False,
) -> ReplayReport:
    """Replay one transcript through InterviewerAgent's diagnostic path."""
    warnings: list[str] = []
    use_stub = stub_classifier
    if not use_stub and not os.environ.get(API_KEY_ENV):
        warnings.append(
            f"WARNING: {API_KEY_ENV} is not set; falling back to --stub-classifier."
        )
        use_stub = True

    transcript = _load_transcript(transcript_path)
    state = InterviewState()
    probe_log: list[str] = []
    agent = InterviewerAgent(
        state=state,
        probe_runners=_make_no_audio_probe_runners(probe_log),
        classifier=stub_classify_turn if use_stub else None,
    )

    for turn in transcript:
        speaker = turn["speaker"]
        text = turn["text"]
        if speaker == INTERVIEWEE_ROLE:
            task = agent.on_user_turn(text)
            agent.record_turn(speaker, text)
            agent.advance_base_questions_from_recorded_turns()
            await _drain_classifier_task(task)
        else:
            agent.record_turn(speaker, text)
            agent.advance_base_questions_from_recorded_turns()

    for task in agent.classifier_tasks:
        await _drain_classifier_task(task)

    outcome = None
    if agent.base_questions_done:
        outcome = await agent.route_to_probe()

    turns = state.transcript_turns()
    report = ReplayReport(
        transcript_path=transcript_path,
        classifier_mode="stub" if use_stub else "live",
        total_turns_replayed=len(turns),
        interviewee_turns=sum(1 for speaker, _ in turns if speaker == INTERVIEWEE_ROLE),
        interviewer_turns=sum(1 for speaker, _ in turns if speaker == INTERVIEWER_ROLE),
        signal_weights=state.signal_weights(),
        base_questions_completed=state.base_questions_completed,
        base_questions_done=agent.base_questions_done,
        chosen_template=state.chosen_template,
        probe_result_landed=_probe_result_landed(state),
        routing_done=agent.routing_done,
        probes_attempted=agent.probes_attempted,
        routing_outcome_template=outcome.template if outcome is not None else None,
        warnings=tuple(warnings),
    )

    if print_report:
        print(format_report(report))
    return report


def _probe_result_landed(state: InterviewState) -> bool:
    return any(
        getattr(state, field) is not None
        for field in (
            "iceberg_result",
            "two_buttons_result",
            "compass_result",
            "arc_result",
        )
    )


def format_report(report: ReplayReport) -> str:
    weights = "\n".join(
        f"  {name}: {report.signal_weights[name]:.3f}" for name in SIGNAL_NAMES
    )
    warnings = "\n".join(report.warnings)
    if warnings:
        warnings += "\n\n"
    probes = ", ".join(report.probes_attempted) or "(none)"
    return (
        f"{warnings}"
        "=== sorting-hat replay report ===\n"
        f"transcript: {report.transcript_path}\n"
        f"classifier_mode: {report.classifier_mode}\n"
        f"total_turns_replayed: {report.total_turns_replayed}\n"
        f"interviewee_turns: {report.interviewee_turns}\n"
        f"interviewer_turns: {report.interviewer_turns}\n"
        "signal_weights:\n"
        f"{weights}\n"
        f"base_questions_completed: {report.base_questions_completed}\n"
        f"base_questions_done: {report.base_questions_done}\n"
        f"chosen_template: {report.chosen_template}\n"
        f"routing_outcome_template: {report.routing_outcome_template}\n"
        f"probe_result_landed: {report.probe_result_landed}\n"
        f"probes_attempted: {probes}\n"
        f"routing_done: {report.routing_done}\n"
        "=================================="
    )


async def _amain(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay a saved transcript through the sorting-hat diagnostic wiring."
    )
    parser.add_argument(
        "transcript",
        nargs="?",
        type=Path,
        default=DEFAULT_TRANSCRIPT_PATH,
        help=f"Path to transcript.json (default: {DEFAULT_TRANSCRIPT_PATH})",
    )
    classifier = parser.add_mutually_exclusive_group()
    classifier.add_argument(
        "--stub-classifier",
        action="store_true",
        help="Use a deterministic local classifier; no API key or network needed.",
    )
    classifier.add_argument(
        "--live-classifier",
        action="store_true",
        help="Use the real OpenRouter classifier when OPENROUTER_API_KEY is set.",
    )
    args = parser.parse_args(argv)

    report = await replay_transcript(
        args.transcript,
        stub_classifier=args.stub_classifier,
        print_report=True,
    )
    if not (
        report.base_questions_done
        and report.chosen_template
        and report.probe_result_landed
        and report.routing_done
    ):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    raise SystemExit(main())
