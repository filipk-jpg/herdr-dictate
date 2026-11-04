#!/usr/bin/env python3
"""
Dictate: push-to-talk speech-to-text for Herdr panes.

Press the bound key once to start recording and again to stop. The audio is
transcribed locally with faster-whisper, and the text goes into the pane you
started in, without pressing Enter.

Herdr runs this script as a plugin action. The first run creates a private
virtualenv in the plugin's state directory and downloads the speech model.
After that, each start/stop press is one short-lived process: the "start"
press records until a later press drops a stop file, then transcribes and
sends the text.

Only the standard library is imported at module level so the toggle and setup
paths work with any Python 3.9+. Audio and Whisper imports happen inside the
virtualenv.
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

# ----------------------------------------------------------------------------
# Paths and constants
# ----------------------------------------------------------------------------

SCRIPT = Path(__file__).resolve()
PLUGIN_ROOT = Path(os.environ.get("HERDR_PLUGIN_ROOT") or SCRIPT.parent)
STATE_DIR = Path(os.environ.get("HERDR_PLUGIN_STATE_DIR") or PLUGIN_ROOT / ".state")
CONFIG_DIR = Path(os.environ.get("HERDR_PLUGIN_CONFIG_DIR") or PLUGIN_ROOT)
HERDR = os.environ.get("HERDR_BIN_PATH") or "herdr"

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0

VENV = STATE_DIR / "venv"
VENV_PYTHON = VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
MODELS_DIR = STATE_DIR / "models"
SESSION_FILE = STATE_DIR / "session.json"
STOP_FILE = STATE_DIR / "stop"
SETUP_LOG = STATE_DIR / "setup.log"
CONFIG_FILE = CONFIG_DIR / "config.toml"

REQUIREMENTS = ["faster-whisper>=1.0", "sounddevice>=0.4", "numpy"]
DEPS_MARKER = STATE_DIR / "deps-v1"  # bump the suffix when REQUIREMENTS change

SAMPLE_RATE = 16000
MIN_SECONDS = 0.4
HEARTBEAT_SECONDS = 1.0
STALE_AFTER_SECONDS = 8.0
# Whisper sometimes invents these on silence.
HALLUCINATIONS = {"", "you", "thank you.", "thanks for watching!", "thank you for watching."}

DEFAULT_CONFIG = {
    "model": "small.en",
    "language": "",
    "device": "cpu",
    "press_enter": False,
    "max_seconds": 120,
    "sounds": True,
    "vocabulary": (
        "Herdr, Claude Code, Codex, OpenCode, TypeScript, JavaScript, Python, Rust, "
        "React, Next.js, npm, pnpm, git, GitHub, API, JSON, README, refactor, endpoint."
    ),
}

CONFIG_TEMPLATE = """\
# Dictate plugin settings. Delete a line to go back to the default.

# Whisper model: tiny.en, base.en (fastest), small.en (default), medium.en (most accurate).
# Use a name without ".en" (e.g. "small") to dictate in other languages.
model = "small.en"

# Language code for models without ".en", e.g. "de". Empty = auto-detect.
language = ""

# "cpu", or "cuda" with an NVIDIA GPU set up for faster-whisper.
device = "cpu"

# Press Enter after typing the text.
press_enter = false

# Stop recording automatically after this many seconds.
max_seconds = 120

# Short sounds when recording starts and stops (Windows and macOS).
sounds = true

