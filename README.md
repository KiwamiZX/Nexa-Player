<p align=center><img width="300" height="300" alt="nexaplayer" src="https://github.com/user-attachments/assets/51093eee-c5ed-4ac6-ba2a-3408f037d8e5" /></p>
# NexaPlayer 🎵▶️

- A sleek, minimalist media player built with **Python, PySide6, and python-vlc.** 
- It’s designed to be lightweight, modern, and visually distinctive. I originally created it to make streaming videos through VLC easier—especially when sharing on Discord—so I could watch along without constantly resizing the window (which otherwise reduces stream quality).

---

## 🚀 Features

- **VLC engine:** Reliable audio/video playback via VLC.
- **Clean UI:** PySide6 (Qt) interface with custom-styled controls.
- **Dual modes:** Main window and mini-player, both synced.
- **Shortcuts:** hide the mini player's HUD with H, press F for Fullscreen, and navigate the video with the arrow keys.

---

## Screenshots

<img width="1290" height="750" alt="image" src="https://github.com/user-attachments/assets/9ce6dc4e-8b44-48a4-b226-a791464f591a" />


<img width="1073" height="739" alt="image" src="https://github.com/user-attachments/assets/1e502d7f-4c99-4629-b3cd-4432662e4ff5" />



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

