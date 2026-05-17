"""Probe sub-interviews — one focused ``AgentTask`` per locked template.

After the five base questions, the supervisor (G8) reads the accumulated
signal weights and invokes exactly one probe. Each probe is a short
sub-interview that deepens into its template and returns the matching typed
result from :mod:`agent.state`.

G4 ships the Iceberg probe — the *depth* shape. G5–G7 add Two Buttons,
Compass, and Arc alongside it.
"""

from agent.probes.iceberg import IcebergProbeTask

__all__ = ["IcebergProbeTask"]
