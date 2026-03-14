"""Packet parsing and CSI conversion."""

import struct
from dataclasses import dataclass

import numpy as np

from .config import DB_EPSILON, N_SUBCARRIERS, REMOVE_SUBCARRIER_INDEXES, BANDWIDTH_MHZ


@dataclass
class ProcessedCSI:
    """Processed CSI data suitable for IPC between receiver and workers."""

    csi: np.ndarray  # complex, shape (64,)
    csi_wo_np: np.ndarray # csi without nulls and pilot subcarriers
    rssi: int
    seq: int
    magnitude_db: np.ndarray
    magnitude_db_wo_np: np.ndarray # magnitude_db without nulls and pilot subcarriers
    phase_rad: np.ndarray
    timestamp: float
    label: int | None


def read_binary(data: bytes) -> dict:
    HEADER_FMT = "<HbB6sHHHH"
    HEADER_SIZE = struct.calcsize(HEADER_FMT)

    if len(data) < HEADER_SIZE:
        raise ValueError(
            f"Packet too short: {len(data)} bytes (need at least {HEADER_SIZE})"
        )

    magic, rssi, fctl, mac_raw, seq, css, csp, cvr = struct.unpack_from(
        HEADER_FMT, data, 0
    )

    if magic not in [0x1111, 0x1112, 0x1113]:
        raise ValueError(f"Invalid magic value: 0x{magic:04x}, expected 0x1111, 0x1112, 0x1113")

    remaining = len(data) - HEADER_SIZE
    if remaining != (N_SUBCARRIERS * 4):
        raise ValueError(
            f"Invalid CSI payload length: {remaining} bytes, expected {N_SUBCARRIERS * 4}\n"
            f"  {seq:<8} {rssi:<8} {remaining:<10} {fctl:<#6x} "
            f"{':'.join(f'{b:02x}' for b in mac_raw)}"
        )

    csi_len = remaining // 2
    csi = np.frombuffer(data, dtype="<i2", count=csi_len, offset=HEADER_SIZE).copy()

    return {
        "magic": magic,
        "rssi": rssi,
        "fctl": fctl,
        "mac": ":".join(f"{b:02x}" for b in mac_raw),
        "seq": seq,
        "css": css,
        "csp": csp,
        "cvr": cvr,
        "csi_len": csi_len,
        "csi": csi,
        "label": 0 if magic == 0x1112 else 1 if magic == 0x1113 else None,
    }


def csi_to_magnitude_db_and_phase(
    csi: np.ndarray,
    rssi: int,
    include_phase: bool = True,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray | None]:
    csi_row = np.asarray(csi).reshape(-1)
    csi_mag = np.abs(csi_row).astype(np.float32)

    if csi_mag.size != N_SUBCARRIERS:
        raise ValueError(
            f"Unexpected CSI length: {csi_mag.size}, expected {N_SUBCARRIERS}"
        )

    rssi_linear = np.power(10.0, rssi / 10.0)
    total_mag_sq = np.sum(csi_mag**2)

    if total_mag_sq > 0.0:
        scaling_factor = np.sqrt(rssi_linear / total_mag_sq)
        csi_mag *= scaling_factor
    else:
        csi_mag.fill(0.0)

    magnitude_row = csi_mag.copy()
    db_row = 20.0 * np.log10(csi_mag + DB_EPSILON)
    db_row_wo_np = np.delete(db_row.copy(), REMOVE_SUBCARRIER_INDEXES[BANDWIDTH_MHZ])
    phase_deg_row = (
        np.angle(csi_row, deg=True).astype(np.float32) if include_phase else None
    )
    return magnitude_row, db_row, db_row_wo_np, phase_deg_row
