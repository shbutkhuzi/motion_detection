import argparse
import os
import struct
import socket
import threading
import tkinter as tk
from tkinter import font as tkfont
import signal
import sys
import time
from datetime import datetime

ETH_HDR = 14
IP_HDR = 20
UDP_HDR = 8
FRAME_OFFSET = ETH_HDR + IP_HDR + UDP_HDR

LISTEN_PORT = 5500
SOURCE_IP = "192.168.137.2"
DEST_IP = "192.168.137.1"  # Receiver (this machine)
SRC_MAC = bytes.fromhex("020000000001")   # Placeholder source MAC
DST_MAC = bytes.fromhex("020000000002")   # Placeholder dest MAC
HEADER_FMT = "<HbB6sHHHH"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

# Global state
label = 1  # 1 -> magic 0x1113, 0 -> magic 0x1112
pcap_file = None
running = True
listener_thread = None
root = None
button = None
label_lock = threading.Lock()


def get_magic_from_label():
    with label_lock:
        return 0x1113 if label == 1 else 0x1112


def modify_packet_magic(data: bytes) -> bytes:
    """Replace magic bytes in packet based on current label."""
    if len(data) < 2:
        return data
    magic = get_magic_from_label()
    return struct.pack("<H", magic) + data[2:]


def toggle_label():
    global label
    with label_lock:
        label = 0 if label else 1
    update_button_display()


def update_button_display():
    if root and button:
        with label_lock:
            lbl = label
        color = "blue" if lbl == 1 else "grey"
        text = f"Label: {lbl}"
        root.after(0, lambda c=color, t=text: _do_update_button(c, t))


def _do_update_button(color, text):
    if button:
        fg = "white" if color == "blue" else "black"
        button.configure(bg=color, fg=fg, activebackground=color, activeforeground=fg, text=text)


def read_binary(data: bytes):
    if len(data) < HEADER_SIZE:
        raise ValueError(
            f"Packet too short: {len(data)} bytes (need at least {HEADER_SIZE})"
        )

    magic, rssi, fctl, mac_raw, seq, css, csp, cvr = struct.unpack_from(
        HEADER_FMT, data, 0
    )

    if magic != 0x1111:
        raise ValueError(f"Invalid magic value: 0x{magic:04x}, expected 0x1111")


