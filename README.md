# Box-Box World's First AI F1 Race Engineer

Box-Box is an AI-powered race engineer for home sim racing. It captures real-time telemetry, analyzes vehicle performance and race conditions, and turns them into contextual strategy recommendations. Telemetry processing, AI reasoning, and conversational voice output form a continuous loop between the virtual race engineer and the driver, bringing human-AI collaboration closer to a real motorsport engineering experience.

## What it does

- Listens for live **F1 24** and **F1 25** UDP telemetry, including the 2026 season pack's 24-car format, and **Gran Turismo 7** JSON telemetry.
- Reads the safety car and VSC, flags, pit stops, race length, the rain forecast, damage, and every car's lap times.
- Makes a strategy call after every lap, and immediately on a safety car, rain, damage, a puncture, a rival's stop, a retirement, or a driver request.
- Works out the pit loss, where the driver would rejoin, and the race time of every 0, 1 and 2 stop plan before the AI decides.
- Knows the driver's team and teammate, and avoids double-stacking in the shared pit box.
- Remembers every call and radio exchange of the race, and asks the driver when a decision depends on how they want to race.
- Asks DeepSeek, Cerebras, or Claude to decide and speaks the call with ElevenLabs or Fish Audio.
- Lets the driver talk back on pit radio. The engineer answers in text and voice.
- Shows a live dashboard: standings, track map, selected-car data, and the radio log.
- Falls back to a 22-car simulator when a game is not connected.

## Run it

Python 3.10 or newer.

```bat
python -m venv venv
venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

Put your AI provider key (DeepSeek, Cerebras, or Anthropic) and your speech key (ElevenLabs or Fish Audio) in `.env`, then start the server and the simulator:

```bat
start.bat
```

On PowerShell, use `.\start.ps1`. The dashboard opens at http://127.0.0.1:8000.

To run the pieces yourself:

```bat
venv\Scripts\python.exe server.py
venv\Scripts\python.exe simulator.py
```

## Connect F1 24 or F1 25

In the game, open Settings → Telemetry:

- UDP Telemetry: On
- UDP Broadcast / IP: this PC's LAN address, on the same network as the console
- UDP Port: `20777`
- UDP Send Rate: 20Hz or higher
- UDP Format (F1 25): `2026` for the 2026 season pack, `2025` or `2024`

Enter a session on track. The menus only send event packets. Box-Box reads the format from each packet's header. Set `F1_UDP_FORMAT` to `2024`, `2025` or `2026` to force one layout. Gran Turismo 7 telemetry is accepted as JSON on UDP port `33740`.

## Pit radio

Click **MIC** or press Space to talk. Ask about tires, the pit window, gaps, or fuel. The reply is played through the page.

### Push-to-talk on a wheel button or key (Linux)

`radio_ptt.py` opens the radio from any key or wheel button while the game has focus. One press opens the channel with a beep, it closes when you stop talking (or on a second press), and the speech is transcribed locally with Parakeet and sent to the engineer.

```sh
python radio_ptt.py --learn   # press the button; prints PTT_DEVICE and PTT_CODE for .env
python radio_ptt.py
```

It needs read access to `/dev/input` (the `input` group), PipeWire's `pw-record` and `pw-play`, and `numpy` and `onnx-asr` (see `requirements-ptt.txt`).

The voice picker next to the mic switches the engineer's voice for the session and answers with a radio check. It lists the ElevenLabs account's voices, or the Fish Audio models in `FISH_AUDIO_REFERENCE_ID` and `FISH_AUDIO_VOICES`; "Other voice id…" takes any id. When the voice is a named person on Fish Audio, captions carry that name.

Tell the engineer how you want to race, for example "I'd rather one-stop" or "keep me out, I'm saving tyres", and it makes a new strategy call with that in mind for the rest of the race. When a decision depends on how you want to race and you haven't said, it asks.

## External captions

Everything the engineer says, on strategy calls and radio replies, can also go to a caption display. Set either or both in `.env`:

```ini
CAPTIONS_URL=http://127.0.0.1:9000/caption
CAPTIONS_COMMAND=/path/to/caption -l {label} -t {duration_ms} {text}
```

The URL receives `{"text": "...", "label": "James Urwin", "color": "#64C4FF", "duration_ms": 0, "kind": "strategy", "lap": 12}`; `kind` is `strategy` or `radio`. The command's placeholders, `{text}`, `{label}`, `{color}`, `{duration_ms}`, `{kind}` and `{lap}`, are filled in after it is split into arguments, so they need no quotes.

The label is the race engineer of the driver's team and the colour is the team's colour (`race/teams.py`). Teams without a listed engineer use "Race Engineer"; custom teams use the game's livery colour.

## Simulator and strategy tests

`simulator.py` runs a 22-car race at Silverstone and sends it to the server. The player's car pits only when the engineer calls it in.

```bat
venv\Scripts\python.exe simulator.py --scenario safety-car --laps 30 --speed 5
```

Scenarios: `normal`, `safety-car`, `vsc`, `rain`, `damage`, `puncture`, `high-deg`, `undercut`, `double-stack`, `chaos`. `--speed` runs the race faster than real time, `--seed` picks a repeatable race, and `--headless` prints a race without a server.

`race_test.py` runs a scenario without the server or the dashboard, with the real engineer making the calls, and prints every call and the lap-by-lap race. The race keeps running while each call is answered.

```bat
venv\Scripts\python.exe race_test.py --scenario rain --say "3:I'd rather one-stop"
venv\Scripts\python.exe race_test.py --scenario rain --baseline
```

`--say LAP:TEXT` sends a radio message on a lap, and `--baseline` runs a fixed one-stop with no engineer for comparison.

## Configuration

Copy `.env.example` to `.env`.

| Variable | Purpose |
| --- | --- |
| `AI_PROVIDER` | `deepseek`, `cerebras` or `claude`. Defaults to `deepseek` |
| `DEEPSEEK_API_KEY` | DeepSeek API key |
| `DEEPSEEK_MODEL` | DeepSeek model. Defaults to `deepseek-v4-flash` |
| `DEEPSEEK_REASONING_EFFORT` | `none` (thinking off), `low`, `high` or `max`. Defaults to `low` |
| `DEEPSEEK_BASE_URL` | OpenAI-compatible API base URL. Defaults to `https://api.deepseek.com` |
| `CEREBRAS_API_KEY` | Cerebras API key |
| `CEREBRAS_MODEL` | Cerebras model. Defaults to `qwen-3.8-27b` |
| `CEREBRAS_REASONING_EFFORT` | `none`, `low`, `medium` or `high`. Defaults to `none`; `gpt-oss-120b` needs `low` or higher |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `ANTHROPIC_WORKSPACE_ID` | Workspace for keys that are not scoped to one |
| `ANTHROPIC_MODEL` | Claude model. Defaults to `claude-haiku-5-5` |
| `ANTHROPIC_REASONING_EFFORT` | `low`, `medium`, `high`, `xhigh` or `max`. Defaults to `low` |
| `TTS_PROVIDER` | `elevenlabs` or `fishaudio`. Defaults to `elevenlabs` |
| `ELEVENLABS_API_KEY` | ElevenLabs key used for pit-radio voice |
| `ELEVENLABS_VOICE_ID` | Spoken voice. A default is used when this is empty, and a premade voice when the voice is not in your ElevenLabs account |
| `AUDIO_PLAY_COMMAND` | Command that plays each line on this machine instead of in the dashboard, with a `{file}` placeholder, e.g. `pw-play {file}` |
| `PTT_DEVICE` | Input device for `radio_ptt.py`, or part of its name |
| `PTT_CODE` | Key or button code for `radio_ptt.py` (`--learn` prints it) |
| `CAPTIONS_URL` | Endpoint that receives each spoken line as a JSON POST |
| `CAPTIONS_COMMAND` | Command run for each spoken line, with `{text}`, `{label}`, `{color}`, `{duration_ms}`, `{kind}` and `{lap}` placeholders |
| `CAPTIONS_LABEL` | Fixed caption label. Defaults to the team's race engineer |
| `CAPTIONS_COLOR` | Fixed caption colour (`#RRGGBB`). Defaults to the team's colour |
| `CAPTIONS_DURATION_MS` | Caption duration sent to the display. Defaults to `0`, which leaves it to the display |
| `ELEVENLABS_MODEL_ID` | Speech model. Defaults to `eleven_flash_v2_5` |
| `FISH_AUDIO_API_KEY` | Fish Audio API key |
| `FISH_AUDIO_REFERENCE_ID` | Fish Audio voice model id |
| `FISH_AUDIO_VOICES` | More Fish Audio model ids, comma-separated, for the dashboard's voice picker |
| `FISH_AUDIO_MODEL` | Fish Audio model. Defaults to `s2.1-pro`; accounts without API credit use `s2.1-pro-free` |
| `FISH_AUDIO_LATENCY` | `low`, `balanced` or `normal`. Defaults to `normal` |
| `F1_PORT` | F1 UDP port. Defaults to `20777` |
| `F1_UDP_FORMAT` | `auto`, or a registered format: `2024` (F1 24), `2025` (F1 25), `2026` (F1 25 2026 season pack). Defaults to `auto` |
| `GT7_PORT` | GT7 UDP port. Defaults to `33740` |

