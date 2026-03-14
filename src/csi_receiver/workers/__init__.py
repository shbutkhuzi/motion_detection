"""Consumer workers for processed CSI data."""

from .base import BaseWorker, SENTINEL
from .presence_worker import PresenceWorker
from .stat_presence_worker import StatPresenceWorker
from .viz_worker import VizWorker

# Registry: add new worker classes here
WORKERS = [VizWorker, 
            PresenceWorker, 
            StatPresenceWorker]

__all__ = ["BaseWorker", "VizWorker", 
           "PresenceWorker", 
           "StatPresenceWorker", 
           "RunningMetrics",
           "WORKERS", "SENTINEL"]
