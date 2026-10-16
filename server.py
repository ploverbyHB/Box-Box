"""
Telemetry → AI provider analysis → TTS → broadcast to frontend
Supports: UDP (PS5) + WebSocket (simulator fallback)
"""
import asyncio, json, time, os, webbrowser, threading, urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from analyzer import provider, radio_call, strategy_call
import tts
from tts import synthesize
import captions
from f1 import F1Accumulator
from race import EngineerMemory, RaceState, Trigger, radio_context, strategy_context, with_undone_calls
from race.teams import caption_style

load_dotenv()

UDP_HOST = os.getenv("UDP_HOST", "0.0.0.0")
GT7_PORT = int(os.getenv("GT7_PORT", "33740"))
F1_PORT = int(os.getenv("F1_PORT", "20777"))
F1_UDP_FORMAT = os.getenv("F1_UDP_FORMAT", "auto")
UDP_TIMEOUT = 5.0
UDP_DEBUG = os.getenv("UDP_DEBUG", "0") == "1"
UDP_DUMP = os.getenv("UDP_DUMP", "0") == "1"

pit_radios = []
_last_advice = ""
_last_udp_time = 0.0
_last_frame_time = 0.0
_fallback = False
race_state = RaceState()
memory = EngineerMemory()
_pending = []
_strategy_running = False
_active_sim = None

BASE_DIR = Path(__file__).parent
dump_dir = BASE_DIR / "udp_dump"
_dump_count = 0

f1_acc = F1Accumulator(F1_UDP_FORMAT)

ENGINEER_FILE = BASE_DIR / "engineer.json"


def _load_engineer_on() -> bool:
  try:
    return bool(json.loads(ENGINEER_FILE.read_text()).get("on", True))
  except (OSError, ValueError):
    return True


_engineer_on = _load_engineer_on()


class Gt7Protocol(asyncio.DatagramProtocol):
  def datagram_received(self, data, addr):
    global _dump_count
    try:
      text = data.decode("utf-8")
      if UDP_DEBUG:
        print(f"[GT7 RAW] {text[:200]}")
      if UDP_DUMP:
        _dump_count += 1
        fname = dump_dir / f"gt7_{_dump_count:06d}.json"
        fname.write_text(text, encoding="utf-8")
      frame = json.loads(text)
      asyncio.create_task(process_frame(frame, "udp"))
    except Exception as e:
      print(f"GT7 UDP parse error: {e}")


class F1Protocol(asyncio.DatagramProtocol):
  def __init__(self):
    self._pkt_counts = {}
    self._last_summary = 0.0
    self._frames = 0

  def datagram_received(self, data, addr):
    global _dump_count
    try:
      packet_id = data[6] if len(data) > 6 else -1
      self._pkt_counts[packet_id] = self._pkt_counts.get(packet_id, 0) + 1
      if UDP_DEBUG:
        print(f"[F1 RAW] from={addr[0]} len={len(data)} packetId={packet_id}")
      if UDP_DUMP:
        dump_dir.mkdir(exist_ok=True)
        _dump_count += 1
        fname = dump_dir / f"f1_{_dump_count:06d}.bin"
        fname.write_bytes(data)
      result = f1_acc.feed(data)
      if result and result.get("cars"):
        self._frames += 1
        asyncio.create_task(process_frame(result, "udp"))
      now = time.time()
      if now - self._last_summary >= 5.0:
        self._last_summary = now
        counts = ", ".join(f"id{k}:{v}" for k, v in sorted(self._pkt_counts.items()))
        print(f"[F1 UDP] from={addr[0]} 5s packets=[{counts}] ui_frames={self._frames}")
        self._pkt_counts.clear()
        self._frames = 0
    except Exception as e:
      print(f"F1 UDP parse error: {e}")


@asynccontextmanager
async def lifespan(_app):
  loop = asyncio.get_running_loop()
  print(f"AI provider: {provider.label} ({provider.model})")
  print(f"Voice: {tts.describe()}")
  await tts.provider.voices()
  print(f"Captions: {captions.describe()}")
  print(f"Engineer: {'on' if _engineer_on else 'OFF (switch it on from the page)'}")
  print("=== F1 UDP setup ===")
  print(f"Listening F1 telemetry on UDP {UDP_HOST}:{F1_PORT} (format: {F1_UDP_FORMAT})")
  print("On PS5 F1 24 / F1 25 Settings → Telemetry:")
  print("  UDP Telemetry = On")
  print("  UDP Broadcast / IP = this PC's LAN IP (same Wi‑Fi/LAN as PS5)")
  print(f"  UDP Port = {F1_PORT}")
  print("  UDP Send Rate = 20Hz or higher")
  print("  Then enter a session ON TRACK (menu only sends Event packets).")
  print("====================")
  for name, port, proto in [
    ("GT7", GT7_PORT, Gt7Protocol),
    ("F1", F1_PORT, F1Protocol),
  ]:
    try:
      await loop.create_datagram_endpoint(proto, local_addr=(UDP_HOST, port))
      print(f"UDP listening ({name}) on {UDP_HOST}:{port}")
    except Exception as e:
      print(f"UDP {name} bind failed (port {port}): {e}")
  yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/audio", StaticFiles(directory=BASE_DIR / "static/audio"), name="audio")


