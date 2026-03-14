#!/bin/bash

NEXMON_INSTALL_DIR="$HOME/nexmon"
source "$NEXMON_INSTALL_DIR/setup_env.sh"

NEXMON_CSI_DIR="$NEXMON_ROOT/patches/bcm43455c0/7_45_189/nexmon_csi"

TCPDUMP_OUTPUT=""
NUM_PACKETS=""
RUN_TCPDUMP=false
MAKECSI_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -w)
            if [[ -n "$2" && ! "$2" =~ ^- ]]; then
                TCPDUMP_OUTPUT="$2"
                shift 2
            else
                echo "Error: -w flag requires an argument"
                exit 1
            fi
            ;;
        -np)
            if [[ -n "$2" && ! "$2" =~ ^- ]]; then
                NUM_PACKETS="$2"
                shift 2
            else
                echo "Error: -np flag requires an argument"
                exit 1
            fi
            ;;
        -tcpdump)
            RUN_TCPDUMP=true
            shift
            ;;
        *)
            MAKECSI_ARGS+=("$1")
            shift
            ;;
    esac
done

cleanup() {
    echo
    echo "Restoring Wi-Fi..."
    make -C "$NEXMON_CSI_DIR" -f Makefile.rpi restore-wifi
    exit 0
}

trap cleanup SIGINT

echo "Extracting CSI parameters using makecsiparams with arguments: ${MAKECSI_ARGS[*]}"
CSI_CONFIG=$("$NEXMON_CSI_DIR/utils/makecsiparams/makecsiparams" "${MAKECSI_ARGS[@]}")

if [ $? -ne 0 ] || [ -z "$CSI_CONFIG" ]; then
    echo "Error: makecsiparams failed or returned empty config"
    exit 1
fi

if ! echo "$CSI_CONFIG" | base64 --decode > /dev/null 2>&1; then
    echo "Error: check csi configuration parameters."
    echo "$CSI_CONFIG"
    exit 1
fi

echo "CSI_CONFIG: $CSI_CONFIG"
sleep 2

make -C "$NEXMON_CSI_DIR" -f Makefile.rpi install-firmware
make -C "$NEXMON_CSI_DIR" -f Makefile.rpi unmanage
make -C "$NEXMON_CSI_DIR" -f Makefile.rpi reload-full

nexutil -s500 -b -l34 -v"$CSI_CONFIG"
nexutil -m1

if [ "$RUN_TCPDUMP" = true ]; then
    TCPDUMP_CMD=(sudo tcpdump -i wlan0 dst port 5500)

    if [ -n "$NUM_PACKETS" ]; then
        TCPDUMP_CMD+=(-c "$NUM_PACKETS")
    fi

    if [ -n "$TCPDUMP_OUTPUT" ]; then
        TCPDUMP_CMD+=(-w "$TCPDUMP_OUTPUT")
    fi

    "${TCPDUMP_CMD[@]}"
else
    echo "Waiting until Ctrl+C is pressed..."
    while true; do
        sleep 1
    done
fi

cleanup
