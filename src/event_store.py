"""事件存储模块 — SQLite 数据库操作，管理截图记录、工作事件和报告"""

import logging
import os
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── 数据库 Schema ────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS screenshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       DATETIME NOT NULL,
    file_path       TEXT NOT NULL,
    phash           TEXT,
    app_name        TEXT DEFAULT '',
    window_title    TEXT DEFAULT '',
    screen_index    INTEGER DEFAULT 0,
    is_duplicate    INTEGER DEFAULT 0,
    skipped         INTEGER DEFAULT 0,
    skip_reason     TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS work_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    screenshot_id   INTEGER REFERENCES screenshots(id),
    timestamp       DATETIME NOT NULL,
    activity        TEXT NOT NULL,
    category        TEXT DEFAULT '',
    detail          TEXT DEFAULT '',
    project         TEXT DEFAULT '',
    is_productive   INTEGER DEFAULT 1,
    technologies    TEXT DEFAULT '[]',
    task_phase      TEXT DEFAULT '',
    context_switch  INTEGER DEFAULT 0,
    context_note    TEXT DEFAULT '',
    raw_response    TEXT DEFAULT '',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date     DATE NOT NULL UNIQUE,
    content         TEXT NOT NULL,
    generated_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS weekly_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start      DATE NOT NULL UNIQUE,
    content         TEXT NOT NULL,
    generated_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_screenshots_timestamp ON screenshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_screenshots_skipped ON screenshots(skipped);
CREATE INDEX IF NOT EXISTS idx_work_events_timestamp ON work_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_work_events_category ON work_events(category);
CREATE INDEX IF NOT EXISTS idx_work_events_project ON work_events(project);
CREATE INDEX IF NOT EXISTS idx_daily_reports_date ON daily_reports(report_date);
CREATE INDEX IF NOT EXISTS idx_weekly_reports_week ON weekly_reports(week_start);

