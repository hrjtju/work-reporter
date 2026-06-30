"""系统托盘模块 — 提供系统托盘图标和快捷操作菜单"""

import logging
import threading
import webbrowser
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Optional

from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)


# ── 托盘图标绘制 ────────────────────────────────────────

def _create_icon_image(size: int = 64, color: str = "#4A90D9") -> Image.Image:
    """创建托盘图标 — 圆形 + 字母 W（Work）."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = 4
    # 外圆
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=color,
    )

    # 字母 W
    try:
        from PIL import ImageFont
        font_size = int(size * 0.55)
        try:
            font = ImageFont.truetype("segoeui.ttf", font_size)
        except Exception:
            font = ImageFont.truetype("arial.ttf", font_size)
        bbox = draw.textbbox((0, 0), "W", font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (size - tw) / 2
        y = (size - th) / 2 - 1
        draw.text((x, y), "W", fill="white", font=font)
    except Exception:
        # 如果找不到字体，画简单标记
        draw.line([(size // 3, size // 2), (size // 2, size // 3)], fill="white", width=3)
        draw.line([(size // 2, size // 3), (size * 2 // 3, size // 2)], fill="white", width=3)

    return img


def _create_paused_icon(size: int = 64) -> Image.Image:
    """创建暂停状态图标 — 灰色."""
    return _create_icon_image(size, color="#999999")


# ── 系统托盘应用 ────────────────────────────────────────

class TrayApp:
    """系统托盘应用.

    提供:
    - 托盘图标（正常/暂停状态切换）
    - 右键菜单：截屏状态、手动生成报告、查看今天事件、打开报告目录、退出
    - 截屏完成后的通知气泡
    """

    def __init__(
        self,
        callbacks: dict[str, Callable],
        project_root: Optional[Path] = None,
    ):
        """
        Args:
            callbacks: 回调函数字典
                - "screenshot": 手动触发截屏
                - "toggle_pause": 切换暂停状态
                - "generate_daily": 生成今日日报
                - "generate_weekly": 生成本周周报
                - "get_status": 获取当前状态文本
                - "is_paused": 检查是否暂停中
                - "exit": 退出程序
            project_root: 项目根目录
        """
        self.callbacks = callbacks
        self.project_root = project_root or Path.cwd()
        self._tray_icon: Any = None
        self._running = False

    def start(self) -> None:
        """启动系统托盘."""
        if self._running:
            return

        import pystray

        icon_img = _create_icon_image()

        # 构建菜单
        menu = pystray.Menu(
            pystray.MenuItem(
                "📸 立即截屏",
                self._on_screenshot,
                default=True,
            ),
            pystray.MenuItem(
                "⏯ 暂停/恢复截屏",
                self._on_toggle_pause,
            ),
            pystray.MenuItem(
                "🔄 自动截屏 开/关",
                self._on_toggle_auto,
            ),
            pystray.MenuItem(
                "🌐 打开仪表盘",
                self._on_open_dashboard,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "📋 查看今日事件",
                self._on_view_today,
            ),
            pystray.MenuItem(
                "📄 生成今日日报",
                self._on_generate_daily,
            ),
            pystray.MenuItem(
                "📊 生成本周周报",
                self._on_generate_weekly,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "📁 打开报告目录",
                self._on_open_reports,
            ),
            pystray.MenuItem(
                "🖼 打开截图目录",
                self._on_open_screenshots,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "ℹ 状态",
                self._on_status,
                enabled=False,
            ),
            pystray.MenuItem(
                "❌ 退出",
                self._on_exit,
            ),
        )

        self._tray_icon = pystray.Icon(
            "work_reporter",
            icon_img,
            "Work Reporter — 工作记录中...",
            menu,
        )

        # 启动托盘（在后台线程）
        tray_thread = threading.Thread(
            target=self._tray_icon.run, daemon=True, name="tray"
        )
        tray_thread.start()
        self._running = True
        logger.info("系统托盘已启动")

    def stop(self) -> None:
        """停止系统托盘."""
        if self._tray_icon is not None:
            self._tray_icon.stop()
            self._running = False
            logger.info("系统托盘已停止")

    def notify(self, title: str, message: str) -> None:
        """显示通知气泡."""
        if self._tray_icon is not None:
            try:
                self._tray_icon.notify(message, title)
            except NotImplementedError:
                # pystray notify 在某些平台不可用
                logger.info("🔔 %s: %s", title, message)

    def update_icon(self, paused: bool) -> None:
        """根据暂停状态切换图标."""
        if self._tray_icon is not None:
            self._tray_icon.icon = _create_paused_icon() if paused else _create_icon_image()
            status = "已暂停" if paused else "运行中"
            self._tray_icon.title = f"Work Reporter — {status}"

    # ── 菜单回调 ──────────────────────────────────────

    def _on_screenshot(self, icon, item) -> None:
        cb = self.callbacks.get("screenshot")
        if cb:
            cb()
        else:
            self.notify("Work Reporter", "截屏功能未就绪")

    def _on_toggle_pause(self, icon, item) -> None:
        cb = self.callbacks.get("toggle_pause")
        if cb:
            cb()
            is_paused = False
            check = self.callbacks.get("is_paused")
            if check:
                is_paused = check()
            self.update_icon(is_paused)
            state = "已暂停" if is_paused else "已恢复"
            self.notify("Work Reporter", f"截屏{state}")

    def _on_toggle_auto(self, icon, item) -> None:
        cb = self.callbacks.get("toggle_auto")
        if cb:
            cb()
            is_auto = False
            check = self.callbacks.get("is_auto")
            if check:
                is_auto = check()
            state = "自动截屏已开启" if is_auto else "自动截屏已关闭，恢复手动"
            self.notify("Work Reporter", state)

    def _on_generate_daily(self, icon, item) -> None:
        cb = self.callbacks.get("generate_daily")
        if cb:
            self.notify("Work Reporter", "正在生成日报...")
            try:
                content = cb()
                self.notify("Work Reporter", f"日报生成完成\n{content[:100]}...")
            except Exception as e:
                self.notify("Work Reporter", f"日报生成失败: {e}")

    def _on_generate_weekly(self, icon, item) -> None:
        cb = self.callbacks.get("generate_weekly")
        if cb:
            self.notify("Work Reporter", "正在生成周报...")
            try:
                content = cb()
                self.notify("Work Reporter", f"周报生成完成\n{content[:100]}...")
            except Exception as e:
                self.notify("Work Reporter", f"周报生成失败: {e}")

    def _on_view_today(self, icon, item) -> None:
        cb = self.callbacks.get("get_status")
        if cb:
            status = cb()
            self.notify("今日状态", status)
        else:
            # 尝试打开今天的日报文件
            today_str = date.today().isoformat()
            report_path = self.project_root / "reports" / "daily" / f"{today_str}.md"
            if report_path.exists():
                webbrowser.open(str(report_path))
            else:
                self.notify("Work Reporter", "今日暂无日报，请先截屏记录工作")

    def _on_open_reports(self, icon, item) -> None:
        report_dir = self.project_root / "reports"
        if report_dir.exists():
            webbrowser.open(str(report_dir))

    def _on_open_screenshots(self, icon, item) -> None:
        ss_dir = self.project_root / "data" / "screenshots"
        if ss_dir.exists():
            webbrowser.open(str(ss_dir))

    def _on_status(self, icon, item) -> None:
        pass  # 只读菜单项，显示当前状态

    def _on_open_dashboard(self, icon, item) -> None:
        cb = self.callbacks.get("open_dashboard")
        if cb:
            cb()
        else:
            self.notify("Work Reporter", "仪表盘尚未启动")

    def _on_exit(self, icon, item) -> None:
        cb = self.callbacks.get("exit")
        if cb:
            cb()
        self.stop()
