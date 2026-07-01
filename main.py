"""Work Reporter — 自动日报/周报工具 主入口

快捷键截屏 → 隐私过滤 → 事件存储 → 定时生成日报/周报
"""

import logging
import os
import signal
import sys
from datetime import date, datetime
from pathlib import Path
import threading

# Fix Windows console encoding for emoji support
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from src.config_loader import load_config, validate_config, get_project_root
from src.screenshot import ScreenshotCapture, ScreenshotResult
from src.privacy_filter import PrivacyFilter
from src.event_store import EventStore
from src.report_generator import ReportGenerator
from src.scheduler import ReportScheduler
from src.tray_app import TrayApp
from src.web_dashboard import WebDashboard
from src.vision_analyzer import VisionAnalyzer
from src.work_memory import WorkMemory
from src.vlm_queue import VlmTaskQueue, VlmTask

# ── 日志配置 ─────────────────────────────────────────────

def setup_logging(project_root: Path) -> None:
    """配置日志：控制台输出 + 文件输出."""
    log_format = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 控制台
    console = logging.StreamHandler()
    console.setFormatter(log_format)
    console.setLevel(logging.INFO)

    # 文件
    log_dir = project_root / "data"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(
        log_dir / "work_reporter.log", encoding="utf-8"
    )
    file_handler.setFormatter(log_format)
    file_handler.setLevel(logging.DEBUG)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console)
    root_logger.addHandler(file_handler)

    # 减少第三方库的日志噪音
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


# ── 主应用 ───────────────────────────────────────────────

