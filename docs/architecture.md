# DailyStream Architecture

> Updated: April 2026 · Version 0.3.0

## Overview

DailyStream uses a **Swift UI shell + Python backend** hybrid architecture.

```
┌──────────────────────────────────────────┐
│ DailyStream.app (Swift/SwiftUI + AppKit) │
│  MenuBarExtra · HUD panels · Capture     │
│  Daily Review · Preferences · Toast      │
│  CoreBridge Actor (JSON-RPC over stdio)  │
└────────────────┬─────────────────────────┘
                 │ stdin/stdout
┌────────────────▼─────────────────────────┐
│ dailystream-core (Python 3.11)           │
│  rpc_server · workspace · pipeline       │
│  timeline · ai_analyzer · note_sync      │
│  capture · config                        │
└──────────────────────────────────────────┘
```

## Principles

1. **Single-direction file writes**: Only Python writes workspace files.
   Swift is read-only. Eliminates concurrent write conflicts.
2. **Thin bridge**: Swift has zero business logic. All workspace/pipeline/
   timeline/AI semantics live in Python.
3. **CLI ↔ .app equivalence**: Every RPC method maps 1:1 to a CLI command.

## Monorepo Layout

```
dailystream/
├── src/dailystream/         Python core (12 modules)
├── tests/                   Python pytest (227 tests)
├── apps/DailyStreamMac/     Swift Package Manager project
│   ├── Sources/DailyStreamCore/Bridge/   RPC types + CoreBridge actor
│   ├── Sources/DailyStreamMac/           SwiftUI app (menu bar, HUD, capture, etc.)
│   └── Tests/                            XCTest (24 tests)
├── scripts/                 Build tooling (bundle, sign, release)
├── docs/                    Architecture + protocol docs
└── Makefile                 dev / test / bundle / release
```

## Data Flow

```
User → Swift UI → CoreBridge → JSON-RPC → Python rpc_server
                                            → workspace.py / pipeline.py / ...
                                            → writes context.json, stream.md, etc.
                                            ← response / event
        ← AppState update ← CoreBridge ←
```

## Python Bundle

The `.app` embeds a standalone Python via `python-build-standalone`:

```
DailyStream.app/
  Contents/
    MacOS/DailyStreamMac          Swift executable
    Frameworks/Python.framework/  Standalone Python 3.11 + dailystream package
```

Swift discovers `dailystream-core` by walking up from its own executable
until it finds `Frameworks/Python.framework/Versions/3.11/bin/dailystream-core`.
