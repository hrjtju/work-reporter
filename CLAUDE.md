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

**Pipeline:** Hotkey/auto-timer → ScreenshotCapture → PrivacyFilter (3 layers) → VlmTaskQueue → VisionAnalyzer (LLM) → EventStore (SQLite) → WorkMemory → ReportGenerator → `.md` files

### Module map (`src/`)

| Module | Role |
|---|---|
| `main.py` | `WorkReporterApp` — creates and wires all modules, runs the main loop. `_on_screenshot_captured()` inserts screenshot, runs privacy filter, then enqueues a `VlmTask`. Workers in `VlmTaskQueue` pull tasks and run VLM analysis asynchronously. In auto mode, privacy skips delete the file immediately (no retry). In manual mode, privacy skips are recorded with `skipped=1`. `_process_manual_vlm_batch()` triggers batch processing of all `vlm_processed=0` screenshots. |
| `screenshot.py` | Multi-monitor capture via `mss`, perceptual hash dedup (`imagehash`), global hotkeys via `pynput`. Supports manual hotkey (`ctrl+shift+alt+s`) and pause hotkey (`ctrl+shift+o`). No auto-capture hotkey — auto mode is toggled via tray menu or dashboard. Adds timestamp watermark to saved PNG files. |
| `privacy_filter.py` | **Layer 1:** app/title blacklist → skip. **Layer 2:** predefined region blur (taskbar, title bar, address bar). **Layer 3:** Tesseract OCR → regex PII detection → blur sensitive areas. Only blocks truly sensitive apps (WeChat, banking, password managers). |
| `vision_analyzer.py` | OpenAI-compatible API client for a local VLM (Ollama default). `analyze_screenshot()` auto-detects large images (>1920px) and splits into 2×2 tiles for better VLM text recognition. Uses a structured Chinese prompt with 9 fixed categories. Has `ALLOWED_CATEGORIES` whitelist validation in `_parse_analysis_response()`. Also detects VLM failure patterns (e.g. "无法识别") and sets confidence to 0.0. `_text_completion()` handles text-only LLM calls for reports and memory. `complete_text()` is the public wrapper. |
| `work_memory.py` | Accumulates work context across events. Every N events (default 20) calls the LLM to summarize recent activity into a compact memory snapshot, which gets injected into subsequent screenshot analysis prompts. Has its own local `_extract_json()` to avoid cross-module private import. |
| `event_store.py` | SQLite (WAL mode) with thread-local connections + write mutex. Tables: `screenshots`, `work_events`, `daily_reports`, `weekly_reports`, `work_memory`. Has inline schema migrations via `_migrate()`. `screenshots.vlm_processed` column tracks whether a screenshot has been VLM-processed. |
| `vlm_queue.py` | `VlmTaskQueue` — producer/consumer queue for async VLM tasks. Workers run `privacy.process → vision_llm.analyze_screenshot → store.insert_work_event → work_memory.on_event_analyzed`. `start()` launches N worker threads; `process_pending()` runs batch processing in manual mode. |
| `report_generator.py` | Aggregates events into `DailyReportData`/`WeeklyReportData` dataclasses. `generate_daily_with_llm()` tries LLM first, falls back to `generate_daily_summary()` (template-based). Handles JSON parsing with escape-sequence-aware field extraction. |
| `scheduler.py` | `APScheduler` cron jobs: daily report (weekdays at 18:00), weekly report (Friday 18:30), cleanup (daily 02:00). |
| `tray_app.py` | `pystray` system tray icon with right-click menu for manual capture, pause, toggle auto-capture, toggle VLM auto/manual, report generation, dashboard. |
| `web_dashboard.py` | Built-in HTTP server on `localhost:8765`. Single-page dashboard with: real-time stats, flat timeline (newest first, with time gaps), vertical stacked-bar heatmap (24 hours, category-colored segments proportional to event duration), LLM raw output tab, log tab (reads `work_reporter.log`), manual actions via POST API. |
| `config_loader.py` | Loads `config.yaml`, deep-merges with `DEFAULT_CONFIG`. `validate_config()` checks hotkey format, time formats, work days, and `weekly_report_day` range (0-6). |

### Key data flow

