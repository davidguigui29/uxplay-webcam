# 📱 UxPlay Virtual Webcam for Linux

A powerful, seamless engine that transforms your iPhone or iPad into a **high-quality, wireless 60fps virtual webcam** for Linux. By combining the AirPlay receiver capabilities of [UxPlay](https://github.com/FDH2/UxPlay) with a custom GStreamer/Python engine, you can stream your device's screen directly into Google Meet, Zoom, OBS, and other web apps.

## ✨ Features

- **Perfect 60fps Output**: Up-samples and locks your iPhone's variable AirPlay feed to a buttery-smooth, strict `60fps YUYV` format so browsers never reject it.
- **Seamless Engine**: Unplug your iPhone? Screen locks? The engine instantly switches to a standby pattern. Reconnect, and you're instantly live again without your browser ever dropping the camera.
- **On-the-Fly Controls**: Flip the camera horizontally or change the aspect ratio crop in real-time using simple keyboard inputs.
- **Bulletproof Recovery**: Automatically cleans up stale/ghost processes and verifies kernel module device states before starting.

## 🛠 Prerequisites

You will need the following dependencies installed on your Linux machine:

- `uxplay` (AirPlay mirroring receiver)
- `v4l2loopback-dkms` (To create the virtual camera devices)
- `gstreamer1.0` (Core video pipeline)
- `python3-gi` (GObject introspection bindings for Python to control GStreamer)

You must configure `v4l2loopback` to create at least two devices (e.g., `/dev/video10` as the intermediate receiver, and `/dev/video11` as the final virtual camera). Ensure `/dev/video11` has `exclusive_caps=1` enabled.

## 🚀 Usage

Simply run the bash wrapper to initialize the engine:

```bash
bash ~/uxplay-webcam/uxplay-webcam.sh
```

Once you see `Engine running. Ready for AirPlay connections!`, mirror your iPhone's screen via the AirPlay menu. The feed will instantly appear in your virtual camera device (usually "UxPlay Screen" or `/dev/video11`).

### Live Keyboard Controls

While the engine is running in your terminal, press the following keys to adjust the stream instantly:

**Camera Flip:**
- `f`: **Front Camera** mode (Normal display)
- `b`: **Back Camera** mode (Horizontal flip — perfect for using your iPhone's rear camera!)

**Aspect Ratio Crop:**
- `p`: **Portrait** (Passes the native vertical iPhone ratio)
- `l`: **Landscape** (Forces a 16:9 crop — great for full-screen presentations)
- `s`: **Square** (Forces a 1:1 crop)

**Exit:**
- `q`: Safely tear down the pipeline and restart your background systemd uxplay service.

## ⚠️ Troubleshooting: The Browser Lock

If you receive an error that looks like this:
```text
Call to S_FMT failed for YUYV @ 1280x720: Device or resource busy
```
**This is not a bug!** When a web browser (like Google Chrome with a Google Meet tab open) connects to your virtual camera, it places a lock on the device that prevents the Linux kernel from changing its video format. 

**The Fix:**
1. Close your Google Meet tab completely.
2. Start this script.
3. Once the engine is fully running, reopen your Google Meet tab.

Because the engine is seamless and never crashes when you disconnect your phone, you only ever need to do this when you first boot the script!

---

## 🤝 Contributing

**We would love your help!** 

This project was built to solve the lack of high-quality, seamless iPhone webcam support on Linux. If you're passionate about Linux multimedia, Python, or GStreamer, there is plenty of room for improvement!

Here are some things we'd love help with:
- **Auto-setup scripts**: Help automate the `v4l2loopback` modprobe setup and device creation.
- **Audio support**: Currently, this is video-only. Routing AirPlay audio into a virtual PulseAudio/PipeWire microphone would be a massive upgrade.
- **UI/Tray Icon**: Wrapping the bash terminal controls into a clean GNOME/KDE tray applet.
- **Dynamic Resolution**: Handling native iPad vs. iPhone aspect ratios more elegantly.

### How to contribute
1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request!

Feel free to open an Issue if you have ideas, feedback, or discover bugs. Let's make Linux video streaming amazing together.
