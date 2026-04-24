# DailyStream RPC Protocol

> Version 0.3.0 · April 2026

The Swift UI shell (`DailyStreamMac`) communicates with the Python core
(`dailystream-core`) via **JSON-RPC 2.0** over **stdio** (newline-delimited
JSON, one message per line).

## Transport

| Direction | Pipe | Format |
|-----------|------|--------|
| Swift → Python | stdin | `{"jsonrpc":"2.0","id":N,"method":"...","params":{...}}\n` |
| Python → Swift (response) | stdout | `{"jsonrpc":"2.0","id":N,"result":{...}}\n` |
| Python → Swift (event) | stdout | `{"jsonrpc":"2.0","method":"...","params":{...}}\n` |
| Python logging | stderr | Free-form text (never parsed) |

## Methods

### app
| Method | Params | Result |
|--------|--------|--------|
| `app.ping` | — | `"pong"` |
| `app.version` | — | `{rpc_version, python_version}` |
| `app.shutdown` | — | `"ok"` |

### workspace
| Method | Params | Result |
|--------|--------|--------|
| `workspace.create` | `{path?, title?, ai_mode}` | `{workspace_dir, workspace_id, ai_mode}` |
| `workspace.open` | `{path}` | workspace status dict |
| `workspace.end` | — | `{timeline_report}` |
| `workspace.status` | — | status dict |
| `workspace.list_recent` | `{limit?}` | `[{workspace_id, title, workspace_path, ...}]` |

### pipeline
| Method | Params | Result |
|--------|--------|--------|
| `pipeline.create` | `{name, description?, goal?}` | `{name, pipeline_dir, active}` |
| `pipeline.switch` | `{name}` | `{active}` |
| `pipeline.list` | — | `{pipelines, active}` |
| `pipeline.rename` | `{old, new}` | `{old, new}` |
| `pipeline.delete` | `{name}` | `{deleted}` |

### capture
| Method | Params | Result |
|--------|--------|--------|
| `capture.screenshot` | `{mode?, region?}` | `{path}` |
| `capture.select_region` | — | `{region}` |
| `capture.clipboard.grab` | — | `{content, type}` |
| `capture.clipboard.save_image` | — | `{path}` |

### feed
| Method | Params | Result |
|--------|--------|--------|
| `feed.text` | `{content, description?, pipeline?}` | `{pipeline, entry_index, entry}` |
| `feed.url` | `{content, description?, pipeline?}` | same |
| `feed.image` | `{path, description?, pipeline?}` | same |

### timeline
| Method | Params | Result |
|--------|--------|--------|
| `timeline.generate` | — | `{path}` |
| `timeline.export_structured` | — | ReviewData JSON (see M4) |

### ai
| Method | Params | Result |
|--------|--------|--------|
| `ai.status` | — | `{sdk_available, has_api_key, model}` |
| `ai.analyze_entry` | `{pipeline, entry_index, force?}` | `{status, result}` |
| `ai.batch_analyze` | — | `{produced_new}` |

### config
| Method | Params | Result |
|--------|--------|--------|
| `config.get` | `{key?}` | full config or `{key, value}` |
| `config.set` | `{key, value}` | `{key, value}` |
| `config.get_schema` | — | `{fields: [...]}` |

### preset
| Method | Params | Result |
|--------|--------|--------|
| `preset.list` | — | `{presets: [...]}` |
| `preset.create` | `{name, region, hotkey?}` | `{preset}` |
| `preset.delete` | `{name}` | `{deleted}` |
| `preset.update` | `{name, region?, hotkey?, new_name?}` | `{preset}` |

## Events (Python → Swift, no `id`)

| Event | Description |
|-------|-------------|
| `workspace.changed` | Workspace state changed (create/end/switch pipeline) |
| `feed.entry_added` | A new entry was fed into a pipeline |
| `ai.analysis_completed` | An AI analysis result is ready |
| `ai.analysis_progress` | Batch analysis progress update |
| `ai.analysis_failed` | An analysis task failed |

## Error Codes

| Code | Meaning |
|------|---------|
| -32700 | Parse error |
| -32600 | Invalid request |
| -32601 | Method not found |
| -32602 | Invalid params |
| -32603 | Internal error |
| -32000 | Domain error |
| -32001 | State conflict (e.g. workspace already active) |
| -32002 | Not found |
