"""LLM 工作记忆模块 — 逐步积累对用户工作的理解

每次截图分析时，将近期工作上下文注入 prompt，让 LLM 能理解：
- 用户之前在做什么
- 当前活动与之前工作的关联
- 用户的工作模式和项目切换

定期调用 LLM 将近期事件压缩为长期记忆。
"""

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# ── 数据类 ──────────────────────────────────────────────

@dataclass
class MemorySnapshot:
    """工作记忆快照."""

    daily_summary: str = ""          # 今日工作一句话总结
    active_projects: list[str] = field(default_factory=list)  # 活跃项目列表
    recent_activities: list[str] = field(default_factory=list)  # 最近活动简述（最多10条）
    work_patterns: str = ""          # 工作模式描述（如："上午写代码，下午开会"）
    last_event_id: int = 0           # 已处理到的事件 ID
    total_events_today: int = 0      # 今日事件总数
    updated_at: str = ""             # 最后更新时间


# ── 记忆摘要 prompt ─────────────────────────────────────

MEMORY_SUMMARIZE_PROMPT = """你是一个工作记忆管理助手。请根据用户最近的工作活动，更新工作记忆。

## 当前记忆
{current_memory}

## 近期活动（自上次更新以来的新事件）
{new_events}

## 要求
请分析这些活动，更新工作记忆。以 JSON 格式返回：

```json
{{
  "daily_summary": "今日工作一句话总结（30字以内）",
  "active_projects": ["项目A", "项目B"],
  "work_patterns": "工作模式描述，如'上午主要编码，下午有2次会议'",
  "context_for_next": "给下一次截图分析的简短上下文提示（50字以内），帮助理解用户在做什么"
}}
```

注意：
- 如果当前记忆为空，从头构建
- 如果项目已存在于 active_projects，保留它；如果发现新项目，添加进去
- 如果某个项目长时间未出现（超过2小时），可以标记为可能已完成
- 保持记忆简洁，只保留有用信息"""


# ── 工作记忆管理器 ──────────────────────────────────────

