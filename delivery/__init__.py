"""The local kiosk delivery server.

A small stdlib-only web server that bridges the offline pipeline and the
kiosk browser: it serves finished portraits out of ``SESSIONS_DIR`` and
exposes a pipeline-progress status endpoint the kiosk polls to show a
stage-by-stage reveal. See :mod:`delivery.server`.
"""

from delivery.server import (
    DEFAULT_PORT,
    DeliveryHandler,
    build_server,
    read_status,
    run,
)

__all__ = [
    "DEFAULT_PORT",
    "DeliveryHandler",
    "build_server",
    "read_status",
    "run",
]
