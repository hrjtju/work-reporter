"""截屏模块 — 全局快捷键触发的多显示器截屏，支持感知哈希去重 + 自动定时截屏"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import imagehash
import mss
from PIL import Image
from pynput import keyboard

logger = logging.getLogger(__name__)


# ── 数据类 ──────────────────────────────────────────────

@dataclass
class ScreenshotResult:
    """截屏结果."""

    timestamp: datetime
    file_path: str              # 保存的截图文件路径
    phash: str                  # 感知哈希值（十六进制字符串）
    is_duplicate: bool          # 是否与上一张重复
    app_name: str               # 活跃窗口应用名
    window_title: str           # 活跃窗口标题
    screen_index: int           # 屏幕索引
    skipped: bool = False       # 是否因隐私/重复被跳过（不保存文件）
    skip_reason: str = ""       # 跳过原因


@dataclass
class ActiveWindowInfo:
    """当前活跃窗口信息."""

    app_name: str = ""
    window_title: str = ""


# ── 热键解析 ─────────────────────────────────────────────

# pynput 需要 <modifier>+<key> 格式
# 用户配置使用 "modifier+modifier+key" 格式，这里做转换
_MODIFIER_NAMES = {"ctrl", "alt", "shift", "cmd"}


def _config_to_pynput(hotkey_str: str) -> str:
    """将用户友好的 hotkey 格式转为 pynput 格式.

    "ctrl+shift+p"  →  "<ctrl>+<shift>+p"
    "ctrl+alt+print_screen" → "<ctrl>+<alt>+<print_screen>"
    """
    parts = [p.strip().lower() for p in hotkey_str.split("+")]
    converted = []
    for p in parts:
        if p in _MODIFIER_NAMES:
            converted.append(f"<{p}>")
        elif len(p) == 1:
            converted.append(p)
        else:
            # 特殊键 (f1, print_screen, etc.)
            converted.append(f"<{p}>")
    return "+".join(converted)


def hotkey_to_display(hotkey_str: str) -> str:
    """将快捷键字符串转为显示友好的格式."""
    parts = hotkey_str.split("+")
    return "+".join(p.capitalize() for p in parts)


# ── 截屏管理器 ───────────────────────────────────────────

class ScreenshotCapture:
    """多显示器截屏管理器，通过全局快捷键触发.

    用法:
        capture = ScreenshotCapture(on_capture=handle_screenshot)
        capture.start()   # 开始监听快捷键
        capture.stop()    # 停止监听
    """

    def __init__(
        self,
        storage_path: str = "data/screenshots",
        quality: int = 85,
        duplicate_threshold: int = 5,
        max_screenshots_per_day: int = 200,
        hotkey: str = "ctrl+shift+alt+s",
        pause_hotkey: str = "ctrl+shift+o",
        auto_interval_minutes: int = 2,
        on_capture: Callable[["ScreenshotResult"], None] | None = None,
        on_notify: Callable[[str, str], None] | None = None,
    ):
        self.storage_path = Path(storage_path)
        self.quality = quality
        self.duplicate_threshold = duplicate_threshold
        self.max_per_day = max_screenshots_per_day
        self.hotkey_str = hotkey
        self.pause_hotkey_str = pause_hotkey
        self.auto_interval = auto_interval_minutes * 60  # 转为秒
        self.on_capture = on_capture
        self.on_notify = on_notify

        # 转为 pynput 格式
        self._pynput_hotkey = _config_to_pynput(hotkey)
        self._pynput_pause = _config_to_pynput(pause_hotkey)

        # 状态
        self._paused = False
        self._auto_mode = False       # 自动截屏开关
        self._auto_timer: threading.Timer | None = None
        self._privacy_retry_pending = False
        self._privacy_retry_timer: threading.Timer | None = None
        self._listener: keyboard.GlobalHotKeys | None = None
        self._lock = threading.Lock()

        # 每个显示器的上一张哈希值
        self._last_phashes: dict[int, str] = {}
        # 当日截图计数
        self._today_count: dict[str, int] = {}

        # 确保存储目录存在
        self.storage_path.mkdir(parents=True, exist_ok=True)

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_auto(self) -> bool:
        return self._auto_mode

    @property
    def capture_mode(self) -> str:
        """返回截屏模式: 'auto' 或 'manual'."""
        return "auto" if self._auto_mode else "manual"

    # ── 热键监听 ──────────────────────────────────────

    def start(self) -> None:
        """开始监听全局快捷键."""
        if self._listener is not None:
            logger.warning("快捷键监听已在运行中")
            return

        hotkey_combos = {
            self._pynput_hotkey: self._on_hotkey,
            self._pynput_pause: self._on_pause_hotkey,
        }
        self._listener = keyboard.GlobalHotKeys(hotkey_combos)
        self._listener.start()
        logger.info(
            "快捷键监听已启动 — 手动: %s, 暂停: %s (自动截屏请在托盘菜单或仪表盘开启)",
            hotkey_to_display(self.hotkey_str),
            hotkey_to_display(self.pause_hotkey_str),
        )

    def stop(self) -> None:
        """停止快捷键监听."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
            logger.info("快捷键监听已停止")

    def _on_hotkey(self) -> None:
        """快捷键按下 — 执行截屏."""
        if self._paused:
            logger.debug("截屏已暂停，忽略快捷键")
            return
        try:
            results = self.capture_all_screens()
            if self.on_capture:
                for result in results:
                    self.on_capture(result)
            # 弹窗通知
            if self.on_notify:
                valid = [r for r in results if not r.skipped]
                dup = [r for r in results if r.is_duplicate]
                if dup and not valid:
                    self.on_notify("Work Reporter", "截屏已跳过（与上一张重复）")
                elif valid:
                    self.on_notify("Work Reporter", f"📸 已截屏 — {len(valid)} 张有效")
        except Exception:
            logger.exception("截屏失败")
            if self.on_notify:
                self.on_notify("Work Reporter", "截屏失败，请查看日志")

    def _on_pause_hotkey(self) -> None:
        """暂停/恢复快捷键按下."""
        self._paused = not self._paused
        if self._paused:
            self._stop_auto_capture()
        state = "已暂停" if self._paused else "已恢复"
        logger.info("截屏%s (快捷键: %s)", state, hotkey_to_display(self.pause_hotkey_str))
        if self.on_notify:
            self.on_notify("Work Reporter", f"截屏{state}")

    def _start_auto_capture(self) -> None:
        """开启自动截屏."""
        self._auto_mode = True
        self._paused = False
        logger.info("自动截屏已开启 (间隔 %d 分钟)", self.auto_interval // 60)
        self._schedule_auto_tick()

    def _stop_auto_capture(self) -> None:
        """关闭自动截屏."""
        self._auto_mode = False
        if self._auto_timer is not None:
            self._auto_timer.cancel()
            self._auto_timer = None
        if self._privacy_retry_timer is not None:
            self._privacy_retry_timer.cancel()
            self._privacy_retry_timer = None
            self._privacy_retry_pending = False
        logger.info("自动截屏已关闭")

    def _schedule_auto_tick(self) -> None:
        """安排下一次自动截屏."""
        if not self._auto_mode:
            return
        self._auto_timer = threading.Timer(self.auto_interval, self._auto_capture_tick)
        self._auto_timer.daemon = True
        self._auto_timer.start()

    def _auto_capture_tick(self) -> None:
        """自动截屏定时触发."""
        if not self._auto_mode or self._paused:
            return
        try:
            results = self.capture_all_screens()
            if self.on_capture:
                for result in results:
                    self.on_capture(result)
            logger.debug("自动截屏完成 — %d 张", len(results))
        except Exception:
            logger.exception("自动截屏失败")
        finally:
            if not self._privacy_retry_pending:
                self._schedule_auto_tick()

    def schedule_privacy_retry(self) -> None:
        """隐私跳过时安排一次 2 分钟后的重试（仅一次）."""
        if not self._auto_mode or self._privacy_retry_pending:
            return
        self._privacy_retry_pending = True
        logger.info("🛡 隐私跳过，2 分钟后重试一次")
        self._privacy_retry_timer = threading.Timer(120, self._privacy_retry_capture)
        self._privacy_retry_timer.daemon = True
        self._privacy_retry_timer.start()

    def _privacy_retry_capture(self) -> None:
        """隐私重试截屏."""
        self._privacy_retry_pending = False
        if not self._auto_mode or self._paused:
            self._schedule_auto_tick()
            return
        try:
            results = self.capture_all_screens()
            if self.on_capture:
                for result in results:
                    self.on_capture(result)
            logger.info("🛡 隐私重试截屏完成")
        except Exception:
            logger.exception("隐私重试截屏失败")
        finally:
            self._schedule_auto_tick()

    # ── 截屏逻辑 ──────────────────────────────────────

    @staticmethod
    def get_active_window_info() -> ActiveWindowInfo:
        """获取当前活跃窗口信息."""
        info = ActiveWindowInfo()
        try:
            import pygetwindow as gw
            active = gw.getActiveWindow()
            if active is not None:
                info.window_title = active.title or ""
                info.app_name = _extract_app_name(active.title or "")
        except Exception as e:
            logger.debug("获取活跃窗口信息失败: %s", e)
        return info

    def capture_all_screens(self) -> list[ScreenshotResult]:
        """截取所有显示器，返回结果列表."""
        results: list[ScreenshotResult] = []
        window_info = self.get_active_window_info()

        with self._lock:
            with mss.mss() as sct:
                monitors = sct.monitors
                # monitors[0] 是虚拟全屏，跳过
                for i, monitor in enumerate(monitors[1:], start=1):
                    result = self._capture_single(sct, monitor, i, window_info)
                    results.append(result)

        return results

    def _capture_single(
        self,
        sct: mss.mss,
        monitor: dict,
        screen_index: int,
        window_info: ActiveWindowInfo,
    ) -> ScreenshotResult:
        """截取单个显示器并处理去重."""
        now = datetime.now()

        # 截取屏幕
        sct_img = sct.grab(monitor)
        pil_img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

        # 计算感知哈希
        phash_value = imagehash.phash(pil_img)
        phash_str = str(phash_value)

        # 去重检查
        is_dup = False
        if screen_index in self._last_phashes:
            try:
                prev_hash = imagehash.hex_to_hash(self._last_phashes[screen_index])
                distance = phash_value - prev_hash
                if distance <= self.duplicate_threshold:
                    is_dup = True
                    logger.debug(
                        "屏幕 %d: 与上一张重复 (距离=%d, 阈值=%d)，跳过保存",
                        screen_index, distance, self.duplicate_threshold,
                    )
            except Exception:
                pass

        self._last_phashes[screen_index] = phash_str

        if is_dup:
            return ScreenshotResult(
                timestamp=now,
                file_path="",
                phash=phash_str,
                is_duplicate=True,
                app_name=window_info.app_name,
                window_title=window_info.window_title,
                screen_index=screen_index,
                skipped=True,
                skip_reason="duplicate",
            )

        file_path = self._save_screenshot(pil_img, now, screen_index)

        return ScreenshotResult(
            timestamp=now,
            file_path=file_path,
            phash=phash_str,
            is_duplicate=False,
            app_name=window_info.app_name,
            window_title=window_info.window_title,
            screen_index=screen_index,
            skipped=False,
        )

    def _save_screenshot(self, img: Image.Image, timestamp: datetime, screen_index: int) -> str:
        """保存截图到按日期组织的目录."""
        date_str = timestamp.strftime("%Y-%m-%d")
        date_dir = self.storage_path / date_str
        date_dir.mkdir(parents=True, exist_ok=True)

        if date_str not in self._today_count:
            self._today_count[date_str] = 0
        self._today_count[date_str] += 1

        if self._today_count[date_str] > self.max_per_day:
            self._cleanup_oldest_in_dir(date_dir)

        time_str = timestamp.strftime("%H-%M-%S")
        filename = f"{time_str}_screen{screen_index}.png"
        filepath = date_dir / filename

        img.save(filepath, "PNG", optimize=True)
        logger.info("截图已保存: %s", filepath)

        return str(filepath)

    def _cleanup_oldest_in_dir(self, dir_path: Path) -> None:
        """删除目录中最旧的文件."""
        try:
            files = sorted(
                dir_path.glob("*.png"),
                key=lambda p: p.stat().st_mtime,
            )
            if files:
                files[0].unlink()
                logger.debug("已删除最旧截图: %s", files[0])
        except Exception as e:
            logger.warning("清理旧截图失败: %s", e)


# ── 辅助函数 ─────────────────────────────────────────────

def _extract_app_name(window_title: str) -> str:
    """从窗口标题提取应用名称."""
    if not window_title:
        return ""
    for sep in [" - ", " — ", " | ", " – "]:
        if sep in window_title:
            parts = window_title.split(sep)
            if len(parts) >= 2:
                return parts[-1].strip()
    return window_title.strip()
