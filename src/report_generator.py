"""报告生成模块 — 日报/周报的数据聚合、LLM 生成和模板兜底."""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── 数据类 ──────────────────────────────────────────────

@dataclass
class DailyReportData:
    """日报数据结构."""

    report_date: date
    events: list[dict]           # 当日工作事件列表
    category_stats: dict[str, int]  # 类别统计
    project_stats: dict[str, int]   # 项目统计
    total_screenshots: int          # 有效截图数
    productive_ratio: float         # 生产效率比例


@dataclass
class WeeklyReportData:
    """周报数据结构."""

    week_start: date
    week_end: date
    daily_reports: list[dict]    # 本周各日报
    all_events: list[dict]       # 本周所有事件
    category_stats: dict[str, int]
    project_stats: dict[str, int]
    total_events: int


# ── 报告生成器 ──────────────────────────────────────────

class ReportGenerator:
    """报告生成器 — 聚合事件数据，优先使用 LLM 生成报告，失败时回退到模板."""

    def __init__(self, event_store, output_path: str = "reports", report_format: str = "markdown", llm: Any = None):
        """
        Args:
            event_store: EventStore 实例
            output_path: 报告输出目录
            report_format: 报告格式 (markdown)
            llm: VisionAnalyzer 实例（可选，用于 LLM 生成报告）
        """
        self.store = event_store
        self.output_path = Path(output_path)
        self.report_format = report_format
        self.llm = llm  # VisionAnalyzer 也可用于文本生成

        # 确保输出目录存在
        self.output_path.mkdir(parents=True, exist_ok=True)
        (self.output_path / "daily").mkdir(exist_ok=True)
        (self.output_path / "weekly").mkdir(exist_ok=True)

    # ── 数据聚合 ──────────────────────────────────────

    def aggregate_daily(self, target_date: date | None = None) -> DailyReportData:
        """聚合日报所需数据."""
        if target_date is None:
            target_date = date.today()

        events = self.store.get_work_events_for_date(target_date)
        screenshots = self.store.get_screenshot_count_for_date(target_date)
        cat_stats = self.store.get_event_category_stats(target_date)
        proj_stats = self.store.get_event_project_stats(target_date)

        # 计算生产率
        productive_count = sum(1 for e in events if e.get("is_productive", 0))
        total = len(events)
        productive_ratio = productive_count / total if total > 0 else 0.0

        return DailyReportData(
            report_date=target_date,
            events=events,
            category_stats=cat_stats,
            project_stats=proj_stats,
            total_screenshots=screenshots,
            productive_ratio=productive_ratio,
        )

    def aggregate_weekly(self, week_start: date | None = None) -> WeeklyReportData:
        """聚合周报所需数据."""
        if week_start is None:
            # 本周一
            today = date.today()
            week_start = today - timedelta(days=today.weekday())

        week_end = week_start + timedelta(days=6)

        daily_reports_list = self.store.get_daily_reports_for_week(week_start)
        all_events = self.store.get_work_events_for_week(week_start)

        # 聚合统计
        cat_stats: dict[str, int] = {}
        proj_stats: dict[str, int] = {}
        for evt in all_events:
            cat = evt.get("category") or "其他"
            proj = evt.get("project") or "未分类"
            cat_stats[cat] = cat_stats.get(cat, 0) + 1
            proj_stats[proj] = proj_stats.get(proj, 0) + 1

        return WeeklyReportData(
            week_start=week_start,
            week_end=week_end,
            daily_reports=daily_reports_list,
            all_events=all_events,
            category_stats=cat_stats,
            project_stats=proj_stats,
            total_events=len(all_events),
        )

    # ── 模板生成（LLM 接入前的基于模板版本） ─────────

    def generate_daily_summary(self, data: DailyReportData) -> str:
        """生成基于模板的日报摘要（LLM 接入后替换此方法）.

        Args:
            data: 聚合后的日报数据

        Returns:
            Markdown 格式的日报内容
        """
        date_str = data.report_date.strftime("%Y年%m月%d日")
        weekday_str = ["一", "二", "三", "四", "五", "六", "日"][data.report_date.weekday()]

        lines = [
            f"# 日报 — {date_str} (周{weekday_str})",
            "",
            "## 📋 今日工作摘要",
            "",
        ]

        if not data.events:
            lines.append("今日暂无记录的工作事件。")
        else:
            # 按项目分组
            by_project: dict[str, list[dict]] = {}
            for evt in data.events:
                proj = evt.get("project") or "其他"
                by_project.setdefault(proj, []).append(evt)

            for proj, evts in by_project.items():
                lines.append(f"### {proj}")
                for evt in evts:
                    time_str = ""
                    if ts := evt.get("timestamp"):
                        try:
                            t = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
                            time_str = f" `{t.strftime('%H:%M')}`"
                        except Exception:
                            pass
                    lines.append(f"- {time_str} {evt.get('activity', '未记录')}")
                lines.append("")

        # 时间分布
        lines.append("## ⏱ 时间分布")
        lines.append("")
        if data.category_stats:
            lines.append("| 类别 | 占比 |")
            lines.append("|------|------|")
            total = sum(data.category_stats.values())
            for cat, count in sorted(data.category_stats.items(), key=lambda x: -x[1]):
                pct = f"{count / total * 100:.0f}%" if total > 0 else "0%"
                lines.append(f"| {cat} | {pct} |")
            lines.append("")
        else:
            lines.append("暂无统计信息。")
            lines.append("")

        # 项目分布
        lines.append("## 📂 项目分布")
        lines.append("")
        if data.project_stats:
            for proj, count in sorted(data.project_stats.items(), key=lambda x: -x[1]):
                lines.append(f"- **{proj}**: {count} 个事件")
        else:
            lines.append("暂无项目统计。")
        lines.append("")

        # 生产率
        lines.append("## 📈 额外信息")
        lines.append("")
        lines.append(f"- 有效截图: {data.total_screenshots} 张")
        lines.append(f"- 工作事件: {len(data.events)} 条")
        lines.append(f"- 生产效率: {data.productive_ratio:.0%}")

        content = "\n".join(lines)

        # 保存到数据库
        self.store.insert_daily_report(data.report_date, content)

        # 保存到文件
        file_path = self._save_report("daily", data.report_date.isoformat(), content)
        logger.info("日报已生成: %s", file_path)

        return content

    def generate_weekly_summary(self, data: WeeklyReportData) -> str:
        """生成基于模板的周报摘要.

        Args:
            data: 聚合后的周报数据

        Returns:
            Markdown 格式的周报内容
        """
        start_str = data.week_start.strftime("%Y年%m月%d日")
        end_str = data.week_end.strftime("%Y年%m月%d日")

        lines = [
            f"# 周报 — {start_str} ~ {end_str}",
            "",
            "## 📋 本周工作摘要",
            "",
        ]

        if not data.all_events:
            lines.append("本周暂无记录的工作事件。")
        else:
            # 按项目+日期组织
            by_date: dict[str, list[dict]] = {}
            for evt in data.all_events:
                ts = evt.get("timestamp", "")
                date_key = ts[:10] if ts else "未知"
                by_date.setdefault(date_key, []).append(evt)

            for dk in sorted(by_date.keys()):
                events = by_date[dk]
                lines.append(f"### {dk}")
                for evt in events:
                    time_str = ""
                    if ts := evt.get("timestamp"):
                        try:
                            t = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
                            time_str = f" `{t.strftime('%H:%M')}`"
                        except Exception:
                            pass
                    proj = evt.get("project") or "其他"
                    lines.append(f"- {time_str} [{proj}] {evt.get('activity', '未记录')}")
                lines.append("")

        # 统计
        lines.append("## 📊 本周统计")
        lines.append("")
        lines.append(f"- 总事件数: {data.total_events}")
        lines.append(f"- 日报数: {len(data.daily_reports)}")
        lines.append("")

        lines.append("### 类别分布")
        lines.append("")
        if data.category_stats:
            lines.append("| 类别 | 数量 |")
            lines.append("|------|------|")
            for cat, count in sorted(data.category_stats.items(), key=lambda x: -x[1]):
                lines.append(f"| {cat} | {count} |")
            lines.append("")

        lines.append("### 项目分布")
        lines.append("")
        if data.project_stats:
            for proj, count in sorted(data.project_stats.items(), key=lambda x: -x[1]):
                lines.append(f"- **{proj}**: {count} 个事件")

        content = "\n".join(lines)

        # 保存
        self.store.insert_weekly_report(data.week_start, content)
        file_path = self._save_report("weekly", data.week_start.isoformat(), content)
        logger.info("周报已生成: %s", file_path)

        return content

    # ── LLM 接口 ──────────────────────────────────────

    def generate_daily_with_llm(self, data: DailyReportData) -> str:
        """使用 LLM 生成日报（如果有 LLM 可用），否则回退到模板.

        Args:
            data: 聚合后的日报数据

        Returns:
            LLM 生成的日报内容
        """
        if self.llm is not None:
            try:
                result = self.llm.generate_daily_report(
                    events=data.events,
                    report_date=data.report_date.isoformat(),
                    screenshot_count=data.total_screenshots,
                )
                if result.content:
                    content = result.content
                    # 保存
                    self.store.insert_daily_report(data.report_date, content)
                    file_path = self._save_report("daily", data.report_date.isoformat(), content)
                    logger.info("🤖 LLM 日报已生成: %s", file_path)
                    return content
            except Exception as e:
                logger.warning("LLM 日报生成失败，回退到模板: %s", e)

        logger.info("使用模板生成日报")
        return self.generate_daily_summary(data)

    def generate_weekly_with_llm(self, data: WeeklyReportData) -> str:
        """使用 LLM 生成周报（如果有 LLM 可用），否则回退到模板."""
        if self.llm is not None:
            try:
                result = self.llm.generate_weekly_report(
                    daily_reports=data.daily_reports,
                    all_events=data.all_events,
                    week_start=data.week_start.isoformat(),
                    week_end=data.week_end.isoformat(),
                    category_stats=data.category_stats,
                    project_stats=data.project_stats,
                )
                if result.content:
                    content = result.content
                    self.store.insert_weekly_report(data.week_start, content)
                    file_path = self._save_report("weekly", data.week_start.isoformat(), content)
                    logger.info("🤖 LLM 周报已生成: %s", file_path)
                    return content
            except Exception as e:
                logger.warning("LLM 周报生成失败，回退到模板: %s", e)

        logger.info("使用模板生成周报")
        return self.generate_weekly_summary(data)

    # ── 辅助 ──────────────────────────────────────────

    def _save_report(self, report_type: str, identifier: str, content: str) -> Path:
        """将报告保存为文件."""
        file_name = f"{identifier}.md"
        file_path = self.output_path / report_type / file_name
        file_path.write_text(content, encoding="utf-8")
        return file_path
