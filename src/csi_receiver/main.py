"""Orchestrator: spawn receiver and worker processes."""

import multiprocessing as mp
import os
import signal
import sys

from .receiver import ReceiverWorker
from .workers import WORKERS, SENTINEL


def _run_worker(worker_cls, queue: mp.Queue) -> None:
    """Entry point for worker process."""
    sys.stdout = os.fdopen(sys.stdout.fileno(), "w", buffering=1, closefd=False)
    sys.stderr = os.fdopen(sys.stderr.fileno(), "w", buffering=1, closefd=False)
    worker = worker_cls(queue)
    worker.run()


def main() -> None:
    mp.freeze_support()
    sys.stdout = os.fdopen(sys.stdout.fileno(), "w", buffering=1, closefd=False)
    sys.stderr = os.fdopen(sys.stderr.fileno(), "w", buffering=1, closefd=False)
    print("Starting CSI Receiver...")

    # One queue per worker
    queues = [mp.Queue(maxsize=256) for _ in WORKERS]

    # Receiver fans out to all queues
    receiver = ReceiverWorker(consumer_queues=queues)
    receiver.start()

    # Spawn worker processes
    processes = []
    for worker_cls, queue in zip(WORKERS, queues):
        p = mp.Process(
            target=_run_worker,
            args=(worker_cls, queue),
            name=worker_cls.__name__,
            daemon=True,
        )
        p.start()
        processes.append(p)

    shutdown_in_progress = False

    def shutdown(*args):
        nonlocal shutdown_in_progress
        if shutdown_in_progress:
            print("\nForce exit.")
            os._exit(1)
        shutdown_in_progress = True
        print("\nShutting down...")
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        receiver.stop()
        for p in processes:
            p.join(timeout=1.0)
            if p.is_alive():
                p.terminate()
                p.join(timeout=0.5)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
