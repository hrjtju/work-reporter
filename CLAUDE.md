# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Work Reporter is a Windows desktop app that captures periodic screenshots of your work, filters sensitive content, uses a local Vision LLM (Ollama) to analyze what you're working on, and generates daily/weekly Markdown reports. It runs as a system tray app with a built-in web dashboard.

## Commands

```bash
# Run the app
uv run python main.py

# Install dependencies
uv sync
```

There is no test suite, linter, or formatter configured yet.

## Architecture

**Pipeline:** Hotkey/auto-timer → ScreenshotCapture → PrivacyFilter (3 layers) → VisionAnalyzer (LLM) → EventStore (SQLite) → WorkMemory → ReportGenerator → `.md` files

### Module map (`src/`)

| Module | Role |
|---|---|
| `main.py` | `WorkReporterApp` — creates and wires all modules, runs the main loop |
| `screenshot.py` | Multi-monitor capture via `mss`, perceptual hash dedup (`imagehash`), global hotkeys via `pynput`, auto-capture timer |
| `privacy_filter.py` | **Layer 1:** app/title blacklist → skip. **Layer 2:** predefined region blur (taskbar, title bar, address bar). **Layer 3:** Tesseract OCR → regex PII detection → blur sensitive areas |
| `vision_analyzer.py` | OpenAI-compatible API client for a local VLM (Ollama default). `analyze_screenshot()` sends a base64 image + structured prompt; `generate_daily_report()` / `generate_weekly_report()` generate Markdown reports. Also used for text-only LLM calls (`_text_completion`). |
| `work_memory.py` | Accumulates work context across events. Every N events (default 20) calls the LLM to summarize recent activity into a compact memory snapshot, which gets injected into subsequent screenshot analysis prompts. |
| `event_store.py` | SQLite (WAL mode) with thread-local connections + write mutex. Tables: `screenshots`, `work_events`, `daily_reports`, `weekly_reports`, `work_memory`. Has inline schema migrations via `_migrate()`. |
| `report_generator.py` | Aggregates events into `DailyReportData`/`WeeklyReportData` dataclasses. `generate_daily_with_llm()` tries LLM first, falls back to `generate_daily_summary()` (template-based). |
| `scheduler.py` | `APScheduler` cron jobs: daily report (weekdays at 18:00), weekly report (Friday 18:30), cleanup (daily 02:00). |
| `tray_app.py` | `pystray` system tray icon with right-click menu for manual capture, pause, report generation, dashboard. |
| `web_dashboard.py` | Built-in HTTP server on `localhost:8765`. Single-page dashboard with real-time stats, event timeline, density grid, manual actions via POST API. |
| `config_loader.py` | Loads `config.yaml`, deep-merges with `DEFAULT_CONFIG`. `validate_config()` checks hotkey format, time formats, work days. |

### Key data flow

1. `ScreenshotCapture._on_hotkey()` (or auto timer) → `capture_all_screens()` → `_capture_single()` per monitor
2. Each screenshot gets a perceptual hash; if Hamming distance ≤ `duplicate_threshold` vs previous, it's skipped
3. `WorkReporterApp._on_screenshot_captured()` → inserts into DB → `PrivacyFilter.process()` (if not skipped)
4. If Vision LLM is enabled and healthy → `VisionAnalyzer.analyze_screenshot()` returns structured `VisionAnalysisResult` (activity, category, project, technologies, task_phase, context_switch, confidence)
5. `WorkMemory.on_event_analyzed()` updates active projects heuristically; every 20 events triggers LLM memory summarization
6. Report generation: `ReportGenerator.aggregate_daily()` → `generate_daily_with_llm()` → saves to DB + `reports/daily/<date>.md`

### Database

SQLite at `data/work_reporter.db` with WAL mode. Thread-local connections (`threading.local()`) with a write mutex. Schema includes indexes on `timestamp`, `category`, `project`. Column migrations run on init (adds `technologies`, `task_phase`, `context_switch`, `context_note` to `work_events` if missing).

### Config

`config.yaml` at project root. Sections: `screenshot` (hotkeys, intervals, quality, dedup threshold), `privacy` (blacklists, blur regions, OCR patterns), `scheduler` (report times, work hours/days), `vision_llm` / `text_llm` (provider URL, model name), `web_dashboard` (port), `report` (output path, format).

### Work memory schema

`work_memory` is a single-row table (`id = 1`). Stores: `daily_summary`, `active_projects` (JSON array), `recent_activities` (JSON array), `work_patterns`, `last_event_id`, `total_events_today`. Updated by `WorkMemory` both heuristically (every event) and via LLM (every N events).
