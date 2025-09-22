# NexaPlayer 🎵▶️

- A sleek, minimalist media player built with **Python, PySide6, and python-vlc.** 
- It’s designed to be lightweight, modern, and visually distinctive. I originally created it to make streaming videos through VLC easier—especially when sharing on Discord—so I could watch along without constantly resizing the window (which otherwise reduces stream quality).

---

## 🚀 Features

- **VLC engine:** Reliable audio/video playback via VLC.
- **Clean UI:** PySide6 (Qt) interface with custom-styled controls.
- **Dual modes:** Main window and mini-player, both synced.

---

## 📦 Requirements and Compiling

- **Python:** 3.10+
- **VLC Media Player:** 64-Bits VLC only (For now) [Download it here](https://get.videolan.org/vlc/3.0.21/win64/vlc-3.0.21-win64.exe)
- **Requirements:**
  ```bash
  pip install PySide6 python-vlc

- **Compiling:**
  - Make sure you have pyinstaller, and then run this on your terminal:
    ```bash
    python -m PyInstaller --onefile --windowed --icon=icons/nexaplayer.ico --name NexaPlayer --hidden-import=vlc app.py
- (Optionally, you can just download and run the latest stable build, if you don't want to compile it yourself)