1. `ScreenshotCapture._on_hotkey()` (or auto timer) → `capture_all_screens()` → `_capture_single()` per monitor
2. Each screenshot gets a perceptual hash; if Hamming distance ≤ `duplicate_threshold` vs previous, it's skipped
3. `WorkReporterApp._on_screenshot_captured()` → `PrivacyFilter.process()` first; if privacy skip in auto mode → `os.remove()` the file and return; if privacy skip in manual mode → insert with `skipped=1`
4. Screenshot inserted into DB with `vlm_processed=0`; a `VlmTask` is enqueued to `VlmTaskQueue`
5. In `vlm_auto=True` mode: queue workers pull tasks → `VisionAnalyzer.analyze_screenshot()` → `store.insert_work_event()` → `work_memory.on_event_analyzed()`, then mark `vlm_processed=1`
6. In `vlm_auto=False` mode: workers are not started; `POST /api/vlm-process` calls `process_pending()` on all `vlm_processed=0` screenshots
7. `_parse_analysis_response()` validates category against `ALLOWED_CATEGORIES` whitelist; unknown categories fall back to "其他"
8. If confidence ≤ 0.3, falls back to rule-engine summary (app name + window title)
9. `WorkMemory.on_event_analyzed()` updates active projects heuristically; every 20 events triggers LLM memory summarization
10. Report generation: `ReportGenerator.aggregate_daily()` → `generate_daily_with_llm()` → saves to DB + `reports/daily/<date>.md`

### Database

SQLite at `data/work_reporter.db` with WAL mode. Thread-local connections (`threading.local()`) with a write mutex. Schema includes indexes on `timestamp`, `category`, `project`. Column migrations run on init (adds `technologies`, `task_phase`, `context_switch`, `context_note` to `work_events`; adds `vlm_processed` to `screenshots`). Logs written to `data/work_reporter.log`.

### Config

`config.yaml` at project root. Sections: `screenshot` (hotkeys: `hotkey` + `pause_hotkey`; `auto_interval_minutes`, quality, dedup threshold, `vlm_auto`), `privacy` (app_blacklist, blur_regions, OCR patterns, title_blacklist), `scheduler` (report times, work hours/days), `vision_llm` / `text_llm` (provider URL, model name, timeout), `web_dashboard` (port), `report` (output path, format).

Note: `auto_hotkey` was removed — auto-capture is toggled via dashboard button or tray menu only. Privacy retry was removed — privacy skips in auto mode delete the screenshot file immediately.

### Fixed categories

The VLM prompt defines 9 fixed categories. The `ALLOWED_CATEGORIES` set in `vision_analyzer.py` validates output. UI mappings (`CAT_ICONS`, `CAT_CSS`, `CAT_COLORS`) in `web_dashboard.py` provide icons, CSS classes, and colors for each:

| Category | CSS class | Color | Icon |
|---|---|---|---|
| 创作构建 | cat-code | #4A90D9 | 🛠 |
| 阅读查阅 | cat-doc | #2ecc71 | 📖 |
| 沟通协作 | cat-comm | #f39c12 | 💬 |
| 分析计算 | cat-browse | #9b59b6 | 📊 |
| 会议讨论 | cat-meeting | #e74c3c | 🎙 |
| 设计绘图 | cat-design | #1abc9c | 🎨 |
| 学习研究 | cat-learn | #7B68EE | 🔬 |
| 娱乐休闲 | cat-other | #95a5a6 | 🎮 |
| 其他 | cat-misc | #8899aa | 📌 |

### Image tiling

When a screenshot exceeds 1920px in either dimension, `analyze_screenshot()` splits it into a 2×2 grid of tiles. Each tile is JPEG-encoded at quality 92 and resized to max 1024px. Tiles are sent as separate `image_url` blocks in the chat completion request, with grid position labels. This prevents the VLM's vision encoder from lossy-compressing large images.

### Work memory schema

`work_memory` is a single-row table (`id = 1`). Stores: `daily_summary`, `active_projects` (JSON array), `recent_activities` (JSON array), `work_patterns`, `last_event_id`, `total_events_today`. Updated by `WorkMemory` both heuristically (every event) and via LLM (every N events).
