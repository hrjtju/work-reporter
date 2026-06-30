"""配置加载模块 — 读取和验证 config.yaml"""

import os
from pathlib import Path
from typing import Any

import yaml

# 默认配置（当 config.yaml 缺失或字段不完整时使用）
DEFAULT_CONFIG: dict[str, Any] = {
    "screenshot": {
        "hotkey": "ctrl+shift+alt+s",
        "pause_hotkey": "ctrl+shift+o",
        "auto_interval_minutes": 2,
        "quality": 85,
        "duplicate_threshold": 5,
        "storage_path": "data/screenshots",
        "max_screenshots_per_day": 200,
    },
    "privacy": {
        "app_blacklist": [],
        "blur_regions": [],
        "content_detection": {"enabled": False, "patterns": {}},
        "title_blacklist": [],
    },
    "scheduler": {
        "work_hours_start": "09:00",
        "work_hours_end": "20:00",
        "work_days": [0, 1, 2, 3, 4],
        "daily_report_time": "18:00",
        "weekly_report_day": 4,
        "weekly_report_time": "18:30",
        "cleanup_time": "02:00",
        "cleanup_keep_days": 14,
    },
    "database": {"path": "data/work_reporter.db"},
    "report": {"output_path": "reports", "format": "markdown"},
}


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并配置字典：override 覆盖 base 中的值."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_path: str | None = None) -> dict[str, Any]:
    """加载配置文件，返回合并默认值后的完整配置.

    Args:
        config_path: 配置文件路径，默认在项目根目录查找 config.yaml

    Returns:
        合并默认值后的配置字典
    """
    if config_path is None:
        # 从当前文件向上找到项目根目录
        project_root = Path(__file__).resolve().parent.parent
        config_path = str(project_root / "config.yaml")

    config = DEFAULT_CONFIG.copy()

    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
        config = _deep_merge(config, user_config)

    return config


def validate_config(config: dict[str, Any]) -> list[str]:
    """验证配置的合理性，返回错误列表（空列表表示配置有效）."""
    errors: list[str] = []

    sc = config.get("screenshot", {})
    hotkey = sc.get("hotkey", "ctrl+shift+p")
    if not isinstance(hotkey, str) or "+" not in hotkey:
        errors.append("screenshot.hotkey 格式无效，应为 'modifier+key'")
    pause_hk = sc.get("pause_hotkey", "ctrl+shift+o")
    if not isinstance(pause_hk, str) or "+" not in pause_hk:
        errors.append("screenshot.pause_hotkey 格式无效，应为 'modifier+key'")
    if hotkey == pause_hk:
        errors.append("screenshot.hotkey 和 pause_hotkey 不能相同")

    dup_threshold = sc.get("duplicate_threshold", 5)
    if not isinstance(dup_threshold, int) or dup_threshold < 0:
        errors.append("screenshot.duplicate_threshold 必须 >= 0")

    sch = config.get("scheduler", {})
    try:
        from datetime import datetime
        datetime.strptime(sch.get("work_hours_start", "09:00"), "%H:%M")
        datetime.strptime(sch.get("work_hours_end", "20:00"), "%H:%M")
        datetime.strptime(sch.get("daily_report_time", "18:00"), "%H:%M")
        datetime.strptime(sch.get("weekly_report_time", "18:30"), "%H:%M")
    except ValueError as e:
        errors.append(f"时间格式错误: {e}")

    work_days = sch.get("work_days", [])
    if not isinstance(work_days, list) or not all(0 <= d <= 6 for d in work_days):
        errors.append("scheduler.work_days 必须是 0-6 的整数列表")

    return errors


def get_project_root() -> Path:
    """返回项目根目录的绝对路径."""
    return Path(__file__).resolve().parent.parent
