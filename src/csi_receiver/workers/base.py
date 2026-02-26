"""Base worker interface and registry."""

import multiprocessing as mp
from abc import ABC, abstractmethod

from ..protocol import ProcessedCSI

# Sentinel: receive this to exit the worker loop
SENTINEL = None


class BaseWorker(ABC):
    """Base class for CSI consumer workers."""

    def __init__(self, input_queue: mp.Queue):
        self._input_queue = input_queue

    def get_input_queue(self) -> mp.Queue:
        return self._input_queue

    def run(self) -> None:
        """Main loop: get from queue, process. Exits on SENTINEL."""
        while True:
            try:
                data = self._input_queue.get()
            except (BrokenPipeError, OSError, ConnectionError):
                break
            if data is SENTINEL:
                break
            self.process(data)

    @abstractmethod
    def process(self, data: ProcessedCSI) -> None:
        """Process one CSI sample. Override in subclasses."""
        pass