## Layout

| File | Role |
| --- | --- |
| `server.py` | FastAPI app, UDP listeners, dashboard, and radio API |
| `f1/parser.py` | F1 UDP packet parser for all 22 cars |
| `f1/f1_24/`, `f1/f1_25/`, `f1/f1_26/` | Per-format packet layouts, car counts, track, team and compound ids |
| `providers/base.py` | Provider interface and registry |
| `providers/openai_compatible.py` | Chat call shared by the OpenAI-compatible providers |
| `providers/deepseek/`, `providers/cerebras/`, `providers/claude/` | AI providers |
| `registry.py` | Name registry the F1 formats, AI providers and speech providers register into |
| `race/state.py` | Lap history, stints, race log, and the triggers for strategy calls |
| `race/pit.py`, `race/pace.py`, `race/planner.py` | Pit loss and rejoin, pace and degradation, stop planning |
| `race/memory.py` | Calls, radio, plan, notes, and what the driver asked for this race |
| `race/context.py` | Text the AI reads for each call |
| `race/teams.py` | Team colours and race engineers for captions |
| `engine_models.py` | Tire, fuel, weather, and opponent models |
| `analyzer.py` | Strategy and radio prompts and calls |
| `tts/__init__.py` | Speech for the engineer's lines, written to `static/audio` for the dashboard |
| `tts/elevenlabs/`, `tts/fishaudio/` | Speech providers |
| `captions.py` | Sends spoken lines to an external caption display |
| `simulator.py` | Race simulator with scenarios, sent to the server over WebSocket |
| `race_test.py` | Headless scenario runs with the engineer in the loop |
| `radio_ptt.py` | Push-to-talk from a key or wheel button, transcribed locally |
| `static/index.html` | Live dashboard |
