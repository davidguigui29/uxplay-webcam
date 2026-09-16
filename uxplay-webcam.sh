#!/bin/bash

# Target Virtual Video Devices
INTERMEDIATE_CAM="/dev/video11"  # Raw stream from UxPlay
VIRTUAL_CAM="/dev/video12"       # Final output for Web Browser / Apps

# Default states
MODE="f"      # 'f' = front camera (normal), 'b' = back camera (horizontal flip)
RES_MODE="p"  # 'p' = portrait (9:16), 'l' = landscape (16:9), 's' = square (1:1)

ENGINE_PID=""
UXPLAY_PID=""
CMD_PIPE="/tmp/uxplay-cmds"

# ============================================================
# Aggressive cleanup: kill ANYTHING that could hold video devices
# ============================================================
nuke_stale_processes() {
    # Kill any orphaned uxplay-engine.py
    pkill -9 -f "uxplay-engine.py" 2>/dev/null

    # Kill any orphaned gst-launch processes touching our devices
    pkill -9 -f "v4l2sink device=$VIRTUAL_CAM" 2>/dev/null
    pkill -9 -f "v4l2sink device=$INTERMEDIATE_CAM" 2>/dev/null

    # Destroy the virtual device if it was left behind
    v4l2loopback-ctl delete $VIRTUAL_CAM 2>/dev/null

    # Kill any orphaned uxplay (but NOT the systemd one yet — that's handled separately)
    killall -9 uxplay 2>/dev/null

    # Wait for v4l2loopback to fully release the device handles
    sleep 2
}

# ============================================================
# Verify /dev/video11 is free before we try to use it
# ============================================================
# check_device_free is no longer needed since we dynamically create the device and set caps

start_uxplay() {
    echo "Starting background UxPlay server (AirPlay Receiver)..."
    # Force YUY2 format into the intermediate v4l2loopback device
    /usr/bin/uxplay -vc videoconvert -vs "videoconvert ! video/x-raw,format=YUY2 ! v4l2sink device=$INTERMEDIATE_CAM" >/dev/null 2>&1 &
    UXPLAY_PID=$!
}

stop_uxplay() {
    if [ -n "$UXPLAY_PID" ]; then
        kill "$UXPLAY_PID" 2>/dev/null
        sleep 0.5
        kill -9 "$UXPLAY_PID" 2>/dev/null
        wait "$UXPLAY_PID" 2>/dev/null
        UXPLAY_PID=""
    fi
}

start_engine() {
    # Create named pipe for commands
    rm -f "$CMD_PIPE"
    mkfifo "$CMD_PIPE"
    
    # Launch Python Engine listening on the pipe
    python3 ~/uxplay-webcam/uxplay-engine.py < "$CMD_PIPE" &
    ENGINE_PID=$!
    
    # Keep the pipe open for writing
    exec 3> "$CMD_PIPE"
}

stop_engine() {
    if [ -n "$ENGINE_PID" ]; then
        # Try graceful shutdown first
        echo "q" >&3 2>/dev/null
        sleep 0.5
        kill "$ENGINE_PID" 2>/dev/null
        sleep 0.5
        kill -9 "$ENGINE_PID" 2>/dev/null
        wait "$ENGINE_PID" 2>/dev/null
        ENGINE_PID=""
    fi
    exec 3>&- 2>/dev/null
    rm -f "$CMD_PIPE"
}

cleanup() {
    echo -e "\nExiting..."
    stop_engine
    stop_uxplay
    
    echo "Destroying dynamic virtual camera..."
    v4l2loopback-ctl delete $VIRTUAL_CAM 2>/dev/null

    echo "Restarting background UxPlay service for normal desktop mirroring..."
    systemctl --user start uxplay.service 2>/dev/null
    echo "Done!"
    exit 0
}

# Trap Ctrl+C and exit signals
trap cleanup SIGINT SIGTERM EXIT

# ============================================================
# Startup sequence
# ============================================================
echo "Stopping background UxPlay service (if running)..."
systemctl --user stop uxplay.service 2>/dev/null

echo "Cleaning up stale processes..."
nuke_stale_processes

echo "Creating dynamic virtual camera ($VIRTUAL_CAM)..."
v4l2loopback-ctl add -n "UxPlay Screen" -x 1 $VIRTUAL_CAM || {
    echo "[ERROR] Failed to create dynamic virtual camera device!"
    exit 1
}
v4l2loopback-ctl set-caps $VIRTUAL_CAM "YUYV:1280x720@30/1"



echo "------------------------------------------------------------------------"
echo "UxPlay Virtual Webcam initialized! (Powered by Python Engine)"
echo "Target device in web apps (Meet, Zoom, OBS): 'UxPlay Screen' ($VIRTUAL_CAM)"
echo "------------------------------------------------------------------------"
echo "Controls (Camera Flip):"
echo "  'f' = FRONT CAMERA mode (normal)"
echo "  'b' = BACK CAMERA / SCREEN SHARE mode (horizontal flip)"
echo ""
echo "Controls (Aspect Ratio Crop):"
echo "  'p' = PORTRAIT (Native iPhone Ratio) [Default]"
echo "  'l' = LANDSCAPE (16:9)"
echo "  's' = SQUARE (1:1)"
echo ""
echo "Press 'q' or Ctrl+C to quit and return to normal background mode"
echo "------------------------------------------------------------------------"

# 1. Start UxPlay AirPlay Receiver in background
start_uxplay
sleep 2

# 2. Start the seamless Python engine
start_engine

# Wait a moment then verify the engine is alive
sleep 2
if ! kill -0 "$ENGINE_PID" 2>/dev/null; then
    echo ""
    echo "[ERROR] Python engine failed to start. Check errors above."
    echo ""
    cleanup
    exit 1
fi

echo "Engine running. Ready for AirPlay connections!"
echo ""

# Input loop: instantly pass commands to the Python engine
while read -rsn1 key; do
    case "$key" in
        f|F)
            echo "f" >&3
            echo "Active Mode: FRONT CAMERA (Normal)" ;;
        b|B)
            echo "b" >&3
            echo "Active Mode: BACK CAMERA (Horizontal Flip)" ;;
        p|P)
            echo "p" >&3
            echo "Aspect Ratio: PORTRAIT (Native iPhone Ratio)" ;;
        l|L)
            echo "l" >&3
            echo "Aspect Ratio: LANDSCAPE (16:9)" ;;
        s|S)
            echo "s" >&3
            echo "Aspect Ratio: SQUARE (1:1)" ;;
        q|Q)
            cleanup ;;
    esac
done