class WorkMemory:
    """管理工作记忆的增量更新.

    用法:
        memory = WorkMemory(store, llm)
        context = memory.get_context_for_prompt()  # 获取注入 prompt 的上下文
        memory.on_event_analyzed(result)           # 事件分析后更新记忆
    """

    def __init__(
        self,
        store: Any,                     # EventStore
        llm: Any = None,                # VisionAnalyzer (用于记忆总结)
        summarize_interval: int = 20,   # 每 N 个事件触发一次记忆总结
        max_recent_events: int = 5,     # 注入 prompt 的最近事件数
    ):
        self.store = store
        self.llm = llm
        self.summarize_interval = summarize_interval
        self.max_recent_events = max_recent_events
        self._event_counter = 0         # 自上次总结后的事件数
        self._lock = threading.Lock()

        # 从数据库加载记忆
        self._snapshot = self._load()

    # ── 公开方法 ───────────────────────────────────────

    def get_context_for_prompt(self) -> str:
        """构建注入截图分析 prompt 的上下文文本."""
        with self._lock:
            parts: list[str] = []

            # 今日总结
            if self._snapshot.daily_summary:
                parts.append(f"**今日工作概况**：{self._snapshot.daily_summary}")

            # 活跃项目
            if self._snapshot.active_projects:
                projects = "、".join(self._snapshot.active_projects)
                parts.append(f"**活跃项目**：{projects}")

            # 工作模式
            if self._snapshot.work_patterns:
                parts.append(f"**工作模式**：{self._snapshot.work_patterns}")

            # 最近活动（只取前 N 条，只显示时间和标题）
            try:
                recent = self.store.get_recent_events(self.max_recent_events)
                if recent:
                    lines = ["**最近活动**："]
                    for evt in reversed(recent):
                        ts = evt.get("timestamp", "")
                        time_str = ""
                        if ts:
                            try:
                                t = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
                                time_str = t.strftime("%H:%M")
                            except Exception:
                                pass
                        activity = evt.get("activity", "")
                        lines.append(f"  {time_str} {activity}")
                    parts.append("\n".join(lines))
            except Exception:
                pass

            return "\n\n".join(parts) if parts else "（暂无历史上下文）"

    def on_event_analyzed(self, event: dict) -> None:
        """事件分析完成后调用，触发记忆更新检查.

        Args:
            event: 刚插入的工作事件 dict
        """
        with self._lock:
            self._event_counter += 1
            event_id = event.get("id", 0)
            if event_id > self._snapshot.last_event_id:
                self._snapshot.last_event_id = event_id
            self._snapshot.total_events_today += 1

            # 简单启发式更新活跃项目（无需 LLM）
            project = event.get("project", "")
            if project and project not in self._snapshot.active_projects:
                self._snapshot.active_projects.append(project)
                # 最多保留 8 个项目
                if len(self._snapshot.active_projects) > 8:
                    self._snapshot.active_projects = self._snapshot.active_projects[-8:]

            # 保存基础更新
            self._save()

            # 到达阈值，触发 LLM 深度总结
            if self._event_counter >= self.summarize_interval:
                self._summarize()

    def force_summarize(self) -> None:
        """强制触发记忆总结（如日报生成前调用）."""
        with self._lock:
            self._summarize()

    def get_snapshot(self) -> MemorySnapshot:
        """获取当前记忆快照（只读）."""
        return self._snapshot

    # ── 内部方法 ───────────────────────────────────────

    def _summarize(self) -> None:
        """调用 LLM 将近期事件总结为长期记忆."""
        self._event_counter = 0

        if self.llm is None:
            logger.info("未配置 LLM，跳过记忆总结（使用启发式更新）")
            self._save()
            return

        try:
            # 收集自上次总结以来的新事件
            new_events = self.store.get_recent_events(self.summarize_interval)
            if not new_events:
                return

            events_text = ""
            for evt in reversed(new_events):
                ts = evt.get("timestamp", "")
                time_str = ""
                if ts:
                    try:
                        t = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
                        time_str = t.strftime("%H:%M")
                    except Exception:
                        pass
                events_text += (
                    f"- [{time_str}] [{evt.get('category', '')}] "
                    f"{evt.get('activity', '')}"
                )
                proj = evt.get("project", "")
                if proj:
                    events_text += f" (项目: {proj})"
                detail = evt.get("detail", "")
                if detail:
                    events_text += f" — {detail}"
                events_text += "\n"

            # 构建当前记忆文本
            current = json.dumps({
                "daily_summary": self._snapshot.daily_summary,
                "active_projects": self._snapshot.active_projects,
                "work_patterns": self._snapshot.work_patterns,
            }, ensure_ascii=False, indent=2)

            # 调用 LLM
            prompt = MEMORY_SUMMARIZE_PROMPT.format(
                current_memory=current,
                new_events=events_text,
            )

            response = self.llm.complete_text(prompt)
            data = self._parse_memory_response(response)

            if data:
                self._snapshot.daily_summary = data.get("daily_summary", self._snapshot.daily_summary)
                self._snapshot.active_projects = data.get("active_projects", self._snapshot.active_projects)
                self._snapshot.work_patterns = data.get("work_patterns", self._snapshot.work_patterns)
                self._snapshot.updated_at = datetime.now().isoformat()
                self._save()
                logger.info("工作记忆已更新: %s", self._snapshot.daily_summary)

        except Exception:
            logger.exception("LLM 记忆总结失败")

    def _load(self) -> MemorySnapshot:
        """从数据库加载记忆."""
        try:
            data = self.store.get_work_memory()
            if data:
                return MemorySnapshot(
                    daily_summary=data.get("daily_summary", ""),
                    active_projects=json.loads(data.get("active_projects", "[]")),
                    recent_activities=json.loads(data.get("recent_activities", "[]")),
                    work_patterns=data.get("work_patterns", ""),
                    last_event_id=data.get("last_event_id", 0),
                    total_events_today=data.get("total_events_today", 0),
                    updated_at=data.get("updated_at", ""),
                )
        except Exception:
            logger.exception("加载工作记忆失败")
        return MemorySnapshot()

    def _save(self) -> None:
        """保存记忆到数据库."""
        try:
            self.store.save_work_memory({
                "daily_summary": self._snapshot.daily_summary,
                "active_projects": json.dumps(self._snapshot.active_projects, ensure_ascii=False),
                "recent_activities": json.dumps(self._snapshot.recent_activities, ensure_ascii=False),
                "work_patterns": self._snapshot.work_patterns,
                "last_event_id": self._snapshot.last_event_id,
                "total_events_today": self._snapshot.total_events_today,
                "updated_at": datetime.now().isoformat(),
            })
        except Exception:
            logger.exception("保存工作记忆失败")

    @staticmethod
    def _extract_json(text: str) -> str:
        """从文本中提取 JSON 块."""
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start)
            return text[start:end].strip()
        if "```" in text:
            start = text.index("```") + 3
            end = text.index("```", start)
            return text[start:end].strip()
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            return text[brace_start:brace_end + 1]
        return text

    @staticmethod
    def _parse_memory_response(raw: str) -> dict | None:
        """解析 LLM 记忆更新响应."""
        try:
            json_str = WorkMemory._extract_json(raw)
            return json.loads(json_str)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("解析记忆响应失败: %s", e)
            return None
