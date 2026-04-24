# Migration Guide: rumps → Swift App

> DailyStream 0.3.0

## What Changed

The Python-based menu bar app (`dailystream app`, powered by `rumps`) has
been **deprecated** in favour of a native Swift app located at
`apps/DailyStreamMac/`.

## Timeline

| Version | Status |
|---------|--------|
| 0.3.0 | `dailystream app` prints deprecation warning; Swift app functional |
| 0.5.0 (planned) | `dailystream app` and `rumps` dependency fully removed |

## For CLI Users

**Nothing changes.** All `dailystream start/end/feed/pipeline/analyze/status/preset`
commands work exactly as before.

## For Menu Bar Users

### Option A: Use the Swift app (recommended)

```bash
cd apps/DailyStreamMac
swift run DailyStreamMac
```

Or, after building:
```bash
swift build -c release
# The binary is at .build/release/DailyStreamMac
```

### Option B: Keep using the old rumps app (temporary)

```bash
pip install 'dailystream[legacy]'
dailystream app
# You will see: "ℹ️ Note: dailystream app is deprecated..."
```

## Data Compatibility

Your existing workspaces work with both the CLI and the Swift app.
No migration needed — the JSON schema is identical.

## Known Differences

| Feature | rumps app | Swift app |
|---------|-----------|-----------|
| NSAlert dialogs | Yes (multiple pop-ups) | No — replaced by HUD panels |
| Screenshot description | NSAlert after capture | HUD with thumbnail preview |
| Screenshot cancel | Silently aborts | Same (no error toast) |
| Cancel description → deletes file | Yes | Yes |
| Daily Review on End | No (just generates MD) | Auto-opens review window |
| Preferences UI | None | Settings → 5 tabs (partial) |
| About window | None | Yes |
| Global hotkey system | CGEventTap | KeyboardShortcuts library |
| Multi-screen overlay | Main screen only | All screens |
| Magnifier during selection | No | Yes (4× zoom) |
