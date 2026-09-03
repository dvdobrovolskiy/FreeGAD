# FreeGAD — AI-powered assistant for FreeCAD

by Dmitriy Dobrovolskiy · https://dobrovolskiy.com/work/freegad · **Download for Windows:**
https://dobrovolskiy.com/go/freegad-win?src=github (latest installer) · Free, AGPL-3.0

<!-- TODO: hero screenshot of the docked chat panel next to a model (resources/media/hero.png)
     and a 20-second GIF: sketch -> constraint -> measure -> edit with the confirmation dialog -->

A persistent, in-CAD AI agent. Literally does work for you. Direct prompt to 3D models conversion, any 3D manipulation by prompt. Ask questions about the active document, run engineering
calculations, and (with confirmation) modify the model — all from a docked chat panel.
Backed by the Claude API (`claude-opus-5` by default) — or any OpenAI-compatible API (OpenAI,
OpenRouter, a local server) — with a tool-use loop over the **live** FreeCAD document. Works with FreeCAD 1.0 and 1.1 on Windows.

Cost & context
- Prompt caching — two cache breakpoints: one on the system prompt (persona + document snapshot), one on the conversation tail. Each tool iteration re-reads the growing history at 1/10 price; data showed 99.99 % of input tokens served from cache.
- Per-turn cost line — after each answer: API calls, uncached input, cache-read, cache-write, output tokens and an estimated $ (list prices per model; OpenRouter's real cost when available), plus a running session total.
- History compaction — at the start of each turn, tool results from earlier turns are shrunk to 600 chars and old screenshots dropped; single tool results are capped at 14k chars.
- Persona guidance — the model is told to plan explore → act → verify as three run_python calls, not five, and take at most one screenshot per edit cycle.

Safety of run_python
- Watchdog — scripts are aborted after 120 s; the GUI repaints every 0.5 s while a script runs so FreeCAD doesn't look frozen.
- Memory guard — a script that grows FreeCAD's memory by more than half the RAM, or leaves less than ~8 % free, is aborted (sticky, memory released) with advice on doing the job with less geometry. 
- Tool description warns about boolean loops and the time/memory limits.

Chat UI
- Image attachments — paste screenshots/images from the clipboard or drop image files into the input; shown as thumbnails, downscaled to 1568 px, JPEG fallback for big photos, sent with the prompt.

What AI can do inside FreeCAD:

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

## What it can't do (yet)

- **Windows only.** The installer, the DPAPI key storage and the memory guard are Windows code; no
  Linux or macOS build yet.
- **FreeCAD 1.0 and 1.1 only** (`package.xml` says `freecadmin 1.0.0`); 0.21 is not supported.
- **Needs your own API key and every turn costs money** — the panel prints tokens and an estimated
  price after each answer. A local OpenAI-compatible server works too if you want zero API cost.
- **It edits only with your confirmation.** Every write tool pops a dialog with the exact code or
  change (unless you tick *Auto-approve edits*). It cannot run while you are away and it cannot
  batch-process files you have not opened.
- **It sees the document, not your screen** — one snapshot per message plus what the tools read and
  the viewport screenshot it asks for; it does not watch you work.
- No TechDraw, Path/CAM or FEM-specific tools; those go through `run_python` and the general
  FreeCAD API.

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
(input/output/cache, per API call), API and tool latencies, how long the GUI thread was blocked
(hang ≥ 2 s), FreeCAD's memory growth per `run_python`, process CPU time, error class,
plugin/FreeCAD/OS versions, object count — keyed by a random `installId`. Never sent: prompts,
answers, code, file names, document contents, the API key.
Events that fail to send are spooled in `%APPDATA%\FreeGAD\telemetry\` (≤ 512 KB) and retried.
A turn that dies with the FreeCAD process (crash, out-of-memory, reboot) is reported on the next
start as a turn with a `ProcessDied` error.

> **Anonymous usage statistics.** With the same switch on, the addon also sends a random install id,
> its version, OS version and which features are used (event names and counts: first run, session,
> turn, settings saved, memory note saved) to dobrovolskiy.com so I can see what to improve. It never
> sends file names, file contents, prompts, answers, or anything typed. Turn it off with
> **Settings… → Collect anonymous usage data**, or set `"enabled": false` in
> `%APPDATA%\FreeGAD\metrics.json`, or set the environment variable `DM_DISABLE=1`.

**Settings… → Script time limit / Memory guard** (both on by default): `run_python` scripts are
aborted after the time limit (120 s; set 0 for heavy jobs you are willing to wait for), and as soon
as one grows FreeCAD's memory by more than half of the machine's RAM or leaves less than ~8 % free —
a runaway boolean/tessellation loop can otherwise push Windows into swapping so hard that only a
reboot helps. Untick the guard only if a job legitimately needs that much memory.

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
freegad/telemetry.py     per-turn LLM metrics -> freecad.dobrovolskiy.com; freegad/dm.py usage counts -> dobrovolskiy.com
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
