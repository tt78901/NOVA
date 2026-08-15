"""A single shared microphone stream.

Both the wake-word detector and the utterance recorder need mic audio. Opening
two streams fights over the device, so we open one and hand out frames from a
queue instead.
"""

from __future__ import annotations

import queue
import sys
import threading

import numpy as np
import sounddevice as sd

from ..config import AudioConfig


class Microphone:
    def __init__(self, cfg: AudioConfig):
        self.cfg = cfg
        self.samplerate = cfg.samplerate
        self.frame_samples = int(cfg.samplerate * cfg.frame_ms / 1000)
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=100)
        self._stream: sd.InputStream | None = None
        self._muted = threading.Event()

    # -- lifecycle ---------------------------------------------------------

    def _callback(self, indata, _frames, _time, status):
        if status:
            print(f"[mic] {status}", file=sys.stderr)
        if self._muted.is_set():
            return
        try:
            self._queue.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass  # consumer is behind; dropping is better than blocking audio

    def start(self) -> None:
        if self._stream is not None:
            return
        device = self.cfg.input_device or None
        self._stream = sd.InputStream(
            device=device,
            channels=1,
            samplerate=self.samplerate,
            blocksize=self.frame_samples,
            dtype="int16",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def __enter__(self) -> "Microphone":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    # -- consumption -------------------------------------------------------

    def mute(self) -> None:
        """Stop capturing. Used while speaking so we don't hear ourselves."""
        self._muted.set()
        self.drain()

    def unmute(self) -> None:
        self.drain()
        self._muted.clear()

    def drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        """Next int16 frame of `frame_ms`, or None if the mic went quiet."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None


def list_devices() -> str:
    return str(sd.query_devices())
