"""Packet processing: raw bytes -> ProcessedCSI."""

import time
from typing import Optional

import numpy as np

from ..config import DEVICE, BANDWIDTH_MHZ
from ..protocol import (
    ProcessedCSI,
    csi_to_magnitude_db_and_phase,
    read_binary,
)


def _get_decoder():
    """Thread-local decoder instance."""
    import threading

    if not hasattr(_get_decoder, "_local"):
        _get_decoder._local = threading.local()
    local = _get_decoder._local
    if not hasattr(local, "decoder"):
        from nexcsi import decoder

        local.decoder = decoder(DEVICE)
    return local.decoder


def process_one_packet(data: bytes) -> Optional[ProcessedCSI]:
    """
    Parse and decode one raw packet into ProcessedCSI.
    Returns None on parse/validation error (caller may log and continue).
    """
    try:
        parsed = read_binary(data)
        # print("Processing packet: ", parsed["seq"])
    except ValueError:
        # print("ValueError: ", data)
        return None

    decoder_obj = _get_decoder()
    csi_raw = parsed["csi"]
    csi_complex = decoder_obj.unpack(
        csi_raw, zero_nulls=True, zero_pilots=True
    )
    if BANDWIDTH_MHZ == 20:
        csi_complex[..., 60] = 0

    csi_flat = np.asarray(csi_complex).reshape(-1)

    try:
        magnitude_row, db_row, db_row_wo_np, phase_deg_row = csi_to_magnitude_db_and_phase(
            csi_flat, parsed["rssi"]
        )
    except ValueError:
        return None

    phase_rad = np.deg2rad(phase_deg_row).astype(np.float32)

    return ProcessedCSI(
        csi=csi_flat.copy(),
        rssi=parsed["rssi"],
        seq=parsed["seq"],
        magnitude_db=db_row.copy(),
        magnitude_db_wo_np=db_row_wo_np.copy(),
        phase_rad=phase_rad,
        timestamp=time.perf_counter(),
    )
