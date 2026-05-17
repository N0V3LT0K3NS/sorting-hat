"""Probe sub-interviews — one focused ``AgentTask`` per locked template.

After the five base questions, the supervisor (G8) reads the accumulated
signal weights and invokes exactly one probe. Each probe is a short
sub-interview that deepens into its template and returns the matching typed
result from :mod:`agent.state`.

One probe per locked template: Iceberg (depth), Two Buttons (tension),
Compass (position), Arc (trajectory).
"""

from agent.probes.iceberg import IcebergProbeTask
from agent.probes.two_buttons import TwoButtonsProbeTask

__all__ = ["IcebergProbeTask", "TwoButtonsProbeTask"]