CREATE TABLE IF NOT EXISTS work_memory (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    daily_summary   TEXT DEFAULT '',
    active_projects TEXT DEFAULT '[]',
    recent_activities TEXT DEFAULT '[]',
    work_patterns   TEXT DEFAULT '',
    last_event_id   INTEGER DEFAULT 0,
    total_events_today INTEGER DEFAULT 0,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


class EventStore:
    """SQLite 事件存储.

    线程安全 — 每个线程使用独立的连接，写入操作使用互斥锁.
    """

    def __init__(self, db_path: str = "data/work_reporter.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._local = threading.local()

        # 初始化数据库
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def _init_db(self) -> None:
        """初始化数据库表并执行迁移."""
        with self._write_lock:
            conn = self._get_conn()
            conn.executescript(SCHEMA_SQL)
            self._migrate(conn)
            conn.commit()
        logger.info("数据库已初始化: %s", self.db_path)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """执行数据库迁移 — 为已有数据库添加新列."""
        migrations = [
            "ALTER TABLE work_events ADD COLUMN technologies TEXT DEFAULT '[]'",
            "ALTER TABLE work_events ADD COLUMN task_phase TEXT DEFAULT ''",
            "ALTER TABLE work_events ADD COLUMN context_switch INTEGER DEFAULT 0",
            "ALTER TABLE work_events ADD COLUMN context_note TEXT DEFAULT ''",
            "ALTER TABLE screenshots ADD COLUMN vlm_processed INTEGER DEFAULT 0",
        ]
        for sql in migrations:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    pass  # 列已存在，跳过
                else:
                    logger.warning("数据库迁移失败: %s — SQL: %s", e, sql)

    # ── 截屏记录 ──────────────────────────────────────

    def insert_screenshot(
        self,
        timestamp: datetime,
        file_path: str,
        phash: str,
        app_name: str,
        window_title: str,
        screen_index: int = 0,
        is_duplicate: bool = False,
        skipped: bool = False,
        skip_reason: str = "",
    ) -> int:
        """插入截图记录，返回 ID."""
        with self._write_lock:
            conn = self._get_conn()
            cursor = conn.execute(
                """INSERT INTO screenshots
                   (timestamp, file_path, phash, app_name, window_title,
                    screen_index, is_duplicate, skipped, skip_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    timestamp.isoformat(),
                    file_path,
                    phash,
                    app_name,
                    window_title,
                    screen_index,
                    1 if is_duplicate else 0,
                    1 if skipped else 0,
                    skip_reason,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_screenshots_for_date(self, target_date: date) -> list[dict]:
        """获取指定日期的所有截图（非跳过、非重复）."""
        conn = self._get_conn()
        date_str = target_date.isoformat()
        rows = conn.execute(
            """SELECT * FROM screenshots
               WHERE date(timestamp) = ? AND skipped = 0 AND is_duplicate = 0
               ORDER BY timestamp""",
            (date_str,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_screenshot_count_for_date(self, target_date: date) -> int:
        """获取指定日期的有效截图数量."""
        conn = self._get_conn()
        row = conn.execute(
            """SELECT COUNT(*) as cnt FROM screenshots
               WHERE date(timestamp) = ? AND skipped = 0 AND is_duplicate = 0""",
            (target_date.isoformat(),),
        ).fetchone()
        return row["cnt"] if row else 0

    def get_today_screenshots(self) -> list[dict]:
        """获取今天的有效截图."""
        return self.get_screenshots_for_date(date.today())

    # ── 工作事件 ──────────────────────────────────────

    def insert_work_event(
        self,
        screenshot_id: int,
        timestamp: datetime,
        activity: str,
        category: str = "",
        detail: str = "",
        project: str = "",
        is_productive: bool = True,
        technologies: list | None = None,
        task_phase: str = "",
        context_switch: bool = False,
        context_note: str = "",
        raw_response: str = "",
    ) -> int:
        """插入工作事件，返回 ID."""
        import json as _json
        with self._write_lock:
            conn = self._get_conn()
            cursor = conn.execute(
                """INSERT INTO work_events
                   (screenshot_id, timestamp, activity, category, detail, project,
                    is_productive, technologies, task_phase, context_switch,
                    context_note, raw_response)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    screenshot_id,
                    timestamp.isoformat(),
                    activity,
                    category,
                    detail,
                    project,
                    1 if is_productive else 0,
                    _json.dumps(technologies or [], ensure_ascii=False),
                    task_phase,
                    1 if context_switch else 0,
                    context_note,
                    raw_response,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def update_event_category(self, event_id: int, category: str) -> bool:
        """更新事件的分类标签."""
        with self._write_lock:
            conn = self._get_conn()
            cursor = conn.execute(
                "UPDATE work_events SET category = ? WHERE id = ?",
                (category, event_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_work_events_for_date(self, target_date: date) -> list[dict]:
        """获取指定日期的工作事件."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM work_events
               WHERE date(timestamp) = ?
               ORDER BY timestamp""",
            (target_date.isoformat(),),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_work_events_for_week(self, week_start: date) -> list[dict]:
        """获取指定一周的工作事件（周一到周日）."""
        from datetime import timedelta
        week_end = week_start + timedelta(days=6)
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM work_events
               WHERE date(timestamp) BETWEEN ? AND ?
               ORDER BY timestamp""",
            (week_start.isoformat(), week_end.isoformat()),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_today_events(self) -> list[dict]:
        """获取今天的工作事件."""
        return self.get_work_events_for_date(date.today())

    def get_event_category_stats(self, target_date: date) -> dict[str, int]:
        """获取指定日期的活动类别统计."""
        events = self.get_work_events_for_date(target_date)
        stats: dict[str, int] = {}
        for evt in events:
            cat = evt.get("category") or "其他"
            stats[cat] = stats.get(cat, 0) + 1
        return stats

    def get_event_project_stats(self, target_date: date) -> dict[str, int]:
        """获取指定日期的项目时间统计."""
        events = self.get_work_events_for_date(target_date)
        stats: dict[str, int] = {}
        for evt in events:
            proj = evt.get("project") or "未分类"
            stats[proj] = stats.get(proj, 0) + 1
        return stats

    def get_recent_events(self, limit: int = 50) -> list[dict]:
        """获取最近的工作事件."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM work_events ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ── 日报 ──────────────────────────────────────────

    def insert_daily_report(self, report_date: date, content: str) -> int:
        """插入/更新日报（按日期唯一，存在则更新）."""
        with self._write_lock:
            conn = self._get_conn()
            cursor = conn.execute(
                """INSERT INTO daily_reports (report_date, content, generated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(report_date) DO UPDATE SET
                       content = excluded.content,
                       generated_at = CURRENT_TIMESTAMP""",
                (report_date.isoformat(), content),
            )
            conn.commit()
            return cursor.lastrowid

    def get_daily_report(self, report_date: date) -> dict | None:
        """获取指定日期的日报."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM daily_reports WHERE report_date = ?",
            (report_date.isoformat(),),
        ).fetchone()
        return dict(row) if row else None

    def get_daily_reports_for_week(self, week_start: date) -> list[dict]:
        """获取一周内的日报."""
        from datetime import timedelta
        week_end = week_start + timedelta(days=6)
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM daily_reports
               WHERE report_date BETWEEN ? AND ?
               ORDER BY report_date""",
            (week_start.isoformat(), week_end.isoformat()),
        ).fetchall()
        return [dict(row) for row in rows]

    # ── 周报 ──────────────────────────────────────────

    def insert_weekly_report(self, week_start: date, content: str) -> int:
        """插入/更新周报."""
        with self._write_lock:
            conn = self._get_conn()
            cursor = conn.execute(
                """INSERT INTO weekly_reports (week_start, content, generated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(week_start) DO UPDATE SET
                       content = excluded.content,
                       generated_at = CURRENT_TIMESTAMP""",
                (week_start.isoformat(), content),
            )
            conn.commit()
            return cursor.lastrowid

    def get_weekly_report(self, week_start: date) -> dict | None:
        """获取指定周的周报."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM weekly_reports WHERE week_start = ?",
            (week_start.isoformat(),),
        ).fetchone()
        return dict(row) if row else None

    def get_latest_weekly_report(self) -> dict | None:
        """获取最新的周报."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM weekly_reports ORDER BY week_start DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    # ── 工作记忆 ──────────────────────────────────────

    def get_work_memory(self) -> dict | None:
        """获取工作记忆（单例行）."""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM work_memory WHERE id = 1").fetchone()
        return dict(row) if row else None

    def save_work_memory(self, data: dict) -> None:
        """保存/更新工作记忆."""
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO work_memory (id, daily_summary, active_projects,
                   recent_activities, work_patterns, last_event_id,
                   total_events_today, updated_at)
                   VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       daily_summary = excluded.daily_summary,
                       active_projects = excluded.active_projects,
                       recent_activities = excluded.recent_activities,
                       work_patterns = excluded.work_patterns,
                       last_event_id = excluded.last_event_id,
                       total_events_today = excluded.total_events_today,
                       updated_at = excluded.updated_at""",
                (
                    data.get("daily_summary", ""),
                    data.get("active_projects", "[]"),
                    data.get("recent_activities", "[]"),
                    data.get("work_patterns", ""),
                    data.get("last_event_id", 0),
                    data.get("total_events_today", 0),
                    data.get("updated_at", ""),
                ),
            )
            conn.commit()

    def update_last_event_timestamp(
        self,
        app_name: str,
        window_title: str,
        new_timestamp: datetime,
    ) -> bool:
        """更新最近一条同类 work_event 的 timestamp，返回是否更新成功."""
        with self._write_lock:
            conn = self._get_conn()
            cursor = conn.execute(
                """UPDATE work_events
                   SET timestamp = ?
                   WHERE id = (
                     SELECT id FROM work_events
                     WHERE app_name = ? AND window_title = ?
                     ORDER BY timestamp DESC LIMIT 1
                   )""",
                (new_timestamp.isoformat(), app_name, window_title),
            )
            conn.commit()
            return cursor.rowcount > 0

    # ── 清理 ──────────────────────────────────────────

    def delete_old_screenshots(self, before_date: date) -> int:
        """删除指定日期之前的截图记录和文件."""
        with self._write_lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT file_path FROM screenshots WHERE date(timestamp) < ?",
                (before_date.isoformat(),),
            ).fetchall()
            file_paths = [r[0] for r in rows if r[0]]
            cursor = conn.execute(
                "DELETE FROM screenshots WHERE date(timestamp) < ?",
                (before_date.isoformat(),),
            )
            count = cursor.rowcount
            conn.commit()
            deleted_files = 0
            for fp in file_paths:
                try:
                    if fp and os.path.exists(fp):
                        os.remove(fp)
                        deleted_files += 1
                except Exception:
                    pass
            logger.info("已清理 %d 条截图记录，删除 %d 个文件 (早于 %s)", count, deleted_files, before_date)
            return count

    def get_unprocessed_screenshots(self, target_date: date) -> list[dict]:
        """返回指定日期未处理 VLM 的截图（skipped=0 AND vlm_processed=0）."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT id, timestamp, file_path, app_name, window_title, screen_index
               FROM screenshots
               WHERE date(timestamp) = ?
                 AND skipped = 0
                 AND vlm_processed = 0
               ORDER BY timestamp ASC""",
            (target_date.isoformat(),),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_screenshots_processed(self, ids: list[int]) -> None:
        """批量标记截图已处理."""
        if not ids:
            return
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                "UPDATE screenshots SET vlm_processed = 1 WHERE id IN ({})".format(
                    ",".join("?" * len(ids))
                ),
                ids,
            )
            conn.commit()

    def close(self) -> None:
        """关闭当前线程的数据库连接."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None
