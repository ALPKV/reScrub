"""Reading and validating the uploaded recording."""

import json
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


class VideoError(ValueError):
    """The file is not a usable recording. The message is shown to the user."""


@dataclass
class VideoInfo:
    fps: float
    width: int
    height: int
    duration_sec: float
    frame_count: int


def looks_like_mp4(head: bytes) -> bool:
    """MP4/MOV files carry an 'ftyp' box in their first bytes."""
    return len(head) >= 12 and head[4:8] == b"ftyp"


def probe(path: Path, max_duration_sec: float | None = None) -> VideoInfo:
    """Inspect the file with ffprobe; reject anything that is not an MP4 video."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", "-select_streams", "v:0", str(path),
            ],
            capture_output=True, check=True, timeout=60,
        ).stdout
        meta = json.loads(out)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError):
        raise VideoError("The file could not be read as a video.") from None

    fmt = meta.get("format", {}).get("format_name", "")
    if "mp4" not in fmt and "mov" not in fmt:
        raise VideoError("Only MP4 recordings are supported.")
    streams = meta.get("streams") or []
    if not streams:
        raise VideoError("The file has no video track.")
    s = streams[0]

    num, _, den = s.get("avg_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    if not fps or fps > 240:
        num, _, den = s.get("r_frame_rate", "30/1").partition("/")
        fps = float(num) / float(den or 1)
    duration = float(s.get("duration") or meta["format"].get("duration") or 0)
    if duration <= 0:
        raise VideoError("The recording has no duration.")
    if max_duration_sec and duration > max_duration_sec:
        raise VideoError(
            f"The recording is {duration / 60:.1f} minutes; the limit is {max_duration_sec / 60:.0f} minutes."
        )
    frames = int(s.get("nb_frames") or round(duration * fps))
    return VideoInfo(
        fps=fps, width=int(s["width"]), height=int(s["height"]), duration_sec=duration, frame_count=frames
    )


def iter_frames(path: Path, fps: float, stride: int = 1) -> Iterator[tuple[int, float, np.ndarray]]:
    """Yield (frame_index, video_sec, BGR frame) for every `stride`-th frame."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise VideoError("The file could not be decoded.")
    idx = 0
    try:
        while True:
            ok = cap.grab()
            if not ok:
                break
            if idx % stride == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                yield idx, idx / fps, frame
            idx += 1
    finally:
        cap.release()
