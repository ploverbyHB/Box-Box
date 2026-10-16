"""
Sends what the engineer says to an external caption display.

CAPTIONS_URL receives a JSON POST: {"text", "label", "color", "duration_ms", "kind", "lap"}.
CAPTIONS_COMMAND runs a local command. {text}, {label}, {color}, {duration_ms}, {kind} and {lap}
are replaced inside each argument after the command is split, so they need no quoting.
The label and colour follow the player's team unless CAPTIONS_LABEL or CAPTIONS_COLOR is set.
"""
import asyncio
import os
import shlex

import aiohttp
from dotenv import load_dotenv

load_dotenv()

CAPTIONS_URL = os.getenv("CAPTIONS_URL", "").strip()
CAPTIONS_COMMAND = os.getenv("CAPTIONS_COMMAND", "").strip()
CAPTIONS_LABEL = os.getenv("CAPTIONS_LABEL", "").strip()
CAPTIONS_COLOR = os.getenv("CAPTIONS_COLOR", "").strip()
# 0 leaves the display to time the caption to the text length
CAPTIONS_DURATION_MS = int(os.getenv("CAPTIONS_DURATION_MS", "0"))
TIMEOUT_S = 3.0

_argv = shlex.split(CAPTIONS_COMMAND, posix=os.name != "nt") if CAPTIONS_COMMAND else []


def describe() -> str:
    targets = [f"POST {CAPTIONS_URL}"] if CAPTIONS_URL else []
    if _argv:
        targets.append(f"command {_argv[0]}")
    return ", ".join(targets) or "off"


async def show(text: str, kind: str = "engineer", lap: int | None = None, label: str = "", color: str = ""):
    text = (text or "").strip()
    if not text or not (CAPTIONS_URL or _argv):
        return
    fields = {
        "text": text,
        "label": CAPTIONS_LABEL or label or "Race Engineer",
        "color": CAPTIONS_COLOR or color,
        "duration_ms": CAPTIONS_DURATION_MS,
        "kind": kind,
        "lap": lap,
    }
    await asyncio.gather(_post(fields), _run(fields))


async def _post(fields: dict):
    if not CAPTIONS_URL:
        return
    try:
        timeout = aiohttp.ClientTimeout(total=TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(CAPTIONS_URL, json=fields) as resp:
                if resp.status >= 400:
                    print(f"Captions endpoint returned HTTP {resp.status}")
    except Exception as e:
        print(f"Captions endpoint failed: {e}")


async def _run(fields: dict):
    if not _argv:
        return
    values = {k: "" if v is None else str(v) for k, v in fields.items()}
    argv = []
    for arg in _argv:
        for key, value in values.items():
            arg = arg.replace("{" + key + "}", value)
        argv.append(arg)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            print("Captions command timed out")
            return
        if proc.returncode:
            print(f"Captions command exited {proc.returncode}: {err.decode(errors='ignore').strip()[:200]}")
    except Exception as e:
        print(f"Captions command failed: {e}")
