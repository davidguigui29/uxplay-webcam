#!/usr/bin/env python3
import gi
import sys
import threading
import time
import subprocess
import os

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

# Initialize GStreamer
Gst.init(sys.argv)

INTERMEDIATE_CAM = "/dev/video10"
VIRTUAL_CAM = "/dev/video11"
MAX_RETRIES = 3

# ============================================================
# Startup device check: verify /dev/video11 is writable
# ============================================================
def check_device_available():
    """Verify the virtual camera device is available for writing."""
    try:
        result = subprocess.run(
            ["lsof", VIRTUAL_CAM],
            capture_output=True, text=True, timeout=5
        )
        # Filter out lsof warnings, keep actual process lines
        holders = []
        for line in result.stdout.splitlines():
            if line.startswith("COMMAND") or "WARNING" in line or "can't stat" in line or "Output info" in line:
                continue
            if line.strip():
                parts = line.split()
                if len(parts) >= 2:
                    holders.append(f"{parts[0]} (PID {parts[1]})")
        
        if holders:
            sys.stderr.write(f"\n[ERROR] {VIRTUAL_CAM} is locked by other processes:\n")
            for h in holders:
                sys.stderr.write(f"  - {h}\n")
            sys.stderr.write(f"\nPlease close those applications and try again.\n\n")
            return False
    except Exception:
        pass  # lsof not available or timed out, proceed anyway
    
    return True

if not check_device_available():
    sys.exit(1)

# ============================================================
# The Core Pipeline
# ============================================================
# input-selector seamlessly switches between the standby pattern and live iPhone feed.
# The output (v4l2sink) runs continuously so the browser NEVER loses connection.
# videoscale with add-borders=true forces EXACTLY 1280x720 output forever.
# NO videorate in the main pipeline — it causes negotiation failure with v4l2sink
# after input-selector. Instead, videorate is applied per-source in the live bin.
pipeline_str = f"""
    input-selector name=selector ! 
    videoconvert ! videoflip name=flip method=none ! aspectratiocrop name=crop aspect-ratio=0/1 ! 
    videoconvert ! video/x-raw,pixel-aspect-ratio=1/1 ! videoscale add-borders=true ! video/x-raw,width=1280,height=720,format=YUY2,framerate=60/1,pixel-aspect-ratio=1/1 ! v4l2sink device={VIRTUAL_CAM}

    videotestsrc is-live=true pattern=smpte ! video/x-raw,framerate=60/1,pixel-aspect-ratio=1/1 ! videoconvert ! selector.sink_0
"""

try:
    pipeline = Gst.parse_launch(pipeline_str)
except GLib.Error as e:
    sys.stderr.write(f"[ERROR] Failed to create GStreamer pipeline: {e}\n")
    sys.exit(1)

selector = pipeline.get_by_name("selector")
flip = pipeline.get_by_name("flip")
crop = pipeline.get_by_name("crop")

bus = pipeline.get_bus()

# Start playing the Standby pattern immediately
ret = pipeline.set_state(Gst.State.PLAYING)
if ret == Gst.StateChangeReturn.FAILURE:
    sys.stderr.write("[ERROR] Failed to start pipeline.\n")
    sys.exit(1)

def check_loopback():
    """ Quietly checks if the iPhone is actively sending video """
    try:
        out = subprocess.check_output(["v4l2-ctl", "--all", "-d", INTERMEDIATE_CAM], stderr=subprocess.DEVNULL)
        return b"loopback: ok" in out
    except:
        return False

def command_listener():
    """ Listens for user commands from the Bash wrapper and dynamically changes properties without restarting! """
    for line in sys.stdin:
        cmd = line.strip().lower()
        
        # Don't try to change properties if the pipeline is restarting or down
        _, state, _ = pipeline.get_state(0)
        if state != Gst.State.PLAYING:
            continue
            
        try:
            if cmd == 'f':
                flip.set_property("method", 0)
            elif cmd == 'b':
                flip.set_property("method", 4)
            elif cmd == 'p':
                Gst.util_set_object_arg(crop, "aspect-ratio", "0/1")  # native (bypass crop)
            elif cmd == 'l':
                Gst.util_set_object_arg(crop, "aspect-ratio", "16/9")
            elif cmd == 's':
                Gst.util_set_object_arg(crop, "aspect-ratio", "1/1")
            elif cmd == 'q':
                pipeline.set_state(Gst.State.NULL)
                os._exit(0)
        except Exception as e:
            sys.stderr.write(f"\\n[WARN] Ignored command during restart: {e}\\n")

