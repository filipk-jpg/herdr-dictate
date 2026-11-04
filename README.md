# Dictate for Herdr

Talk to your agents instead of typing. Press a key, speak, and press it again.
Your words show up in the focused Herdr pane, ready to edit, without pressing Enter.

- **Local.** Speech is transcribed on your machine with [faster-whisper](https://github.com/SYSTRAN/faster-whisper). No audio leaves your computer.
- **Pane-aware.** Text goes to the pane you started in, even if you switch panes while talking.
- **Works on Linux, macOS, and Windows.**
- **Knows dev words.** A vocabulary hint helps Whisper spell names like Next.js, pnpm and Claude Code correctly. You can add your own terms.

## Requirements

- Herdr 0.9.0 or newer
- Python 3.9+ on PATH (`python3` on macOS/Linux, `python` on Windows). Use 3.11+ if you want `config.toml`.
- A microphone
- Linux only: PortAudio (`sudo apt install libportaudio2` or your distro's equivalent)

## Install

From GitHub:

```sh
herdr plugin install filipk-jpg/herdr-dictate
```

Or from a local folder:

```sh
herdr plugin link /path/to/herdr-dictate
```

Then bind a key in your Herdr config. The config is `~/.config/herdr/config.toml` on Linux and macOS, and `%APPDATA%\herdr\config.toml` on Windows.

**macOS / Linux**

```toml
[[keys.command]]
key = "ctrl+alt+m"
type = "plugin_action"
command = "filipk-jpg.dictate.toggle"
description = "dictate"
```

**Windows**

```toml
[[keys.command]]
key = "ctrl+alt+m"
type = "plugin_action"
command = "filipk-jpg.dictate.toggle-windows"
description = "dictate"
```

Then reload the config: `herdr server reload-config`.

The **first press** runs a one-time setup. It creates a private Python environment in the plugin's state directory and downloads the speech model, about 500 MB for the default model. Expect a few minutes. A notification tells you when it's ready.

## Use

1. Focus the pane you want to type into.
2. Press `ctrl+alt+m` and talk. A "Listening..." notification appears, plus a short sound on macOS and Windows.
3. Press `ctrl+alt+m` again. The text is typed into the pane in a few seconds.
4. Check the text and press Enter yourself, or set `press_enter = true`.

## Settings

Settings live in `config.toml` in the plugin's config directory, which is created on first run:

```sh
herdr plugin config-dir filipk-jpg.dictate
```

| Setting | Default | What it does |
| --- | --- | --- |
| `model` | `small.en` | `base.en` is faster, `medium.en` is more accurate. Use a model name without `.en` (e.g. `small`) for other languages. |
| `language` | `""` | Language code for multilingual models, e.g. `de`. Empty means auto-detect. |
| `device` | `cpu` | Set to `cuda` with an NVIDIA GPU set up for faster-whisper. |
| `press_enter` | `false` | Submit automatically after typing. |
| `max_seconds` | `120` | Stop recording automatically after this long. |
| `sounds` | `true` | Start/stop sounds on macOS and Windows. |
| `vocabulary` | dev terms | Words Whisper should expect. Add your project names here. |

Changes apply on the next recording. A new model downloads the first time you use it.

## How it works

The action is a toggle. The first press records until the next press, then transcribes the audio and sends the text with `herdr pane send-text <pane> <text>`. The second press only drops a stop file. The two presses coordinate through a heartbeat file in the plugin state directory, so a crash never leaves Dictate stuck. The Whisper model loads while you're still talking, so transcription starts as soon as you stop.

## Troubleshooting

- **Nothing happens on key press:** your terminal or OS may be swallowing the chord. Try another key, or run `herdr plugin action invoke filipk-jpg.dictate.toggle` to test without the key.
- **Setup failed:** check `setup.log` in the plugin state directory: `~/.local/state/herdr/plugins/filipk-jpg.dictate/` on Linux and macOS. `herdr plugin log list --plugin filipk-jpg.dictate` also shows recent runs. To start over, delete that state directory and press the key again.
- **Microphone error:** make sure your terminal app is allowed to use the microphone. On macOS, check System Settings → Privacy & Security → Microphone.
- **Slow transcription:** switch `model` to `base.en`.

## License

MIT
