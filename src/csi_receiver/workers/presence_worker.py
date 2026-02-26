"""Presence detection worker (stub for future implementation)."""

import multiprocessing as mp

from ..protocol import ProcessedCSI
from .base import BaseWorker


class PresenceWorker(BaseWorker):
    """Placeholder for presence detection. Process CSI data to detect presence."""

    def process(self, data: ProcessedCSI) -> None:
        """Process CSI for presence detection. To be implemented."""
        pass
