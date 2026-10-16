"""
Push-to-talk for the pit radio from any keyboard key or wheel button (Linux).

Press the button once and talk: the channel closes when you stop talking (or on a second
press), the speech is transcribed locally with Parakeet (onnx-asr) and sent to the engineer.
Presses are ignored while the engineer is switched off on the page or the car is not on track,
so the same button can still work the game's menus.

  python radio_ptt.py --learn     # press the button you want; prints PTT_DEVICE and PTT_CODE
  python radio_ptt.py             # listen for PTT_DEVICE / PTT_CODE from the environment

Needs read access to /dev/input (the input group), pw-record and pw-play (PipeWire), and
the onnx-asr package. BOXBOX_URL points at the server (default http://127.0.0.1:8000).
"""
import argparse
import json
import math
import os
import selectors
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np

EVENT = struct.Struct("llHHi")
EV_KEY = 1
PRESS = 1
RATE = 16000
FRAME = 480  # 30 ms
# Speech is anything this many times louder than the noise measured when the channel opens
SPEECH_FACTOR = 2.5
MIN_SPEECH_RMS = 400.0
END_SILENCE_S = 0.9
NO_SPEECH_S = 5.0
MAX_S = 15.0
CALIBRATE_S = 0.25
# Transcription threads: onnxruntime otherwise takes every allowed core and spin-waits on them,
# which makes a running game stutter
THREADS = int(os.getenv("PTT_THREADS", "2"))


def input_devices() -> list[tuple[str, str]]:
    """(name, /dev/input/eventN) for every input device the kernel lists."""
    found, name = [], ""
    for line in Path("/proc/bus/input/devices").read_text().splitlines():
        if line.startswith("N: Name="):
            name = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("H: Handlers="):
            for handler in line.split("=", 1)[1].split():
                if handler.startswith("event"):
                    found.append((name, f"/dev/input/{handler}"))
    return found


def open_devices(match: str = "") -> dict:
    opened = {}
    for name, path in input_devices():
        if match.lower() in name.lower():
            try:
                opened[os.open(path, os.O_RDONLY | os.O_NONBLOCK)] = name
            except OSError:
                pass
    return opened


def presses(selector: selectors.BaseSelector):
    """Yield (device name, key code) for every key or button press, until every device is gone."""
    while selector.get_map():
        for key, _ in selector.select():
            try:
                data = os.read(key.fd, EVENT.size * 64)
            except OSError:
                selector.unregister(key.fd)
                os.close(key.fd)
                continue
            for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                _, _, etype, code, value = EVENT.unpack_from(data, i)
                if etype == EV_KEY and value == PRESS:
                    yield key.data, code


def learn():
    devices = open_devices()
    if not devices:
        sys.exit("No readable input devices: is this user in the input group?")
    selector = selectors.DefaultSelector()
    for fd, name in devices.items():
        selector.register(fd, selectors.EVENT_READ, name)
    print("Press the key or wheel button to use for the radio...", flush=True)
    name, code = next(presses(selector))
    print(f"PTT_DEVICE={name}\nPTT_CODE={code}")


def tone(path: Path, freq: float, ms: int = 90):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        n = RATE * ms // 1000
        fade = RATE // 200
        w.writeframes(b"".join(
            struct.pack("<h", int(9000 * math.sin(2 * math.pi * freq * i / RATE) * min(1, i / fade, (n - i) / fade)))
            for i in range(n)
        ))