async def _broadcast(payload: dict):
  dead = []
  for client in pit_radios.copy():
    try:
      await client.send_json(payload)
    except Exception:
      dead.append(client)
  for client in dead:
    try:
      pit_radios.remove(client)
    except ValueError:
      pass


async def _speak(text: str) -> str | None:
  """Synthesize a line; returns the URL for the dashboard, or None when it plays on this machine."""
  url = await synthesize(text)
  if not _engineer_on:
    return None
  if url and tts.plays_locally():
    asyncio.create_task(tts.play(url))
    return None
  return url


def _submit(triggers: list):
  """Queue triggers; one strategy call runs at a time and absorbs everything that arrived meanwhile."""
  for t in triggers:
    if not any(p.code == t.code and p.detail == t.detail for p in _pending):
      _pending.append(t)
  if not _strategy_running and any(t.urgent or t.code == "LAP" for t in _pending):
    asyncio.create_task(_strategy_loop())


async def _strategy_loop():
  global _strategy_running, _pending, _last_advice
  _strategy_running = True
  try:
    while any(t.urgent or t.code == "LAP" for t in _pending):
      reasons, _pending = _pending, []
      session, lap = race_state.session_uid, race_state.lap
      flashbacks, called_at = race_state.flashbacks, race_state.time
      context = strategy_context(race_state, memory, reasons)
      started = time.time()
      result = await asyncio.to_thread(strategy_call, context)
      # A new session or a flashback while the call ran makes its answer stale
      if race_state.session_uid != session or race_state.flashbacks != flashbacks or not _engineer_on:
        continue
      if ((race_state.player() or {}).get("pitStatus") or "NONE") != "NONE":
        result["box_this_lap"] = False
      memory.record_call(lap, reasons, result, called_at)
      if result["advice"]:
        _last_advice = result["advice"]
      audio_url = await _speak(result["voice"]) if result["voice"] else None
      if result["voice"] and _engineer_on:
        label, color = _caption_style()
        asyncio.create_task(captions.show(result["voice"], "strategy", lap, label, color))
      plan = result["plan"] or {}
      print(f"[ENGINEER] L{lap} {'/'.join(r.code for r in reasons)} in {time.time() - started:.1f}s"
            f"{' BOX ' + plan.get('next_compound', '') if result['box_this_lap'] else ''}"
            f" | {result['voice'] or '(quiet)'} | plan: {plan.get('summary', '')}")
      await _broadcast({
        "type": "strategy",
        "lap": lap,
        "reasons": [r.code for r in reasons],
        "advice": result["advice"],
        "voice": result["voice"],
        "audio_url": audio_url,
        "box_this_lap": result["box_this_lap"],
        "plan": result["plan"],
      })
  finally:
    _strategy_running = False


async def process_frame(data: dict, source: str):
  global _last_udp_time, _last_frame_time, _fallback, _last_advice

  if source == "udp":
    _last_udp_time = time.time()
    if _fallback:
      _fallback = False
      print("Switched to UDP (live data)")
  else:
    if time.time() - _last_udp_time < UDP_TIMEOUT:
      return

  _last_frame_time = time.time()
  triggers, new_session = race_state.update(data)
  if new_session:
    memory.reset_race()
    _pending.clear()
    _last_advice = ""
    tts.stop()
    print(f"New session {race_state.session_uid or '(no id)'}: race memory cleared")

  flashback = next((t for t in triggers if t.code == "FLASHBACK"), None)
  if flashback:
    tts.stop()
    _pending.clear()
    undone = memory.rewind(race_state.rewound_to)
    triggers = [with_undone_calls(t, undone) if t is flashback else t for t in triggers]
    print(f"Flashback to {race_state.rewound_to:.1f}s: {len(undone)} engineer call(s) undone")

  await _broadcast({
    "advice": _last_advice,
    "voice": "",
    "audio_url": None,
    "cars": data.get("cars", []),
    "track": data.get("track", {"id": -1, "name": ""}),
    "player_idx": data.get("player_idx", -1),
    "lap": data.get("lap", 0),
    "corners": race_state.corners.labels(),
  })

  if triggers and _engineer_on:
    _submit(triggers)


@app.get("/favicon.ico")
async def favicon():
  return Response(status_code=204)


@app.get("/")
async def get_index():
  return HTMLResponse((BASE_DIR / "static/index.html").read_text(encoding="utf-8"))


class RadioRequest(BaseModel):
  text: str


