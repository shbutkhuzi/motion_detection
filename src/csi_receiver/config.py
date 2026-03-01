"""Configuration constants for the CSI receiver."""

LISTEN_PORT = 5500
SOURCE_IP = "192.168.137.2"
DEVICE = "raspberrypi"
BANDWIDTH_MHZ = 20  # Don't change this, code is not currently designed to handle multiple bandwidths

BAND_TO_NSUB = {
    20: 64,
    40: 128,
    80: 256,
}

REMOVE_SUBCARRIER_INDEXES = {
    20: [0, 1, 2, 3, 11, 25, 32, 39, 53, 60, 61, 62, 63]
}

N_SUBCARRIERS = BAND_TO_NSUB[BANDWIDTH_MHZ]
WATERFALL_SIZE = 256
DB_EPSILON = 1e-12
DB_MIN = -100.0
DB_MAX = -30.0
