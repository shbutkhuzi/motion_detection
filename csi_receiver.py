import socket
import struct
import time
import numpy as np
from nexcsi import decoder

LISTEN_PORT = 5500
SOURCE_IP = "192.168.137.2"
DEVICE = "raspberrypi"
BANDWIDTH_MHZ = 20   # Don't change this, code is not currently designed to handle multiple bandwidths
BAND_TO_NSUB = {
    20: 64,
    40: 128,
    80: 256,
}
N_SUBCARRIERS = BAND_TO_NSUB[BANDWIDTH_MHZ]
WATERFALL_SIZE = 256
DB_EPSILON = 1e-12
DB_MIN = -120.0
DB_MAX = -30.0


def read_binary(data: bytes) -> dict:

    HEADER_FMT = "<HbB6sHHHH"
    HEADER_SIZE = struct.calcsize(HEADER_FMT)

    if len(data) < HEADER_SIZE:
        print(f"Packet too short: {len(data)} bytes (need at least {HEADER_SIZE})")
        raise ValueError("Packet too short to contain header")

    magic, rssi, fctl, mac_raw, seq, css, csp, cvr = struct.unpack_from(HEADER_FMT, data, 0)

    if magic != 0x1111:
        raise ValueError(f"Invalid magic value: 0x{magic:04x}, expected 0x1111")

    remaining = len(data) - HEADER_SIZE
    if remaining != (N_SUBCARRIERS * 4):
        raise ValueError(
            f"Invalid CSI payload length: {remaining} bytes, expected {N_SUBCARRIERS * 4}\n"
            f"  {seq:<8} {rssi:<8} {remaining:<10} {fctl:<#6x} {':'.join(f'{b:02x}' for b in mac_raw)}"
        )

    csi_len = remaining // 2
    csi = np.frombuffer(data, dtype="<i2", count=csi_len, offset=HEADER_SIZE).copy()

    parsed = {
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
    }

    return parsed


def csi_to_magnitude_db_and_phase(
    csi: np.ndarray,
    rssi: int,
    include_phase: bool = True,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    csi_row = np.asarray(csi).reshape(-1)
    csi_mag = np.abs(csi_row).astype(np.float32)

    if csi_mag.size != N_SUBCARRIERS:
        raise ValueError(f"Unexpected CSI length: {csi_mag.size}, expected {N_SUBCARRIERS}")

    rssi_linear = np.power(10.0, rssi / 10.0)
    total_mag_sq = np.sum(csi_mag ** 2)

    if total_mag_sq > 0.0:
        scaling_factor = np.sqrt(rssi_linear / total_mag_sq)
        csi_mag *= scaling_factor
    else:
        csi_mag.fill(0.0)

    magnitude_row = csi_mag.copy()
    db_row = 20.0 * np.log10(csi_mag + DB_EPSILON)
    phase_deg_row = np.angle(csi_row, deg=True).astype(np.float32) if include_phase else None
    return magnitude_row, db_row, phase_deg_row


_presence_last_time = None
_csi_history = None
_rssi_history = None
def run_presence_detection(csi: np.ndarray, rssi: int, window_size: int = 10, ms_diff_tolerance: float = 200):
    global _presence_last_time, _csi_history, _rssi_history

    now = time.perf_counter()
    csi = csi.copy()

    remove_indices = [0, 1, 2, 3, 11, 25, 32, 39, 53, 60, 61, 62, 63]
    csi_no_nulls = np.delete(csi, remove_indices, axis=1)

    csi_mag = 20.0 * np.log10(
        np.maximum(np.abs(csi_no_nulls).astype(np.float32), np.finfo(np.float32).eps)
    )

    if _presence_last_time is None or (now - _presence_last_time) > ms_diff_tolerance / 1000.0:
        _csi_history = csi_mag.copy()
        _rssi_history = [rssi]
    else:
        if _csi_history is None:
            _csi_history = csi_mag.copy()
            _rssi_history = [rssi]
        else:
            _csi_history = np.concatenate([_csi_history, csi_mag], axis=0)
            _rssi_history = np.concatenate([_rssi_history, [rssi]], axis=0)
        if _csi_history.shape[0] > window_size:
            _csi_history = _csi_history[-window_size:, :]
            _rssi_history = _rssi_history[-window_size:]
           
            print("Average RSSI: ", np.var(_rssi_history))
            var = np.var(_csi_history, axis=0)
            print("Average CSI Variance: ", np.mean(var))

    _presence_last_time = now
    return _csi_history








def run_receiver():
    print("Starting CSI Receiver...")

    sock_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_recv.bind(('0.0.0.0', LISTEN_PORT))

    print(f"Listening on port {LISTEN_PORT} for packets from {SOURCE_IP}...")
    decoder_obj = decoder(DEVICE)

    try:
        while True:

            data, addr = sock_recv.recvfrom(4096)

            if addr[0] != SOURCE_IP:
                continue

            try:
                parsed = read_binary(data)

                csi = decoder_obj.unpack(parsed['csi'], zero_nulls=False, zero_pilots=False)
                print(csi)

                magnitude_row, db_row, phase_row = csi_to_magnitude_db_and_phase(
                    csi,
                    parsed['rssi']
                )

            except ValueError as e:
                print(e)
                continue

    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Error: {e}")
    finally:
        sock_recv.close()


if __name__ == "__main__":

    run_receiver()
