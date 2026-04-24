# DailyStream

A minimal daily recording stream system for macOS. Capture screenshots and clipboard content with global hotkeys, organize by pipelines, and sync to Apple Notes or Obsidian.

## Features

- **Capture Mode Designer**: Mode → Preset → Attachment three-layer architecture.  Build your own capture recipes from predefined atomic Attachments (burst / interval / hide-cursor / auto-OCR / …), bind them to hotkeys, and switch whole Modes with one click.  See `docs/capture_mode_designer.md`.
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

This is needed for global hotkey listening to work across all apps.

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
  "screenshot_save_path": "",
  "screenshot_presets": [
    {"name": "Left Half", "region": "0,0,960,1080", "hotkey": "<cmd>+3"},
    {"name": "Right Half", "region": "960,0,960,1080", "hotkey": "<cmd>+4"}
  ],
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
| `screenshot_save_path` | `` | Path string | Custom screenshot save directory. Empty = `<workspace>/screenshots/` |
| `screenshot_presets` | `null` | Array of `{name, region, hotkey?}` | Predefined capture regions with optional hotkeys (see below) |
| `default_workspace_path` | `` | Path string | Default directory for workspaces (optional) |
| `note_sync_backend` | `markdown` | `markdown`, `obsidian`, `both`, `none` | Where to sync captured content |
| `obsidian_vault_path` | `` | Path string | Obsidian vault path (required if using obsidian backend) |

**Hotkey Format**: Use `<modifier>+<key>` format, e.g. `<cmd>+1`, `<shift>+<alt>+s`

### Screenshot Presets

You can predefine multiple capture regions so you don't have to manually drag every time. Each preset has:

- **`name`** — Display name shown in the screenshot submenu
- **`region`** — Capture area as `"x,y,width,height"` in pixels (screen coordinates)
- **`hotkey`** *(optional)* — Global hotkey to trigger this preset directly (e.g. `"<cmd>+3"`). Press this key combo anywhere to instantly capture the region, **no menu needed**

#### Creating Presets

**Method 1 — From the menu bar** (recommended):

1. Click `📸 Screenshot` → `➕ Create Preset...`
2. A translucent overlay appears — drag to select the desired region
3. Enter a name → Done! The preset is saved and immediately usable

**Method 2 — From the command line**:

```bash
# Interactive: drag to select, then name it
dailystream preset create --name "My Region"

# With hotkey: press <cmd>+3 to capture this region instantly
dailystream preset create --name "Left Half" --region "0,0,960,1080" --hotkey "<cmd>+3"

# Direct: specify coordinates (no hotkey)
dailystream preset create --name "Right Half" --region "960,0,960,1080"

# List all presets
dailystream preset list

# Delete by name or index
dailystream preset delete "Left Half"
dailystream preset delete 1
```

**Method 3 — Edit config JSON directly** (`~/.dailystream/config.json`):

```json
"screenshot_presets": [
  {"name": "Left Half",   "region": "0,0,960,1080",   "hotkey": "<cmd>+3"},
  {"name": "Right Half",  "region": "960,0,960,1080",  "hotkey": "<cmd>+4"},
  {"name": "Top Half",    "region": "0,0,1920,540"},
  {"name": "Center 720p", "region": "600,180,720,720"}
]
```

#### Using Presets

**Fastest way — Global hotkey** (recommended): If a preset has a `hotkey` configured, just press that key combo anywhere and the region is captured instantly. No menus, no clicking.

The `📸 Screenshot` menu item becomes a **submenu** listing all your presets:

```
📸 Screenshot
  ├── 📐 Left Half  [<cmd>+3]  ← hotkey shown; click or press <cmd>+3
  ├── 📐 Right Half [<cmd>+4]
  ├── ✂️ Free Selection          ← drag to select (same as before)
  ├── ➕ Create Preset...        ← create a new preset interactively
  └── 🗑 Delete Preset           ← remove an existing preset
```

Pressing the **screenshot hotkey** (`<cmd>+1` by default) defaults to free selection mode.

**How to find coordinates**: `x,y` is the top-left corner in screen pixels; `w,h` is the width and height. The easiest way is to use `➕ Create Preset...` which captures coordinates automatically.

## License

MIT
