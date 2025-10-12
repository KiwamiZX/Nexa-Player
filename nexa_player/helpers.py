from __future__ import annotations

import os
import urllib.parse

import cv2


def get_video_duration(path: str) -> int:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return 0
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.release()
    if fps > 0:
        return int((frame_count / fps) * 1000)
    return 0


def ms_to_minsec(ms: int) -> str:
    if ms <= 0:
        return "00:00"
    seconds = ms // 1000
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def clean_filename_from_mrl(mrl: str) -> str:
    if mrl.startswith("file:///"):
        path = urllib.parse.unquote(mrl[8:])
    else:
        path = urllib.parse.unquote(mrl)
    return os.path.basename(path)
