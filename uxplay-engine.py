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

# Device assignments:
# INTERMEDIATE_CAM = Raw stream from UxPlay ("UxPlay Raw Feed")
# VIRTUAL_CAM = Final 720p virtual webcam for web applications ("UxPlay Virtual Webcam")
INTERMEDIATE_CAM = "/dev/video11"
VIRTUAL_CAM = "/dev/video12"
MAX_RETRIES = 3

# Startup device check removed because dynamically created devices with pre-set caps can safely be held by browsers

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
    videoconvert ! videoscale add-borders=true ! capsfilter caps=video/x-raw,width=1280,height=720,format=YUY2,framerate=30/1,pixel-aspect-ratio=1/1,colorimetry=bt709,interlace-mode=progressive,multiview-mode=mono ! v4l2sink device={VIRTUAL_CAM} sync=false

    videotestsrc is-live=true pattern=smpte ! videoconvert ! videorate ! capsfilter caps=video/x-raw,format=YUY2,framerate=30/1,colorimetry=bt709 ! selector.sink_0
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
    time.sleep(0.5)
    ret = pipeline.set_state(Gst.State.PLAYING)

if ret == Gst.StateChangeReturn.FAILURE:
    msg = bus.pop_filtered(Gst.MessageType.ERROR)
    if msg:
        err, debug = msg.parse_error()
        sys.stderr.write(f"[ERROR] Failed to start pipeline: {err.message} ({debug})\n")
    else:
        sys.stderr.write("[ERROR] Failed to start pipeline.\n")
    sys.exit(1)

def check_loopback():
    """ Quietly checks if the iPhone is actively sending video """
    try:
        out = subprocess.check_output(["v4l2-ctl", "--all", "-d", INTERMEDIATE_CAM], stderr=subprocess.DEVNULL)
        return b"loopback: ok" in out
    except:
        return False

def apply_command(cmd):
    """ Executes user commands thread-safely on the GLib main loop """
    try:
        _, state, _ = pipeline.get_state(0)
        if state != Gst.State.PLAYING:
            return False
            
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
        sys.stderr.write(f"\n[WARN] Ignored command: {e}\n")
    return False

def command_listener():
    """ Listens for user commands from the Bash wrapper and delegates to GLib main thread """
    for line in sys.stdin:
        cmd = line.strip().lower()
        GLib.idle_add(apply_command, cmd)

live_bin = None
live_pad = None
is_live = False          # live source bin is attached to the selector
live_active = False      # selector is currently showing the live source
last_buf_time = 0.0      # updated from the streaming thread whenever a live frame arrives

def _on_live_buffer(pad, info):
    global last_buf_time
    last_buf_time = time.time()
    return Gst.PadProbeReturn.OK

def do_connect():
    """ Attaches the live iPhone source (on the GLib main loop). It is NOT shown until real frames arrive. """
    global live_bin, live_pad, is_live, last_buf_time
    if is_live:
        return False
    try:
        last_buf_time = 0.0
        live_bin = Gst.parse_bin_from_description(
            f"v4l2src device={INTERMEDIATE_CAM} ! videoconvert ! videorate ! capsfilter caps=video/x-raw,format=YUY2,framerate=30/1,colorimetry=bt709",
            True
        )
        pipeline.add(live_bin)
        live_pad = selector.request_pad_simple("sink_%u")
        src_pad = live_bin.get_static_pad("src")
        src_pad.add_probe(Gst.PadProbeType.BUFFER, _on_live_buffer)
        src_pad.link(live_pad)
        live_bin.sync_state_with_parent()
        is_live = True
    except Exception as e:
        sys.stderr.write(f"[WARN] Failed to connect live source: {e}\n")
        _teardown_live()
    return False

def _teardown_live():
    global live_bin, live_pad, is_live, live_active
    try:
        selector.set_property("active-pad", selector.get_static_pad("sink_0"))
    except Exception:
        pass
    live_active = False
    if live_bin:
        try:
            live_bin.set_state(Gst.State.NULL)
            src_pad = live_bin.get_static_pad("src")
            if src_pad and live_pad:
                src_pad.unlink(live_pad)
            if live_pad:
                selector.release_request_pad(live_pad)
            pipeline.remove(live_bin)
        except Exception as e:
            sys.stderr.write(f"[WARN] Failed to remove live source: {e}\n")
    live_bin = None
    live_pad = None
    is_live = False

def do_disconnect():
    """ iPhone gone: drop the live source and go back to the standby pattern. """
    if is_live:
        _teardown_live()
    return False

def set_live_active(active):
    """ Switch the selector between live feed and standby (GLib main loop only). """
    global live_active
    try:
        if active and is_live and live_pad is not None:
            selector.set_property("active-pad", live_pad)
            live_active = True
        else:
            selector.set_property("active-pad", selector.get_static_pad("sink_0"))
            live_active = False
    except Exception as e:
        sys.stderr.write(f"[WARN] Failed to switch source: {e}\n")
    return False

def connection_monitor():
    """ Attaches/detaches the live source and shows it only while frames are really flowing. """
    while True:
        connected = check_loopback()
        if connected and not is_live:
            GLib.idle_add(do_connect)
        elif not connected and is_live:
            GLib.idle_add(do_disconnect)
        elif is_live:
            flowing = (time.time() - last_buf_time) < 2.0 if last_buf_time else False
            if flowing and not live_active:
                GLib.idle_add(set_live_active, True)
            elif not flowing and live_active:
                GLib.idle_add(set_live_active, False)
        time.sleep(0.5)

# Start background threads
t1 = threading.Thread(target=command_listener, daemon=True)
t1.start()

t2 = threading.Thread(target=connection_monitor, daemon=True)
t2.start()

retry_count = 0

def bus_call(bus, message, loop):
    global retry_count, live_bin, live_pad, is_live
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
            try:
                holders = subprocess.run(["fuser", "-v", VIRTUAL_CAM], capture_output=True, text=True)
                sys.stderr.write(f"[DEBUG] Holders of {VIRTUAL_CAM}: {holders.stdout.strip()} {holders.stderr.strip()}\n")
            except Exception:
                pass
            if retry_count > MAX_RETRIES:
                sys.stderr.write(f"\n[FATAL] Failed to open {VIRTUAL_CAM} after {MAX_RETRIES} retries.\n")
                sys.stderr.write(f"Something is holding the device. Run: lsof {VIRTUAL_CAM}\n\n")
                loop.quit()
                return True
            
            sys.stderr.write(f"\n[RETRY {retry_count}/{MAX_RETRIES}] {VIRTUAL_CAM} is busy. Retrying in 3 seconds...\n")
            if is_live:
                _teardown_live()
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
