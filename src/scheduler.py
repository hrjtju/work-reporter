"""调度模块 — 日报/周报定时生成 + 截屏热键监听整合"""

import logging
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler as APScheduler

logger = logging.getLogger(__name__)


class ReportScheduler:
    """报告调度器 — 管理定时任务（日报/周报/清理）.

    Usage:
        sched = ReportScheduler(config, store, generator)
        sched.start()

        # 手动触发
        sched.generate_daily_report_now()
        sched.generate_weekly_report_now()
    """

    def __init__(
        self,
        scheduler_config: dict[str, Any],
        event_store: Any,
        report_generator: Any,
    ):
        """
        Args:
            scheduler_config: config.yaml 中 scheduler 段的配置
            event_store: EventStore 实例
            report_generator: ReportGenerator 实例
        """
        self.config = scheduler_config
        self.store = event_store
        self.generator = report_generator

        self._aps = APScheduler(
            timezone="Asia/Shanghai",  # 默认时区，可通过配置覆盖
            job_defaults={"misfire_grace_time": 300},  # 5分钟容错
        )

        self._running = False

        # 事件回调
        self.on_report_generated: Callable[[str, str], None] | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """启动调度器."""
        if self._running:
            return

        self._setup_jobs()
        self._aps.start()
        self._running = True
        logger.info("报告调度器已启动")

    def stop(self) -> None:
        """停止调度器."""
        if not self._running:
            return
        self._aps.shutdown(wait=False)
        self._running = False
        logger.info("报告调度器已停止")

    def _setup_jobs(self) -> None:
        """配置定时任务."""
        work_days = self.config.get("work_days", [0, 1, 2, 3, 4])
        work_day_str = ",".join(str(d) for d in work_days)

        # 日报定时任务
        daily_time = self.config.get("daily_report_time", "18:00")
        daily_hour, daily_minute = map(int, daily_time.split(":"))
        self._aps.add_job(
            self._generate_daily_report_job,
            "cron",
            day_of_week=work_day_str,
            hour=daily_hour,
            minute=daily_minute,
            id="daily_report",
            name="日报生成",
        )
        logger.info("日报定时: 每周 %s, %s", work_day_str, daily_time)

        # 周报定时任务
        weekly_day = self.config.get("weekly_report_day", 4)  # 周五
        weekly_time = self.config.get("weekly_report_time", "18:30")
        weekly_hour, weekly_minute = map(int, weekly_time.split(":"))
        self._aps.add_job(
            self._generate_weekly_report_job,
            "cron",
            day_of_week=str(weekly_day),
            hour=weekly_hour,
            minute=weekly_minute,
            id="weekly_report",
            name="周报生成",
        )
        weekday_name = ["一", "二", "三", "四", "五", "六", "日"][weekly_day]
        logger.info("周报定时: 每周%s, %s", weekday_name, weekly_time)

        # 清理旧截图任务
        cleanup_time = self.config.get("cleanup_time", "02:00")
        cleanup_hour, cleanup_minute = map(int, cleanup_time.split(":"))
        self._aps.add_job(
            self._cleanup_job,
            "cron",
            hour=cleanup_hour,
            minute=cleanup_minute,
            id="cleanup",
            name="清理旧截图",
        )
        logger.info("清理定时: 每天 %s", cleanup_time)

    # ── 定时任务回调 ──────────────────────────────────

    def _generate_daily_report_job(self) -> None:
        """日报生成定时任务 — 生成昨天的日报."""
        try:
            target = date.today()
            logger.info("⏰ 日报定时任务触发 — %s", target)
            data = self.generator.aggregate_daily(target)
            content = self.generator.generate_daily_with_llm(data)
            if self.on_report_generated:
                self.on_report_generated("daily", content[:200])
        except Exception:
            logger.exception("日报生成失败")

    def _generate_weekly_report_job(self) -> None:
        """周报生成定时任务."""
        try:
            today = date.today()
            week_start = today - timedelta(days=today.weekday())
            logger.info("⏰ 周报定时任务触发 — 起始周 %s", week_start)
            data = self.generator.aggregate_weekly(week_start)
            content = self.generator.generate_weekly_with_llm(data)
            if self.on_report_generated:
                self.on_report_generated("weekly", content[:200])
        except Exception:
            logger.exception("周报生成失败")

    def _cleanup_job(self) -> None:
        """清理旧截图."""
        try:
            keep_days = self.config.get("cleanup_keep_days", 14)
            before = date.today() - timedelta(days=keep_days)
            count = self.store.delete_old_screenshots(before)
            logger.info("🧹 已清理 %d 条旧截图记录 (保留最近 %d 天)", count, keep_days)
        except Exception:
            logger.exception("截图清理失败")

    # ── 手动触发 ──────────────────────────────────────

    def generate_daily_report_now(self, target_date: date | None = None) -> str:
        """手动生成日报.

        Args:
            target_date: 目标日期，默认为今天

        Returns:
            生成的日报内容
        """
        if target_date is None:
            target_date = date.today()
        data = self.generator.aggregate_daily(target_date)
        content = self.generator.generate_daily_with_llm(data)
        if self.on_report_generated:
            self.on_report_generated("daily", content[:200])
        return content

    def generate_weekly_report_now(self, week_start: date | None = None) -> str:
        """手动生成周报.

        Args:
            week_start: 周起始日期（周一），默认为本周一

        Returns:
            生成的周报内容
        """
        if week_start is None:
            today = date.today()
            week_start = today - timedelta(days=today.weekday())
        data = self.generator.aggregate_weekly(week_start)
        content = self.generator.generate_weekly_with_llm(data)
        if self.on_report_generated:
            self.on_report_generated("weekly", content[:200])
        return content

    def get_next_report_times(self) -> dict[str, Optional[datetime]]:
        """获取下一次日报、周报、清理的执行时间."""
        result: dict[str, Optional[datetime]] = {}
        for job_id in ["daily_report", "weekly_report", "cleanup"]:
            job = self._aps.get_job(job_id)
            result[job_id] = job.next_run_time if job else None
        return result
