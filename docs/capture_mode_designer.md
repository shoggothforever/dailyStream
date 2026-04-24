# Capture Mode Designer

The Capture Mode Designer introduces a three-layer model that makes
DailyStream's capture system fully user-configurable without re-compiling
the app.

```
Mode ── container for a whole recipe book
  └── Preset ── one named capture recipe + hotkey
         └── Attachment ── atomic capability (strategy / feedback / …)
```

Only one **Mode** is active at a time.  Switching Mode re-registers all
hotkeys from scratch so Presets in other Modes are never triggered
accidentally.

## Vocabulary

| Term        | Meaning                                                                 |
|-------------|-------------------------------------------------------------------------|
| Mode        | Named collection of Presets (e.g. "Default", "Gaming", "Teaching").     |
| Preset      | Source + Attachments + hotkey.  Lives inside exactly one Mode.          |
| Attachment  | Atomic, reusable capability the user can plug into a Preset.            |
| Source      | Where the capture comes from: interactive / fullscreen / region / clipboard / window. |
| Attachment Kind | Category used for UI grouping + single-/multi-choice rules.          |

### Attachment kinds

* **STRATEGY** *(pick one)* — how often we capture:
  * `single` — one frame
  * `burst` — N frames at a fixed interval
  * `interval` — every N seconds until stopped
* **FEEDBACK** *(multi)* — how the user is told a shot happened:
  * `silent_save`, `flash_menubar`, `sound`, `notification`
* **WINDOW_CTRL** *(multi)* — scene setup before the shot:
  * `hide_cursor` — omit the mouse pointer from the image (passes `-C` to `screencapture`)
  * `hide_dock` — toggle Dock auto-hide via AppleScript; restored after the shot
* **POST** *(multi, ordered)* — post-processing per frame:
  * `auto_ocr` — Vision framework text recognition, result written to `post_artifacts.ocr_text`
  * `quick_tags` — transient keypress-to-tag HUD
  * `auto_copy_clipboard` — copy the frame to the system clipboard
  * `ai_analyze` — send the frame to Claude and optionally prefill the HUD description
  * `run_command` — user-defined shell command / script; context is passed via `DAILYSTREAM_*` env vars
* **DELIVERY** *(pick one)* — where the frame goes:
  * `current_pipeline` (default)

## User flow

1. Click the menu bar icon → **Screenshot ▸ ⚙️ Capture Mode Designer…**.
2. On the left, pick or create a **Mode**.
3. In the middle, pick or create a **Preset**.
4. On the right, edit:
   * Name / emoji
   * Hotkey (click the recorder field, then press the combo)
   * Source kind (+ region picker if REGION)
   * Attachments grouped by Kind
5. Press **Save**.  The hotkey is registered immediately if this is the
   active Mode; otherwise it waits for you to activate the Mode.
6. Switch Mode from the menu bar header or Designer "Activate" button.

## Backward compatibility

Existing `config.json` files with the legacy keys
`screenshot_mode` + `screenshot_presets[]` are migrated automatically
on the first `Config.load()`.  The migration:

* Builds a built-in `default` Mode containing:
  * `free-selection` preset (using the previous `screenshot_mode`
    as its Source kind)
  * `clipboard` preset
  * One region preset per entry in the legacy `screenshot_presets`,
    preserving its name, region and hotkey
* Writes the new `capture_modes` key next to the legacy ones.  The
  legacy keys are **retained on disk** so rolling back to an older build
  never loses preset data.

## JSON schema

Inside `config.json`:

```jsonc
{
  // …existing keys…

  "capture_modes": {
    "active_mode_id": "default",
    "modes": [
      {
        "id": "default",
        "name": "Default",
        "emoji": "🗂",
        "presets": [
          {
            "id": "free-selection",
            "name": "Free Selection",
            "emoji": "✂️",
            "source": { "kind": "interactive" },
            "attachments": [
              { "id": "single", "params": {} },
              { "id": "current_pipeline", "params": {} }
            ],
            "hotkey": null
          }
        ]
      }
    ]
  }
}
```

## RPC surface

All methods live in the `capture_modes.*` namespace (see also
`docs/rpc_protocol.md`).

### Read

| Method                                   | Params          | Returns                                                        |
|------------------------------------------|-----------------|----------------------------------------------------------------|
| `capture_modes.list_modes`               | none            | `{ modes, active_mode_id }`                                    |
| `capture_modes.get_active`               | none            | `{ active_mode_id, mode? }`                                    |
| `capture_modes.list_attachment_catalog`  | none            | `{ catalog: AttachmentSpec[] }`                                |
| `capture_modes.list_running_intervals`   | none            | `{ running: { key, alive }[] }`                                |

### Write

| Method                                 | Params                                   | Returns                                |
|----------------------------------------|------------------------------------------|----------------------------------------|
| `capture_modes.switch_active_mode`     | `{ mode_id }`                            | `{ active_mode_id }`                   |
| `capture_modes.save_mode`              | `{ mode }`                               | `{ mode, created }`                    |
| `capture_modes.delete_mode`            | `{ mode_id }`                            | `{ deleted, active_mode_id }`          |
| `capture_modes.save_preset`            | `{ mode_id, preset }`                    | `{ preset, created }`                  |
| `capture_modes.delete_preset`          | `{ mode_id, preset_id }`                 | `{ deleted }`                          |

### Execute

| Method                              | Params                               | Returns                              |
|-------------------------------------|--------------------------------------|--------------------------------------|
| `capture_modes.execute_preset`      | `{ mode_id, preset_id, silent? }`    | `ExecutionReport`                    |
| `capture_modes.start_interval`      | `{ mode_id, preset_id }`             | `{ running, mode_id, preset_id, seconds }` |
| `capture_modes.stop_interval`       | `{ mode_id, preset_id }`             | `{ running, mode_id, preset_id }`    |

### Events (notifications)

| Method                                | Params                                          |
|---------------------------------------|-------------------------------------------------|
| `capture_modes.changed`               | whole state (same as `list_modes`)              |
| `capture_modes.interval_started`      | `{ mode_id, preset_id, seconds, max_count }`    |
| `capture_modes.interval_stopped`      | `{ mode_id, preset_id, captured }`              |
| `capture.mode_preset_executed`        | `ExecutionReport` (see below)                   |
| `capture.flash_menubar`               | `{}`                                            |
| `capture.notification`                | `{ title, body }`                               |
| `capture.quick_tags_prompt`           | `{ path, window_seconds, tags[] }`              |

### ExecutionReport shape

```jsonc
{
  "mode_id": "default",
  "preset_id": "free-selection",
  "preset_name": "Free Selection",
  "silent": false,
  "cancelled": false,
  "error": null,
  "frames": [
    {
      "path": "/path/to/frame.png",
      "index": 0,
      "source_kind": "interactive",
      "skipped": false,
      "error": null,
      "post_artifacts": { "ocr_text": "…" }
    }
  ]
}
```

## Extending the catalog

New capabilities are added by:

1. Appending an `AttachmentSpec` in
   `src/dailystream/capture_modes/catalog.py`.
2. Teaching `src/dailystream/capture_modes/executor.py` how to react to
   the new `id` (in one of the `_apply_feedback` / `_run_post` /
   `_WindowCtrlHandler` helpers).
3. (Optional) Writing a Swift UI override if the generated param form
   isn't expressive enough.

The Designer's attachment grid is rendered entirely from
`list_attachment_catalog`, so the new capability appears automatically —
no Swift changes required for simple attachments.