# Words Whisper should expect: project names, libraries, people.
vocabulary = "Herdr, Claude Code, Codex, OpenCode, TypeScript, JavaScript, Python, Rust, React, Next.js, npm, pnpm, git, GitHub, API, JSON, README, refactor, endpoint."
"""


# ----------------------------------------------------------------------------
# Helpers (standard library only)
# ----------------------------------------------------------------------------

def log(message):
    # Herdr keeps stdout in its plugin command log: herdr plugin log list --plugin <id>
    print(time.strftime("[%H:%M:%S] ") + message, flush=True)


def herdr(*args, timeout=15):
    try:
        result = subprocess.run(
            [HERDR, *args], capture_output=True, text=True,
            timeout=timeout, creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"herdr {args[0]} {args[1] if len(args) > 1 else ''} failed: {exc}")
        return None
    if result.returncode != 0:
        log(f"herdr {' '.join(args[:2])} exited {result.returncode}: {result.stderr.strip()}")
    return result


def notify(title, body=None):
    args = ["notification", "show", title]
    if body:
        args += ["--body", body]
    herdr(*args)


def play(kind, config):
    if not config.get("sounds", True):
        return
    try:
        if IS_WINDOWS:
            import winsound
            winsound.Beep(880 if kind == "start" else 520, 80)
        elif IS_MACOS:
            sound = "Tink" if kind == "start" else "Pop"
            subprocess.Popen(["afplay", f"/System/Library/Sounds/{sound}.aiff"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def load_config():
    config = dict(DEFAULT_CONFIG)
    if not CONFIG_FILE.exists():
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        except OSError:
            pass
        return config
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        log("config.toml needs Python 3.11+ to read; using defaults")
        return config
    try:
        with open(CONFIG_FILE, "rb") as handle:
            config.update(tomllib.load(handle))
    except (OSError, ValueError) as exc:
        notify("Dictate: config error", f"{CONFIG_FILE}: {exc}")
    return config


def read_session():
    """The running dictation, or None. A session whose heartbeat stopped is stale."""
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if time.time() - data.get("beat", 0) > STALE_AFTER_SECONDS:
        return None
    return data


class Session:
    """Writes session.json every second while this process is working, so a
    second key press knows whether to stop a recording or wait."""

    def __init__(self, phase, pane=None):
        self.data = {"pid": os.getpid(), "pane": pane, "phase": phase, "started": time.time()}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat_loop, daemon=True)

    def set_phase(self, phase):
        self.data["phase"] = phase
        self._write()

    def _write(self):
        self.data["beat"] = time.time()
        tmp = SESSION_FILE.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self.data), encoding="utf-8")
            os.replace(tmp, SESSION_FILE)
        except OSError:
            pass

    def _beat_loop(self):
        while not self._stop.wait(HEARTBEAT_SECONDS):
            self._write()

    def __enter__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._write()
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2)
        for path in (SESSION_FILE, STOP_FILE):
            try:
                path.unlink()
            except OSError:
                pass
        return False


# ----------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------

def toggle():
    session = read_session()
    if session:
        if session.get("phase") == "recording":
            STOP_FILE.write_text("", encoding="utf-8")
            log("stop requested")
        elif session.get("phase") == "setup":
            notify("Dictate is still setting up", "This only happens once. Hang on.")
        else:
            notify("Dictate", "Still transcribing the last clip.")
        return 0

    if not (VENV_PYTHON.exists() and DEPS_MARKER.exists()):
        return setup()

    pane = os.environ.get("HERDR_PANE_ID")
    if not pane:
        notify("Dictate", "No focused pane. Run it from a pane keybinding.")
        return 1
    result = subprocess.run([str(VENV_PYTHON), str(SCRIPT), "record", pane],
                            creationflags=NO_WINDOW)
    return result.returncode


def setup():
    notify("Dictate: one-time setup",
           "Installing the speech model. This takes a few minutes; you'll get a message when it's ready.")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    steps = []
    if not VENV_PYTHON.exists():
        steps.append([sys.executable, "-m", "venv", str(VENV)])
    steps += [
        [str(VENV_PYTHON), "-m", "pip", "install", "--disable-pip-version-check", *REQUIREMENTS],
        [str(VENV_PYTHON), str(SCRIPT), "warmup"],  # downloads the model
    ]
    with Session("setup"), open(SETUP_LOG, "w", encoding="utf-8") as out:
        for step in steps:
            out.write(f"$ {' '.join(step)}\n")
            out.flush()
            code = subprocess.run(step, stdout=out, stderr=subprocess.STDOUT,
                                  creationflags=NO_WINDOW).returncode
            if code != 0:
                log(f"setup step failed ({code}); see {SETUP_LOG}")
                notify("Dictate: setup failed", f"Details in {SETUP_LOG}")
                return 1
    DEPS_MARKER.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    log("setup complete")
    notify("Dictate is ready", "Press your dictate key to start talking, and again to stop.")
    return 0


def load_model(config):
    from faster_whisper import WhisperModel
    device = config.get("device", "cpu")
    compute = "float16" if device == "cuda" else "int8"
    return WhisperModel(config["model"], device=device, compute_type=compute,
                        download_root=str(MODELS_DIR))


def warmup():
    config = load_config()
    print(f"Downloading Whisper model '{config['model']}'...", flush=True)
    load_model(config)
    print("Model ready.", flush=True)
    return 0


def record(pane):
    import numpy as np
    import sounddevice as sd

    config = load_config()
    try:
        STOP_FILE.unlink()
    except OSError:
        pass

    with Session("recording", pane) as session:
        # Load the model while the user talks, so it's ready when they stop.
        loaded = {}

        def load():
            try:
                loaded["model"] = load_model(config)
            except Exception as exc:  # reported after recording
                loaded["error"] = exc

        loader = threading.Thread(target=load, daemon=True)
        loader.start()

        chunks = []
        try:
            stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                    callback=lambda data, *_: chunks.append(data.copy()))
            stream.start()
        except Exception as exc:
            notify("Dictate: microphone error", str(exc))
            return 1

        play("start", config)
        notify("Listening...", "Press the dictate key again to stop.")
        started = time.time()
        max_seconds = float(config.get("max_seconds", 120))
        while not STOP_FILE.exists() and time.time() - started < max_seconds:
            time.sleep(0.05)
        stream.stop()
        stream.close()
        play("stop", config)
        session.set_phase("transcribing")

        audio = np.concatenate(chunks)[:, 0] if chunks else np.zeros(0, dtype="float32")
        if len(audio) < SAMPLE_RATE * MIN_SECONDS:
            notify("Dictate", "Nothing recorded.")
            return 0

        loader.join()
        if "error" in loaded:
            notify("Dictate: model error", str(loaded["error"]))
            return 1

        model = config["model"]
        language = "en" if model.endswith(".en") else (config.get("language") or None)
        segments, _ = loaded["model"].transcribe(
            audio, language=language, vad_filter=True, beam_size=5,
            initial_prompt=config.get("vocabulary") or None,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        if text.lower() in HALLUCINATIONS:
            notify("Dictate", "Didn't catch that.")
            return 0

        log(f'pane {pane}: "{text}" ({time.time() - started:.1f}s total)')
        result = herdr("pane", "send-text", pane, text + " ")
        if result is None or result.returncode != 0:
            notify("Dictate: couldn't type into the pane", text)
            return 1
        if config.get("press_enter"):
            herdr("pane", "send-keys", pane, "enter")
    return 0


def main(argv):
    command = argv[1] if len(argv) > 1 else "toggle"
    if command == "toggle":
        return toggle()
    if command == "setup":
        return setup()
    if command == "warmup":
        return warmup()
    if command == "record" and len(argv) > 2:
        return record(argv[2])
    print("usage: dictate.py [toggle|setup]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
