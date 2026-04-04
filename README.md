# DailyStream

A minimal daily recording stream system for macOS. Capture screenshots and clipboard content with global hotkeys, organize by pipelines, and sync to Apple Notes or Obsidian.

## Features

- **Global hotkeys**: `Ctrl+Shift+S` for screenshot, `Ctrl+Shift+V` for clipboard capture
- **Menu bar app**: Always-on tray app with workspace/pipeline management
- **Pipeline organization**: Group recordings by topic/theme
- **Real-time sync**: Apple Notes (macOS) or Obsidian vault (cross-platform)
- **CLI interface**: Full command-line control

## Installation

```bash
# Clone the repo
git clone https://github.com/shoggothforever/dailyStream.git
cd dailyStream

# Create virtual environment and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## macOS Permissions

**Required**: Grant Accessibility permission to your terminal app.

1. Open **System Settings → Privacy & Security → Accessibility**
2. Click `+` and add your terminal app:
   - Built-in Terminal: `/System/Applications/Utilities/Terminal.app`
   - iTerm2: `/Applications/iTerm.app`
   - Warp / Alacritty / other: find in `/Applications/`
3. Toggle it **ON**

This is needed for `pynput` to listen to global hotkeys across all apps.
The permission goes to your **terminal**, not Python itself.

## Usage

### Menu Bar App (recommended)

```bash
dailystream app
```

### CLI Commands

```bash
# Start a workspace (choose directory via dialog)
dailystream start

# Start with specific path
dailystream start --path ~/my-notes/today

# Create a pipeline
dailystream pipeline create "reading-notes"

# Activate a pipeline
dailystream activate "reading-notes"

# Manually feed content
dailystream feed "Some interesting text"

# Check status
dailystream status

# End workspace and generate timeline
dailystream end
```

## Configuration

Config file: `~/.dailystream/config.json`

Use `config.example.json` as a template. Supported options:

```json
{
  "hotkey_screenshot": "<cmd>+1",
  "hotkey_clipboard": "<cmd>+2",
  "screenshot_mode": "interactive",
  "default_workspace_path": "",
  "note_sync_backend": "markdown",
  "obsidian_vault_path": ""
}
```

### Configuration Options

| Option | Default | Values | Description |
|--------|---------|--------|-------------|
| `hotkey_screenshot` | `<cmd>+1` | Hotkey string | Global hotkey to capture screenshot |
| `hotkey_clipboard` | `<cmd>+2` | Hotkey string | Global hotkey to capture clipboard content |
| `screenshot_mode` | `interactive` | `interactive`, `fullscreen` | `interactive`: let user select region; `fullscreen`: capture entire screen |
| `default_workspace_path` | `` | Path string | Default directory for workspaces (optional) |
| `note_sync_backend` | `markdown` | `markdown`, `obsidian`, `both`, `none` | Where to sync captured content |
| `obsidian_vault_path` | `` | Path string | Obsidian vault path (required if using obsidian backend) |

**Hotkey Format**: Use `<modifier>+<key>` format, e.g. `<cmd>+1`, `<shift>+<alt>+s`

## License

MIT