def connection_monitor():
    """ Monitors the iPhone connection and seamlessly injects the live feed into the running pipeline! """
    live_bin = None
    live_pad = None
    is_live = False
    
    while True:
        connected = check_loopback()
        
        if connected and not is_live:
            try:
                # Create live camera source dynamically, immediately matching the 60fps requirement!
                live_bin = Gst.parse_bin_from_description(
                    f"v4l2src device={INTERMEDIATE_CAM} ! videoconvert ! videorate ! capsfilter caps=video/x-raw,framerate=60/1",
                    True
                )
                pipeline.add(live_bin)
                live_pad = selector.request_pad_simple("sink_%u")
                src_pad = live_bin.get_static_pad("src")
                src_pad.link(live_pad)
                
                # Sync state and switch instantly!
                live_bin.sync_state_with_parent()
                selector.set_property("active-pad", live_pad)
                is_live = True
            except Exception as e:
                sys.stderr.write(f"[WARN] Failed to connect live source: {e}\n")
                # Cleanup partial state
                if live_bin:
                    live_bin.set_state(Gst.State.NULL)
                    try:
                        pipeline.remove(live_bin)
                    except:
                        pass
                live_bin = None
                live_pad = None
                is_live = False
            
        elif not connected and is_live:
            # Switch back to standby instantly!
            standby_pad = selector.get_static_pad("sink_0")
            selector.set_property("active-pad", standby_pad)
            
            # Safely remove the live camera source
            if live_bin:
                live_bin.set_state(Gst.State.NULL)
                src_pad = live_bin.get_static_pad("src")
                if src_pad and live_pad:
                    src_pad.unlink(live_pad)
                if live_pad:
                    selector.release_request_pad(live_pad)
                pipeline.remove(live_bin)
            
            live_bin = None
            live_pad = None
            is_live = False
            
        time.sleep(1)

# Start background threads
t1 = threading.Thread(target=command_listener, daemon=True)
t1.start()

t2 = threading.Thread(target=connection_monitor, daemon=True)
t2.start()

retry_count = 0

def bus_call(bus, message, loop):
    global retry_count
    """ Gracefully handles unexpected GStreamer errors """
    t = message.type
    if t == Gst.MessageType.EOS:
        loop.quit()
    elif t == Gst.MessageType.ERROR:
        err, debug = message.parse_error()
        src_name = message.src.get_name() if message.src else "unknown"
        full_err = (err.message + " " + (debug or "")).lower()
        
        # Ignore v4l2src crashes — the connection_monitor handles reconnection
        if "v4l2src" in src_name or "v4l2src" in full_err:
            return True
        
        # v4l2sink errors (device busy, not output device, etc.)
        if "v4l2sink" in src_name or "v4l2sink" in full_err or "busy" in full_err or "output device" in full_err:
            sys.stderr.write(f"\\n[DEBUG] Raw Error: src={src_name}, msg={err.message}, debug={debug}\\n")
            retry_count += 1
            if retry_count > MAX_RETRIES:
                sys.stderr.write(f"\n[FATAL] Failed to open {VIRTUAL_CAM} after {MAX_RETRIES} retries.\n")
                sys.stderr.write(f"Something is holding the device. Run: lsof {VIRTUAL_CAM}\n\n")
                loop.quit()
                return True
            
            sys.stderr.write(f"\n[RETRY {retry_count}/{MAX_RETRIES}] {VIRTUAL_CAM} is busy. Retrying in 3 seconds...\n")
            pipeline.set_state(Gst.State.NULL)
            
            def do_restart():
                pipeline.set_state(Gst.State.PLAYING)
                return False
                
            GLib.timeout_add_seconds(3, do_restart)
            return True
            
        sys.stderr.write(f"GStreamer Error: {err.message}: {debug}\n")
        loop.quit()
    return True

bus.add_signal_watch()
loop = GLib.MainLoop()
bus.connect("message", bus_call, loop)

try:
    loop.run()
except KeyboardInterrupt:
    pass

pipeline.set_state(Gst.State.NULL)