@app.post("/api/radio")
async def api_radio(req: RadioRequest):
  """Driver speaks to the race engineer; reply is spoken back via TTS."""
  text = (req.text or "").strip()
  if not text:
    return {"ok": False, "error": "empty message"}
  if not _engineer_on:
    return {"ok": False, "error": "the engineer is switched off"}

  corners = await asyncio.to_thread(race_state.corners.report, race_state.player_idx)
  context = radio_context(race_state, memory, text, corners)
  result = await asyncio.to_thread(radio_call, context)
  if not _engineer_on:
    return {"ok": False, "error": "the engineer is switched off"}
  voice = reply = result["voice"]
  memory.record_radio(race_state.lap, text, voice)
  if memory.add_preference(result["remember"]):
    if race_state.is_race() and race_state.started:
      _submit([Trigger("DRIVER", f'{result["remember"]}; you already answered on the radio ("{voice}"): the plan must match that answer, '
                                 f'and voice stays "" unless the driver must act now')])
  audio_url = await _speak(voice) if voice else None
  label, color = _caption_style()
  if _engineer_on:
    asyncio.create_task(captions.show(voice, "radio", race_state.lap, label, color))

  print(f"[RADIO] Driver: {text}")
  print(f"[RADIO] Engineer: {voice}" + (f" (remembered: {result['remember']})" if result["remember"] else ""))

  await _broadcast({
    "type": "radio",
    "driver": text,
    "advice": reply,
    "voice": voice,
    "audio_url": audio_url,
  })

  return {
    "ok": True,
    "driver": text,
    "reply": reply,
    "voice": voice,
    "audio_url": audio_url,
  }


def _caption_style() -> tuple[str, str]:
  """The speaking voice's name when the voice is a known person, else the team's engineer; the team's colour."""
  label, color = caption_style(race_state.player())
  return tts.provider.speaker_name() or label, color


class EngineerRequest(BaseModel):
  on: bool


def _engineer_status() -> dict:
  on_track = time.time() - _last_frame_time < 3.0
  return {"on": _engineer_on, "on_track": on_track, "listening": _engineer_on and on_track}


@app.get("/api/engineer")
async def api_engineer():
  return _engineer_status()


@app.post("/api/engineer")
async def api_engineer_set(req: EngineerRequest):
  """Switch the whole engineer on or off: strategy calls, radio replies, voice and captions."""
  global _engineer_on
  _engineer_on = req.on
  try:
    ENGINEER_FILE.write_text(json.dumps({"on": _engineer_on}))
  except OSError as e:
    print(f"Could not save the engineer switch: {e}")
  if not _engineer_on:
    _pending.clear()
    tts.stop()
  print(f"Engineer switched {'on' if _engineer_on else 'off'}")
  status = _engineer_status()
  await _broadcast({"type": "engineer", **status})
  return status


class VoiceRequest(BaseModel):
  id: str


@app.get("/api/voices")
async def api_voices():
  return {"provider": tts.provider.label, "current": tts.provider.voice_id, "voices": await tts.provider.voices()}


@app.post("/api/voice")
async def api_voice(req: VoiceRequest):
  """Switch the engineer's voice for this session and answer with a radio check in it."""
  voice_id = (req.id or "").strip()
  if not voice_id:
    return {"ok": False, "error": "empty voice id"}
  previous = tts.provider.voice_id
  tts.provider.set_voice(voice_id)
  await tts.provider.voices()
  line = "Radio check. You'll hear me on this channel for the rest of the race."
  audio_url = await synthesize(line)
  if not audio_url:
    tts.provider.set_voice(previous)
    return {"ok": False, "error": f"{tts.provider.label} could not speak with voice {voice_id}"}
  if tts.plays_locally() or not _engineer_on:
    if _engineer_on:
      asyncio.create_task(tts.play(audio_url))
    audio_url = None
  label, color = _caption_style()
  if _engineer_on:
    asyncio.create_task(captions.show(line, "radio", race_state.lap, label, color))
  print(f"Voice switched to {label} ({voice_id})")
  return {"ok": True, "voice": voice_id, "name": label, "audio_url": audio_url}


@app.websocket("/ws/telemetry")
async def telemetry_ws(websocket: WebSocket):
  global _active_sim
  await websocket.accept()
  # Two simulators would feed two different races into one race state
  if _active_sim is not None:
    print("A simulator is already connected; refusing a second one")
    await websocket.close(code=1013)
    return
  _active_sim = websocket
  print("Simulator connected (fallback mode)")
  try:
    async for raw in websocket.iter_text():
      frame = json.loads(raw)
      await process_frame(frame, "simulator")
  except Exception as e:
    print(f"Simulator WS error: {e}")
  finally:
    if _active_sim is websocket:
      _active_sim = None


@app.websocket("/ws/pitradio")
async def pitradio_ws(websocket: WebSocket):
  global pit_radios
  await websocket.accept()
  pit_radios.append(websocket)
  print(f"Frontend connected ({len(pit_radios)} online)")
  try:
    await websocket.receive_text()
  except Exception:
    pass
  finally:
    if websocket in pit_radios:
      pit_radios.remove(websocket)


def _open_browser():
  try:
    urllib.request.urlopen("http://127.0.0.1:8000", timeout=1)
    webbrowser.open("http://127.0.0.1:8000")
  except Exception:
    threading.Timer(2, _open_browser).start()


if __name__ == "__main__":
  threading.Timer(1, _open_browser).start()
  import uvicorn
  uvicorn.run("server:app", host="127.0.0.1", port=8000)