class Radio:
    def __init__(self, url: str, model_name: str):
        import onnx_asr
        import onnxruntime
        self.url = url.rstrip("/")
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = THREADS
        options.inter_op_num_threads = 1
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        # nixpkgs onnxruntime prefers OpenVINO, which fails on Parakeet
        self.model = onnx_asr.load_model(
            model_name, quantization="int8", sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.busy = False
        self.closed_reason = None
        self.stop_now = threading.Event()
        cues = Path(tempfile.mkdtemp(prefix="boxbox-ptt-"))
        self.open_cue, self.close_cue = cues / "open.wav", cues / "close.wav"
        tone(self.open_cue, 880)
        tone(self.close_cue, 620)

    def cue(self, path: Path):
        subprocess.Popen(["pw-play", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def closed(self) -> str | None:
        """Why the channel stays shut right now, or None when the engineer is listening."""
        try:
            with urllib.request.urlopen(f"{self.url}/api/engineer", timeout=1) as resp:
                status = json.loads(resp.read())
        except Exception:
            return "Box-Box is not running"
        if not status.get("on"):
            return "the engineer is switched off"
        return None if status.get("listening") else "not on track"

    def pressed(self):
        if self.busy:
            self.stop_now.set()
            return
        reason = self.closed()
        if reason:
            if reason != self.closed_reason:
                print(f"Radio closed: {reason}", flush=True)
            self.closed_reason = reason
            return
        self.closed_reason = None
        self.busy = True
        self.stop_now.clear()
        threading.Thread(target=self.transmit, daemon=True).start()

    def record(self) -> np.ndarray | None:
        proc = subprocess.Popen(
            ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        frames, floor, spoke, quiet_since = [], [], False, None
        started = time.monotonic()
        try:
            while not self.stop_now.is_set():
                chunk = proc.stdout.read(FRAME * 2)
                if not chunk:
                    break
                samples = np.frombuffer(chunk, dtype=np.int16)
                frames.append(samples)
                rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
                elapsed = time.monotonic() - started
                if elapsed < CALIBRATE_S:
                    floor.append(rms)
                    continue
                threshold = max(MIN_SPEECH_RMS, SPEECH_FACTOR * (float(np.median(floor)) if floor else 0.0))
                if rms >= threshold:
                    spoke, quiet_since = True, None
                elif spoke:
                    quiet_since = quiet_since or time.monotonic()
                    if time.monotonic() - quiet_since >= END_SILENCE_S:
                        break
                if (not spoke and elapsed >= NO_SPEECH_S) or elapsed >= MAX_S:
                    break
        finally:
            proc.terminate()
            proc.wait()
        if not spoke or not frames:
            return None
        return np.concatenate(frames).astype(np.float32) / 32768.0

    def transmit(self):
        try:
            self.cue(self.open_cue)
            audio = self.record()
            self.cue(self.close_cue)
            if audio is None:
                print("Radio: heard nothing", flush=True)
                return
            text = str(self.model.recognize(audio, sample_rate=RATE)).strip()
            if not text:
                print("Radio: could not make that out", flush=True)
                return
            print(f"Driver: {text}", flush=True)
            req = urllib.request.Request(
                f"{self.url}/api/radio", data=json.dumps({"text": text}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                print(f"Engineer: {json.loads(resp.read()).get('reply', '')}", flush=True)
        except Exception as e:
            print(f"Radio failed: {e}", flush=True)
        finally:
            self.busy = False

    def listen(self, device: str, code: int):
        while True:
            devices = open_devices(device)
            if not devices:
                print(f"Waiting for an input device matching {device!r}...", flush=True)
                time.sleep(3)
                continue
            selector = selectors.DefaultSelector()
            for fd, name in devices.items():
                selector.register(fd, selectors.EVENT_READ, name)
            print(f"Listening for code {code} on {', '.join(sorted(set(devices.values())))}", flush=True)
            for _, pressed_code in presses(selector):
                if pressed_code == code:
                    self.pressed()
            print("Input device gone, looking for it again", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--learn", action="store_true", help="print the device and code of the next button press")
    ap.add_argument("--device", default=os.getenv("PTT_DEVICE", ""), help="input device name, or part of it")
    ap.add_argument("--code", type=int, default=int(os.getenv("PTT_CODE", "0") or 0), help="key or button code")
    ap.add_argument("--url", default=os.getenv("BOXBOX_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--model", default=os.getenv("PTT_MODEL", "nemo-parakeet-tdt-0.6b-v2"))
    args = ap.parse_args()
    if args.learn:
        learn()
        return
    if not args.device or not args.code:
        sys.exit("Set PTT_DEVICE and PTT_CODE (run with --learn to find them)")
    Radio(args.url, args.model).listen(args.device, args.code)


if __name__ == "__main__":
    main()