class WorkReporterApp:
    """Work Reporter 主应用 — 协调所有模块."""

    def __init__(self):
        self.project_root = get_project_root()
        setup_logging(self.project_root)

        self.logger = logging.getLogger("work_reporter")
        self.logger.info("=" * 50)
        self.logger.info("Work Reporter 启动中...")

        # 单实例锁 — 防止重复启动
        self._lock_file = self.project_root / "data" / ".work_reporter.pid"
        self._lock_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._acquire_lock():
            self.logger.error("Work Reporter 已在运行中 (PID: %s)，如确认未运行请删除 %s",
                              self._lock_file.read_text().strip() if self._lock_file.exists() else "?",
                              self._lock_file)
            print(f"\n  ERROR: Work Reporter is already running.")
            print(f"  If not, delete: {self._lock_file}\n")
            sys.exit(1)

        # 加载配置
        self.config = load_config()
        errors = validate_config(self.config)
        if errors:
            for e in errors:
                self.logger.error("配置错误: %s", e)
            sys.exit(1)

        # 初始化模块
        self.store = EventStore(self.config["database"]["path"])
        self.generator = ReportGenerator(
            self.store,
            self.config["report"]["output_path"],
            self.config["report"]["format"],
        )
        self.privacy = PrivacyFilter(self.config["privacy"])
        self.screenshot_capture = ScreenshotCapture(
            storage_path=self.config["screenshot"]["storage_path"],
            quality=self.config["screenshot"]["quality"],
            duplicate_threshold=self.config["screenshot"]["duplicate_threshold"],
            max_screenshots_per_day=self.config["screenshot"]["max_screenshots_per_day"],
            hotkey=self.config["screenshot"]["hotkey"],
            pause_hotkey=self.config["screenshot"]["pause_hotkey"],
            auto_interval_minutes=self.config["screenshot"]["auto_interval_minutes"],
            on_capture=self._on_screenshot_captured,
            on_notify=self._on_notify,
        )

        # 调度器
        self.scheduler = ReportScheduler(
            self.config["scheduler"],
            self.store,
            self.generator,
        )

        # 系统托盘
        self.tray = TrayApp(
            callbacks={
                "screenshot": self._manual_screenshot,
                "toggle_pause": self._toggle_pause,
                "toggle_auto": self._toggle_auto,
                "toggle_vlm_auto": self._toggle_vlm_auto,
                "generate_daily": self._manual_daily_report,
                "generate_weekly": self._manual_weekly_report,
                "get_status": self._get_status_text,
                "is_paused": lambda: self.screenshot_capture.is_paused,
                "is_auto": lambda: self.screenshot_capture.is_auto,
                "is_vlm_auto": lambda: self._vlm_auto,
                "open_dashboard": self._open_dashboard,
                "exit": self.shutdown,
                "restart": self._restart_app,
            },
            project_root=self.project_root,
        )

        # Web 仪表盘
        web_port = self.config.get("web_dashboard", {}).get("port", 8765)
        self.web_dashboard = WebDashboard(self, port=web_port)

        # Vision Analyzer（视觉 LLM，用于截图分析）
        self.vision_llm: VisionAnalyzer | None = None
        vcfg = self.config.get("vision_llm", {})
        if vcfg.get("enabled", False):
            try:
                self.vision_llm = VisionAnalyzer(
                    base_url=vcfg.get("base_url", "http://localhost:8080/v1"),
                    model_name=vcfg.get("model", "minicpm-v"),
                    api_key=vcfg.get("api_key", "not-needed"),
                    timeout=vcfg.get("timeout", 120),
                    max_retries=vcfg.get("max_retries", 3),
                )
                # 检查服务是否可用
                if self.vision_llm.check_health():
                    self.logger.info("✅ Vision LLM 已连接: %s", vcfg["base_url"])
                else:
                    self.logger.warning("⚠ Vision LLM 服务不可用 (%s)，将使用规则引擎", vcfg["base_url"])
            except Exception as e:
                self.logger.warning("⚠ Vision LLM 初始化失败: %s，将使用规则引擎", e)

        # Text LLM（用于日报/周报生成）
        self.text_llm: VisionAnalyzer | None = None
        tcfg = self.config.get("text_llm", {})
        if tcfg.get("enabled", False):
            try:
                self.text_llm = VisionAnalyzer(
                    base_url=tcfg.get("base_url", "http://localhost:8080/v1"),
                    model_name=tcfg.get("model", "minicpm-v"),
                    api_key=tcfg.get("api_key", "not-needed"),
                    timeout=tcfg.get("timeout", 120),
                    max_retries=tcfg.get("max_retries", 3),
                )
            except Exception as e:
                self.logger.warning("⚠ Text LLM 初始化失败: %s", e)

        # 将 Text LLM 绑定到 ReportGenerator（使报告生成使用 AI）
        if self.text_llm is not None:
            self.generator.llm = self.text_llm

        # 工作记忆 — LLM 逐步积累对用户工作的理解
        self.work_memory = WorkMemory(
            store=self.store,
            llm=self.text_llm or self.vision_llm,
        )
        self.logger.info("✅ 工作记忆已就绪")

        # VLM 任务队列（生产者/消费者）
        self._vlm_auto = self.config.get("screenshot", {}).get("vlm_auto", True)
        self.vlm_queue = VlmTaskQueue()
        if self._vlm_auto:
            self.vlm_queue.start(workers=2)
            self.logger.info("✅ VLM 队列已启动（自动模式）")
        else:
            self.logger.info("✅ VLM 队列已就绪（手动模式）")

        # GPU 监控 — 高负载时自动切换小模型
        self._gpu_monitor_stop = threading.Event()
        self._gpu_monitor_thread: threading.Thread | None = None
        self._start_gpu_monitor()

        self.logger.info("所有模块初始化完成")
        self._print_startup_info()

    def _start_gpu_monitor(self) -> None:
        """启动 GPU 利用率监控线程，高负载时切换模型."""
        import subprocess
        import re as _re

        def _monitor():
            while not self._gpu_monitor_stop.wait(30):  # 每30秒检查
                try:
                    result = subprocess.run(
                        ["nvidia-smi", "--query-gpu=utilization.gpu",
                         "--format=csv,noheader,nounits"],
                        capture_output=True, text=True, timeout=10,
                    )
                    if result.returncode != 0:
                        continue
                    gpu_pct = int(_re.search(r"\d+", result.stdout).group())
                    if gpu_pct > 50:
                        if self.text_llm and self.text_llm.model_name != "gemma4:e2b":
                            self.text_llm.switch_model("gemma4:e2b")
                            self.logger.info("⚠ GPU %d%% > 50%%, 文本 LLM 降级为 gemma4:e2b", gpu_pct)
                    else:
                        if self.text_llm and self.text_llm.model_name != "gemma4:12b":
                            self.text_llm.switch_model("gemma4:12b")
                            self.logger.info("GPU %d%% ≤ 50%%, 文本 LLM 恢复为 gemma4:12b", gpu_pct)
                except Exception:
                    pass  # nvidia-smi 不可用时静默忽略

        self._gpu_monitor_thread = threading.Thread(
            target=_monitor, daemon=True, name="gpu-monitor",
        )
        self._gpu_monitor_thread.start()

    # ── 单实例锁 ──────────────────────────────────────

    def _acquire_lock(self) -> bool:
        """尝试获取 PID 锁文件，成功返回 True，已存在且进程存活则返回 False."""
        try:
            if self._lock_file.exists():
                old_pid_str = self._lock_file.read_text().strip()
                try:
                    old_pid = int(old_pid_str)
                    # 检查该 PID 是否仍存活
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    handle = kernel32.OpenProcess(0x0400, False, old_pid)  # PROCESS_QUERY_INFORMATION
                    if handle:
                        kernel32.CloseHandle(handle)
                        return False  # 进程仍在运行
                except (ValueError, OSError):
                    pass  # PID 无效或进程已退出，忽略旧锁
            self._lock_file.write_text(str(os.getpid()))
            return True
        except Exception:
            return True  # 获取锁失败也不阻止启动

    def _release_lock(self) -> None:
        """释放 PID 锁文件."""
        try:
            if self._lock_file.exists():
                self._lock_file.unlink()
        except Exception:
            pass

    def _on_screenshot_captured(self, result: ScreenshotResult) -> None:
        """截屏完成后的处理管线 — 隐私检查后入队，异步处理."""
        try:
            if result.skipped:
                # 去重截图也记录到 DB，便于统计
                if result.is_duplicate and result.file_path:
                    self.store.insert_screenshot(
                        timestamp=result.timestamp,
                        file_path=result.file_path,
                        phash=result.phash,
                        app_name=result.app_name,
                        window_title=result.window_title,
                        screen_index=result.screen_index,
                        is_duplicate=True,
                        skipped=True,
                        skip_reason=result.skip_reason,
                    )
                return

            # 隐私过滤（先于 DB 插入）
            privacy_result = self.privacy.process(
                image_path=result.file_path,
                app_name=result.app_name,
                window_title=result.window_title,
            )

            if privacy_result.should_skip:
                self.logger.info(
                    "截图已跳过 (隐私): %s — %s",
                    result.app_name, privacy_result.skip_reason,
                )
                if self.screenshot_capture.is_auto:
                    # 自动模式：删除截图，不留痕迹，不重试
                    try:
                        os.remove(result.file_path)
                        self.logger.info("已删除隐私截图: %s", result.file_path)
                    except Exception:
                        pass
                else:
                    # 手动模式：保留截图记录，标记 skipped
                    self.store.insert_screenshot(
                        timestamp=result.timestamp,
                        file_path=result.file_path,
                        phash=result.phash,
                        app_name=result.app_name,
                        window_title=result.window_title,
                        screen_index=result.screen_index,
                        is_duplicate=False,
                        skipped=True,
                        skip_reason=privacy_result.skip_reason,
                    )
                return

            # 插入截图记录
            ss_id = self.store.insert_screenshot(
                timestamp=result.timestamp,
                file_path=result.file_path,
                phash=result.phash,
                app_name=result.app_name,
                window_title=result.window_title,
                screen_index=result.screen_index,
                is_duplicate=False,
                skipped=False,
                skip_reason="",
            )

            # 入队，由消费者异步处理
            memory_context = self.work_memory.get_context_for_prompt() if self.work_memory else ""
            task = VlmTask(
                screenshot_id=ss_id,
                file_path=result.file_path,
                app_name=result.app_name,
                window_title=result.window_title,
                timestamp=result.timestamp,
                memory_context=memory_context,
                store=self.store,
                vision_llm=self.vision_llm,
                privacy=self.privacy,
                work_memory=self.work_memory,
                is_manual=False,
            )
            self.vlm_queue.put(task)

        except Exception:
            self.logger.exception("处理截图回调失败")

    # ── 手动操作 ──────────────────────────────────────

    def _manual_screenshot(self) -> None:
        """手动触发截屏."""
        self.logger.info("📸 手动截屏触发")
        try:
            results = self.screenshot_capture.capture_all_screens()
            for r in results:
                self._on_screenshot_captured(r)
            valid = [r for r in results if not r.skipped]
            self.tray.notify(
                "Work Reporter",
                f"截屏完成: {len(valid)}/{len(results)} 张有效",
            )
        except Exception as e:
            self.logger.exception("手动截屏失败")
            self.tray.notify("Work Reporter", f"截屏失败: {e}")

    def _toggle_pause(self) -> None:
        """切换暂停状态."""
        self.screenshot_capture._paused = not self.screenshot_capture._paused
        state = "已暂停" if self.screenshot_capture._paused else "已恢复"
        self.tray.update_icon(self.screenshot_capture._paused)
        self.logger.info("截屏%s", state)

    def _toggle_auto(self) -> None:
        """切换自动截屏模式."""
        if self.screenshot_capture.is_auto:
            self.screenshot_capture._stop_auto_capture()
        else:
            self.screenshot_capture._start_auto_capture()

    def _toggle_vlm_auto(self) -> None:
        """切换 VLM 自动/手动模式."""
        self._vlm_auto = not self._vlm_auto
        if self._vlm_auto:
            self.vlm_queue.start(workers=2)
            self.logger.info("VLM 已切换为自动模式，队列消费者已启动")
        else:
            self.vlm_queue.stop()
            self.logger.info("VLM 已切换为手动模式")

    def _process_manual_vlm_batch(self) -> dict:
        """手动触发批量 VLM 处理（手动模式下调用）."""
        today = date.today()
        rows = self.store.get_unprocessed_screenshots(today)
        if not rows:
            return {"processed": 0, "failed": 0, "message": "无待处理截图"}
        tasks = [
            VlmTask(
                screenshot_id=r["id"],
                file_path=r["file_path"],
                app_name=r["app_name"],
                window_title=r["window_title"],
                timestamp=datetime.fromisoformat(r["timestamp"]) if isinstance(r["timestamp"], str) else r["timestamp"],
                memory_context=self.work_memory.get_context_for_prompt() if self.work_memory else "",
                store=self.store,
                vision_llm=self.vision_llm,
                privacy=self.privacy,
                work_memory=self.work_memory,
                is_manual=True,
            )
            for r in rows
        ]
        return self.vlm_queue.process_pending(
            tasks, self.store, self.vision_llm, self.work_memory,
        )

    def _manual_daily_report(self) -> str:
        """手动生成日报."""
        return self.scheduler.generate_daily_report_now()

    def _manual_weekly_report(self) -> str:
        """手动生成周报."""
        return self.scheduler.generate_weekly_report_now()

    def _get_status_text(self) -> str:
        """获取当前状态文本."""
        today = date.today()
        ss_count = self.store.get_screenshot_count_for_date(today)
        evt_count = len(self.store.get_today_events())

        # 截屏模式
        if self.screenshot_capture.is_paused:
            mode_str = "⏸ 已暂停"
        elif self.screenshot_capture.is_auto:
            mode_str = f"🔄 自动 (每 {self.config['screenshot']['auto_interval_minutes']} 分钟)"
        else:
            mode_str = "✋ 手动"

        lines = [
            f"📅 {today.strftime('%Y年%m月%d日')}",
            f"📸 今日截图: {ss_count} 张",
            f"📝 今日事件: {evt_count} 条",
            f"⏯ 截屏模式: {mode_str}",
        ]

        # LLM 状态
        if self.vision_llm is not None:
            v_status = "✅ 已连接" if self.vision_llm.check_health() else "⚠ 断开"
        else:
            v_status = "❌ 未启用"
        lines.append(f"🤖 Vision LLM: {v_status}")

        # 隐私统计
        stats = self.privacy.get_stats()
        lines.append(f"🛡 隐私过滤: 跳过 {stats['skip_count']} 次, 区域模糊 {stats['blur_count']} 次")

        # 下次报告时间
        next_times = self.scheduler.get_next_report_times()
        for job_name, next_time in next_times.items():
            if next_time:
                labels = {
                    "daily_report": "📋 下次日报",
                    "weekly_report": "📊 下次周报",
                    "cleanup": "🧹 下次清理",
                }
                time_str = next_time.strftime("%m/%d %H:%M")
                lines.append(f"{labels.get(job_name, job_name)}: {time_str}")

        return "\n".join(lines)

    # ── 生命周期 ──────────────────────────────────────

    def start(self) -> None:
        """启动所有服务."""
        self.logger.info("启动服务...")

        # 启动热键监听
        self.screenshot_capture.start()

        # 启动调度器
        self.scheduler.start()

        # 启动 Web 仪表盘
        self.web_dashboard.start()

        # 启动系统托盘
        self.tray.start()

        self.logger.info("✅ Work Reporter 已就绪")
        self.logger.info(
            "   截屏快捷键: %s",
            self.config["screenshot"]["hotkey"].upper(),
        )
        self.logger.info(
            "   暂停快捷键: %s",
            self.config["screenshot"]["pause_hotkey"].upper(),
        )
        self.logger.info("   Web 仪表盘: %s", self.web_dashboard.url)
        self.logger.info("   右键托盘图标查看更多操作")

    def shutdown(self) -> None:
        """安全关闭所有服务."""
        self.logger.info("正在关闭...")
        self._gpu_monitor_stop.set()
        self.screenshot_capture.stop()
        self.scheduler.stop()
        self.web_dashboard.stop()
        self.tray.stop()
        if self.vision_llm:
            self.vision_llm.close()
        if self.text_llm:
            self.text_llm.close()
        self.store.close()
        self._release_lock()
        self.logger.info("Work Reporter 已退出")

    def _restart_app(self) -> None:
        """重启应用：关闭所有服务，然后启动新进程."""
        self.logger.info("正在重启...")
        self._gpu_monitor_stop.set()
        self.screenshot_capture.stop()
        self.scheduler.stop()
        self.web_dashboard.stop()
        self.tray.stop()
        if self.vision_llm:
            self.vision_llm.close()
        if self.text_llm:
            self.text_llm.close()
        self.store.close()
        self._release_lock()
        # 启动新进程，然后当前进程退出
        import subprocess, sys
        subprocess.Popen(
            [sys.executable, str(self.project_root / "main.py")],
            cwd=str(self.project_root),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            start_new_session=True,
        )
        self.logger.info("Work Reporter 正在重启...")
        sys.exit(0)  # 退出当前进程，让新进程接管

    def _on_notify(self, title: str, message: str) -> None:
        """发送系统通知（通过托盘气泡）."""
        self.tray.notify(title, message)

    def _open_dashboard(self) -> None:
        """在默认浏览器中打开 Web 仪表盘."""
        import webbrowser
        webbrowser.open(self.web_dashboard.url)

    def run(self) -> None:
        """运行主循环."""
        self.start()

        # 等待信号
        try:
            # 在 Windows 上，使用 threading.Event 保持主线程活跃
            import threading
            stop_event = threading.Event()

            # 注册信号处理
            signal.signal(signal.SIGINT, lambda s, f: stop_event.set())
            signal.signal(signal.SIGTERM, lambda s, f: stop_event.set())

            stop_event.wait()
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def _print_startup_info(self) -> None:
        """Print startup banner (ASCII-safe, no emoji for Windows GBK compat)."""
        print()
        print("+------------------------------------------+")
        print("|        Work Reporter v0.1.0              |")
        print("|        Auto Daily/Weekly Reporter        |")
        print("+------------------------------------------+")
        print(f"|  Manual:      {self.config['screenshot']['hotkey'].upper():<26s}|")
        print(f"|  Pause:       {self.config['screenshot']['pause_hotkey'].upper():<26s}|")
        print(f"|  Dashboard:   {self.web_dashboard.url:<26s}|")
        v_status = "[OK]" if (self.vision_llm and self.vision_llm.check_health()) else "[OFFLINE]"
        print(f"|  Vision LLM:  {v_status:<26s}|")
        print("|  Right-click tray icon for menu          |")
        print("+------------------------------------------+")
        print()


# ── 入口 ─────────────────────────────────────────────────

def main():
    app = WorkReporterApp()
    app.run()


if __name__ == "__main__":
    main()
