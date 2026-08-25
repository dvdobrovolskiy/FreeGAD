# FreeGAD — Claude-powered AI assistant for FreeCAD

A persistent, in-CAD AI agent. Ask questions about the active document, run engineering
calculations, and (with confirmation) modify the model — all from a docked chat panel.
Backed by the Claude API (`claude-opus-5` by default) — or any OpenAI-compatible API (OpenAI,
OpenRouter, a local server) — with a tool-use loop over the **live** FreeCAD document. Works with FreeCAD 1.0 and 1.1 on Windows.

What Claude can do inside FreeCAD:

- **See the document** — a compact snapshot (objects, types, dependencies, bounding boxes,
  volumes, placements, selection) goes into every conversation; tools read the live details
  (every property, expressions, sketch geometry and constraints, spreadsheet cells, face/edge data).
- **See the 3D view** — `take_screenshot` renders the viewport and sends the image to Claude, so it
  can check shape, orientation and its own edits visually.
- **Measure** — distances between objects / sub-elements, face areas, edge lengths, radii.
- **Modify the model** — `run_python` (full FreeCAD API, one undo step, recompute after),
  `set_property`, `set_expression`, `delete_object`, `set_visibility`, `select`, `recompute`.
  Every write tool shows a confirmation dialog with the exact code/change; tick
  **Auto-approve edits** in the panel to skip the dialogs.
- **Remember** — notes saved with `remember` come back in later sessions: per-user notes
  (conventions, printer, materials, how you like answers) and per-document notes (design intent,
  which sketch drives what). Documents are keyed by their `Uid`, so notes survive renames/moves.
  Nothing is written into the `.FCStd`.

## Install

### Option A — installer (end users)

Run `FreeGADSetup.exe`. It installs per-user (no admin) into
`%APPDATA%\FreeCAD\Mod\FreeGAD` and optionally asks which provider to use and for your API key.
Start FreeCAD: a **FreeGAD** menu and toolbar button appear in every workbench, and there is also
a FreeGAD workbench.

Build the installer with `make_installer.bat` (needs [Inno Setup 6](https://jrsoftware.org/isinfo.php);
bumps the patch version in `version.txt` each build, like CSV Viewer).

### Option B — script (developers)

```
pwsh -File install.ps1                    # copy addon + prompt for key (if none stored)
pwsh -File install.ps1 -SetKey            # change the stored key only
pwsh -File install.ps1 -ApiKey sk-ant-... # non-interactive
pwsh -File install.ps1 -SkipKey           # install without touching the key
pwsh -File install.ps1 -Uninstall         # remove the addon (-Purge also deletes config/memory)
```

## API key and provider

FreeGAD talks to one of two kinds of API: **Anthropic** (Claude, the default) or any
**OpenAI-compatible** endpoint — OpenAI itself (`https://api.openai.com/v1`), OpenRouter
(`https://openrouter.ai/api/v1`, model ids like `anthropic/claude-opus-4.8`) or a local server. Pick
the provider in the key dialog or in Settings; both keys can be stored, so switching is just a
setting (effort is sent to OpenAI-compatible APIs as `reasoning_effort`). A key is needed once per
Windows user. Set or change it any time:

- inside FreeCAD: **FreeGAD → Set API key…** (or the **Key** button in the chat panel) — has a
  *Test* button that checks the key against the API;
- `install.ps1 -SetKey`;
- or the `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` environment variables (used when nothing is stored).

It is stored DPAPI-encrypted for the current Windows user in `%APPDATA%\FreeGAD\config.json`
(`apiKeyEnc` / `openaiApiKeyEnc`, plus `provider`, `openaiModel`, `openaiBaseUrl`). A plain `apiKey`
field written by hand is encrypted on first load.

## Settings

**FreeGAD → Settings…**: provider (Anthropic / OpenAI-compatible + base URL), model, effort
(`low`…`max`), max output tokens, server-side refusal fallback (Anthropic), auto-approve. Stored in `%APPDATA%\FreeGAD\config.json`.

## Memory

**FreeGAD → Memory…** lists and deletes notes. Files live in `%APPDATA%\FreeGAD\memory\`
(`user.json`, `documents\<uid-hash>.json`). Claude decides what to save; you can also just tell it
"remember that …" or "forget that …".

## Chat history

Transcripts are saved per document in `%APPDATA%\FreeGAD\history\` (last 40 lines restored when
the panel opens; a short excerpt is given to Claude for continuity). Recycling: 400 entries /
400 KB per file, files idle for 180 days deleted, folder capped at 25 MB. **Memory… → Clear chat
history** deletes the current document's file.

## Usage telemetry (on by default, opt-out)

**Settings… → Collect anonymous usage data.** Each turn sends: model/effort, token counts
(input/output/cache), API and tool latencies, how long the GUI thread was blocked (hang ≥ 2 s),
process CPU time, error class, plugin/FreeCAD/OS versions, object count — keyed by a random
`installId`. Never sent: prompts, answers, code, file names, document contents, the API key.
Events that fail to send are spooled in `%APPDATA%\FreeGAD\telemetry\` (≤ 512 KB) and retried.

Backend + dashboard: `server/` (FastAPI + SQLite, SvelteKit static build) at
https://freecad.dobrovolskiy.com (login required). Deploy with `deploy-server.ps1`
(`-SetPassword` to change the dashboard password, `-Logs` to tail). Data lives in the
`freegad_freegad_data` Docker volume on the VPS.

## Layout

```
Init.py / InitGui.py     FreeCAD entry points: commands, workbench, global menu + toolbar
freegad/agent.py         Claude tool-use loop, one conversation per document, prompt caching
freegad/tools.py         tool schemas + executors (run on the GUI thread)
freegad/context.py       compact document snapshot / object detail
freegad/memory.py        persistent notes (user + per-document)
freegad/client.py        raw HTTP client for POST /v1/messages (stdlib only)
freegad/config.py        config.json, DPAPI-encrypted key, installer key hand-off
freegad/ui.py            chat dock, API key / settings / memory dialogs, worker thread
install.ps1              dev install / key management
Installer.iss            Inno Setup script; make_installer.bat builds FreeGADSetup.exe
```

The plugin uses raw HTTP (urllib) instead of the `anthropic` SDK on purpose: FreeCAD ships its own
embedded Python and pip-installing into it is fragile across versions.

## Troubleshooting

- *"No API key set"* — FreeGAD → Set API key….
- Nothing in the FreeGAD menu after install — check the Report view for `FreeGAD loaded`; the
  addon folder must be `%APPDATA%\FreeCAD\Mod\FreeGAD\InitGui.py`.
- Edits Claude made can be undone with normal **Undo** (each tool call is one transaction).

## License

AGPL-3.0-or-later. Copyright (C) 2026 Dmitriy Dobrovolskiy. See `LICENSE`; every source file carries an SPDX header.
