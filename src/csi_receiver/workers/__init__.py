"""Consumer workers for processed CSI data."""

from .base import BaseWorker, SENTINEL
from .presence_worker import PresenceWorker
from .viz_worker import VizWorker

# Registry: add new worker classes here
WORKERS = [VizWorker, PresenceWorker]

__all__ = ["BaseWorker", "VizWorker", "PresenceWorker", "WORKERS", "SENTINEL"]
