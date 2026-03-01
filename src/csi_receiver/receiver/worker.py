"""Receiver worker: recv thread + processing pool, fan-out to consumer queues."""

import queue
import sys
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import List

from ..config import LISTEN_PORT, SOURCE_IP
from ..protocol import ProcessedCSI
from .processor import process_one_packet

SENTINEL = None  # Used to signal shutdown to consumers


class ReceiverWorker:
    """Receives UDP packets, processes them, and fans out to consumer queues."""

    def __init__(
        self,
        consumer_queues: List[queue.Queue],
        num_workers: int = 2,
        raw_queue_maxsize: int = 1024,
    ):
        self.consumer_queues = consumer_queues
        self.num_workers = num_workers
        self.raw_queue: queue.Queue = queue.Queue(maxsize=raw_queue_maxsize)
        self._stop = threading.Event()
        self._recv_thread: threading.Thread | None = None
        self._pool: ThreadPoolExecutor | None = None

    def _recv_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.5)
        try:
            sock.bind(("0.0.0.0", LISTEN_PORT))
        except OSError as e:
            print(f"Failed to bind to port {LISTEN_PORT}: {e}")
            return

        print(f"Listening on port {LISTEN_PORT} for packets from {SOURCE_IP}...")

        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            if addr[0] != SOURCE_IP:
                continue

            try:
                self.raw_queue.put_nowait(data)
            except queue.Full:
                # Drop oldest to make room for newest
                try:
                    self.raw_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.raw_queue.put_nowait(data)
                except queue.Full:
                    pass

        sock.close()

    def _process_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data = self.raw_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            processed = process_one_packet(data)
            if processed is None:
                continue

            for q in self.consumer_queues:
                try:
                    q.put_nowait(processed)
                except (queue.Full, BrokenPipeError, OSError):
                    pass

    def _worker_task(self) -> None:
        self._process_loop()

    def start(self) -> None:
        self._stop.clear()
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=False)
        self._recv_thread.start()
        self._pool = ThreadPoolExecutor(max_workers=self.num_workers)
        for _ in range(self.num_workers):
            self._pool.submit(self._worker_task)

    def stop(self) -> None:
        self._stop.set()

        # Drain raw queue first so processing threads exit quickly
        while True:
            try:
                self.raw_queue.get_nowait()
            except queue.Empty:
                break

        if self._recv_thread is not None:
            self._recv_thread.join(timeout=1.0)
        if self._pool is not None:
            kwargs = {"wait": True}
            if sys.version_info >= (3, 9):
                kwargs["cancel_futures"] = True
            self._pool.shutdown(**kwargs)

        for q in self.consumer_queues:
            try:
                q.put(SENTINEL, timeout=0.5)
            except (BrokenPipeError, OSError, queue.Full):
                pass