def _ip_checksum(header: bytes) -> int:
    """Compute IPv4 header checksum."""
    total = 0
    for i in range(0, len(header), 2):
        total += (header[i] << 8) | header[i + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def build_udp_frame(payload: bytes, src_ip: str, src_port: int, dst_ip: str, dst_port: int) -> bytes:
    """Build full Ethernet + IPv4 + UDP + payload frame."""
    src_ip_b = socket.inet_aton(src_ip)
    dst_ip_b = socket.inet_aton(dst_ip)

    udp_len = 8 + len(payload)
    ip_total_len = 20 + udp_len

    # Ethernet header (14 bytes)
    eth = DST_MAC + SRC_MAC + struct.pack(">H", 0x0800)

    # IPv4 header (20 bytes, checksum = 0 for calculation)
    ip_header = (
        struct.pack(">BBHHHBBH", 0x45, 0, ip_total_len, 0, 0, 64, 17, 0)
        + src_ip_b
        + dst_ip_b
    )
    checksum = _ip_checksum(ip_header)
    ip_header = (
        struct.pack(">BBHHHBBH", 0x45, 0, ip_total_len, 0, 0, 64, 17, checksum)
        + src_ip_b
        + dst_ip_b
    )

    # UDP header (8 bytes, checksum 0)
    udp_header = struct.pack(
        ">HHHH", src_port, dst_port, udp_len, 0
    )

    return eth + ip_header + udp_header + payload


def write_pcap_header(f):
    """Write pcap global header (24 bytes)."""
    f.write(struct.pack(
        "<IHHiIII",
        0xA1B2C3D4,  # magic
        2,           # version_major
        4,           # version_minor
        0,           # thiszone
        0,           # sigfigs
        65535,       # snaplen
        1,           # network (Ethernet)
    ))


def write_pcap_packet(f, data: bytes):
    """Append a packet to the pcap file."""
    now = time.time()
    ts_sec = int(now)
    ts_usec = int((now - ts_sec) * 1_000_000)
    incl_len = len(data)
    orig_len = incl_len
    f.write(struct.pack("<IIII", ts_sec, ts_usec, incl_len, orig_len))
    f.write(data)
    f.flush()


def expand_file_args(args: list[str]) -> list[str]:
    """Expand file args: paths ending with /* are replaced with all files in that directory."""
    result = []
    for arg in args:
        if arg.endswith("/*"):
            dirpath = arg[:-2]
            if not os.path.isdir(dirpath):
                print(f"Warning: not a directory: {dirpath}", file=sys.stderr)
                continue
            for name in sorted(os.listdir(dirpath)):
                path = os.path.join(dirpath, name)
                if os.path.isfile(path):
                    result.append(path)
        else:
            result.append(arg)
    return result


def parse_udp_from_frame(frame: bytes) -> tuple[int, int, str, bytes]:
    """Extract src_port, dst_port, dst_ip, payload from Ethernet+IPv4+UDP frame."""
    if len(frame) < FRAME_OFFSET:
        raise ValueError(f"Frame too short: {len(frame)} bytes")
    src_port, dst_port = struct.unpack_from(">HH", frame, ETH_HDR + IP_HDR)
    dst_ip = socket.inet_ntoa(frame[ETH_HDR + 16:ETH_HDR + 20])
    payload = frame[FRAME_OFFSET:]
    return src_port, dst_port, dst_ip, payload


def replay_pcap(path: str) -> None:
    """Replay pcap file: send UDP packets to original destination IP with timing and ports."""
    def on_sigint_replay(signum, frame):
        print("\nReplay interrupted by user")
        sys.exit(0)

    signal.signal(signal.SIGINT, on_sigint_replay)

    with open(path, "rb") as f:
        # Skip pcap global header (24 bytes)
        f.read(24)
        last_ts = None
        sock = None
        while True:
            hdr = f.read(16)
            if len(hdr) < 16:
                break
            ts_sec, ts_usec, incl_len, _ = struct.unpack("<IIII", hdr)
            ts = ts_sec + ts_usec / 1_000_000
            frame = f.read(incl_len)
            if len(frame) < incl_len:
                break
            try:
                src_port, dst_port, dst_ip, payload = parse_udp_from_frame(frame)
            except ValueError:
                continue
            if sock is None:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind(("", 5501))
                except OSError:
                    sock.bind(("", 0))
            if last_ts is not None:
                delay = ts - last_ts
                if delay > 0:
                    time.sleep(delay)
            last_ts = ts
            sock.sendto(payload, ("127.0.0.1", dst_port))
        if sock:
            sock.close()
        print(f"Replay complete: {path}")


def udp_listener():
    global pcap_file, running
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", LISTEN_PORT))

    pcap_path = f"csi_recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pcap"
    pcap_file = open(pcap_path, "wb")
    write_pcap_header(pcap_file)

    try:
        while running:
            sock.settimeout(0.5)
            try:
                data, addr = sock.recvfrom(65535)
                if addr[0] != SOURCE_IP:
                    continue
                modified = modify_packet_magic(data)
                frame = build_udp_frame(
                    modified, addr[0], addr[1], DEST_IP, LISTEN_PORT
                )
                write_pcap_packet(pcap_file, frame)
            except socket.timeout:
                continue
    except Exception as e:
        if running:
            print(f"Listener error: {e}", file=sys.stderr)
    finally:
        sock.close()
        if pcap_file:
            pcap_file.close()
            pcap_file = None
            print(f"Saved to {pcap_path}")


def shutdown():
    global running, root, listener_thread
    running = False
    if listener_thread and listener_thread.is_alive():
        listener_thread.join(timeout=2.0)
    if root:
        try:
            root.quit()
            root.destroy()
        except Exception:
            pass
    sys.exit(0)


def on_sigint(signum, frame):
    global running
    running = False
    if root:
        root.quit()  # Break mainloop so main()'s finally runs shutdown()
    else:
        shutdown()


def create_gui():
    global root, button

    root = tk.Tk()
    root.title("CSI Label Toggle")
    root.geometry("400x300")
    root.resizable(True, True)

    def on_toggle():
        toggle_label()

    large_font = tkfont.Font(size=24, weight="bold")
    button = tk.Button(
        root,
        text="Label: 1",
        font=large_font,
        bg="blue",
        fg="white",
        activebackground="blue",
        activeforeground="white",
        cursor="hand2",
        relief="raised",
        bd=4,
        command=on_toggle,
    )
    button.bind("<Button-3>", lambda e: shutdown())  # Right-click to exit
    button.pack(expand=True, fill="both", padx=40, pady=40)

    root.protocol("WM_DELETE_WINDOW", shutdown)

    return root


def main():
    global listener_thread
    parser = argparse.ArgumentParser(description="CSI recorder and pcap replay")
    parser.add_argument(
        "--file", "-f", nargs="+", metavar="FILE",
        help="Replay pcap file(s); use path/* to replay all files in directory"
    )
    args = parser.parse_args()

    if args.file:
        files = expand_file_args(args.file)
        if not files:
            print("No files to replay", file=sys.stderr)
            return
        print(f"Replaying {len(files)} file(s) sequentially:")
        for f in files:
            print(f"  - {f}")
        print()
        for i, path in enumerate(files):
            if i > 0:
                print("Waiting 5 seconds before next file...")
                time.sleep(5)
            print(f"Replaying: {path}")
            replay_pcap(path)
        return

    signal.signal(signal.SIGINT, on_sigint)

    listener_thread = threading.Thread(target=udp_listener, daemon=False)
    listener_thread.start()

    create_gui()
    try:
        root.mainloop()
    finally:
        shutdown()


if __name__ == "__main__":
    main()
