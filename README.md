# CSI Motion Detection

Channel State Information (CSI) based motion detection using WiFi signals. The system captures CSI data from a Raspberry Pi running [Nexmon CSI](https://github.com/seemoo-lab/nexmon_csi), deduplicates and forwards packets to a laptop, where processing and visualization run in real time.

---

## Overview

```
┌─────────────────────┐     UDP (dedup)     ┌─────────────────────┐
│   Raspberry Pi      │ ─────────────────>  │      Laptop         │
│  nexmon_extract.sh  │    port 5500        │   csi_receiver      │
│  nftables           │                     │   csi_recorder      │
└─────────────────────┘                     └─────────────────────┘
```

---

## Raspberry Pi Setup

### nexmon_extract.sh

A bash script run on the Raspberry Pi **after Nexmon CSI is installed**. It configures the CSI capture and optionally runs `tcpdump` to capture raw CSI frames.

**Usage:**
```bash
./nexmon_extract.sh [makecsiparams args] [-tcpdump] [-w OUTPUT] [-np N]
```

**Arguments:**
- **makecsiparams args** — Passed to `makecsiparams` to configure CSI (e.g. bandwidth, core).
- **`-tcpdump`** — Start tcpdump to capture CSI packets from `wlan0` on destination port 5500.
- **`-w FILE`** — Write captured packets to a pcap file.
- **`-np N`** — Limit capture to N packets (use with `-tcpdump`).

**What it does:**
1. Sources the Nexmon environment (`$HOME/nexmon/setup_env.sh`).
2. Builds CSI parameters via `makecsiparams`.
3. Installs firmware, reloads the interface, and configures `nexutil`.
4. Optionally runs `tcpdump -i wlan0 dst port 5500` to record CSI traffic.

**Note:** The script does not include packet deduplication or forwarding. To deduplicate and forward traffic to the laptop, one has to add **nftables rules** on the Raspberry Pi.

---

## Laptop Setup

### csi_receiver

Runs on the **laptop**. Receives CSI UDP packets (from the Pi), parses and decodes them, and distributes data to multiple worker processes.

**Run:**
```bash
python run.py
```

**Architecture:**
- **Receiver worker** — Single process with a recv thread and a ThreadPool. Receives packets on port 5500, filters by source IP, parses with `read_binary`, decodes with nexcsi, and fans out to worker queues.
- **Consumer workers** (multiprocessing):
  - **VizWorker** — PyQtGraph waterfall and magnitude/phase plots.
  - **StatPresenceWorker** — Statistical analysis (Mahalanobis distance, RSSI/CSI variance, IQR) and box plot.
  - **PresenceWorker** — ML-based presence detection.

---

## csi_recorder.py

Used to **record labeled datasets** and **replay** them for evaluation.

### Recording

Run without arguments to start recording:
```bash
python csi_recorder.py
```

- Listens for CSI UDP packets on port 5500 (from `SOURCE_IP`).
- Opens a GUI with a label toggle button (Label: 0 or 1).
- **Left-click** the button to toggle the label; **right-click** to stop and save.
- Packets are written to `csi_recording_YYYYMMDD_HHMMSS.pcap` with the magic byte modified to encode the current label (0x1112 for label 0, 0x1113 for label 1).

### Replay

Replay pcap files for offline evaluation:
```bash
python csi_recorder.py -f recording.pcap
python csi_recorder.py -f dir/*   # Replay all pcaps in a directory
```

- Reads pcap files and replays UDP payloads to `127.0.0.1` with original timing.
- Useful for evaluating models without live capture from the Pi.

---

## cnn_train.ipynb

Jupyter notebook for **data processing** and **CNN training** of the presence-detection model used by `PresenceWorker`.

### Data preparation

- **`extract_time_and_csi`** — Reads pcap files via nexcsi, decodes CSI, removes null and pilot subcarriers.
- **`extract_csi_images`** — Builds sliding-window CSI images (time × subcarriers) with configurable window size and time-tolerance to drop gaps.
- **`csi_image_shuffle_subcarriers`** — Data augmentation: shuffles subcarrier order for a fraction of samples.
- **`image_to_dataset`** — Converts CSI to log-magnitude, normalizes per sample (min-max), and attaches labels.
- **`build_dataset`** — Processes a list of `(pcap_path, label, augment)` tuples and concatenates into a single dataset.

### Model training

- Loads `dataset.npy`, shuffles, and splits into 70% train / 15% validation / 15% test.
- Builds a binary CNN: Conv2D + BatchNorm + ReLU + MaxPool blocks, then Flatten + Dense with sigmoid.
- Input shape: `(window_size, n_subcarriers, 1)` (e.g. 32×51×1).
- Trains with Adam, binary cross-entropy, ModelCheckpoint (`best_model.keras`), and EarlyStopping.

---
