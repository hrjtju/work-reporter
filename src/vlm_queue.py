"""VLM 任务队列 — 生产者/消费者模式，异步处理截图分析"""

import json
import logging
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class VlmTask:
    """队列中的 VLM 任务单元."""
    screenshot_id: int
    file_path: str
    app_name: str
    window_title: str
    timestamp: datetime
    memory_context: str
    # 以下字段仅在 process_pending 时需要传入
    store: Optional[object] = None
    vision_llm: Optional[object] = None
    privacy: Optional[object] = None
    work_memory: Optional[object] = None
    is_manual: bool = False  # True=手动触发，False=自动
    retry_count: int = 0  # 已重试次数，避免无限重试


class VlmTaskQueue:
    """VLM 任务生产者/消费者队列."""

    def __init__(self) -> None:
        self._queue: queue.Queue[VlmTask] = queue.Queue()
        self._workers: list[threading.Thread] = []
        self._stop_event = threading.Event()
        self._started = False

    def put(self, task: VlmTask) -> None:
        """生产者：放入一个 VLM 任务（不等待处理完成）."""
        if self._started:
            self._queue.put(task)
            logger.debug("VLM 任务入队: screenshot_id=%d", task.screenshot_id)

    def start(self, workers: int = 2) -> None:
        """启动消费者线程（自动模式）."""
        if self._started:
            return
        self._started = True
        for i in range(workers):
            t = threading.Thread(target=self._worker, name=f"VlmWorker-{i}", daemon=True)
            t.start()
            self._workers.append(t)
        logger.info("VLM 消费者线程已启动 (%d 个 worker)", workers)

    def stop(self) -> None:
        """停止消费者线程（优雅退出）."""
        if not self._started:
            return
        self._stop_event.set()
        # 中止所有 worker
        for _ in self._workers:
            self._queue.put(None)  # poison pill
        for t in self._workers:
            t.join(timeout=5)
        self._workers.clear()
        self._started = False
        logger.info("VLM 消费者线程已停止")

    def process_pending(
        self,
        tasks: list[VlmTask],
        store,
        vision_llm,
        work_memory,
    ) -> dict:
        """手动处理所有待处理任务，返回统计结果."""
        processed = 0
        failed = 0
        for task in tasks:
            task.store = store
            task.vision_llm = vision_llm
            task.work_memory = work_memory
            task.is_manual = True
            try:
                self._process_one(task)
                processed += 1
            except Exception:
                logger.exception("手动 VLM 处理失败: screenshot_id=%d", task.screenshot_id)
                failed += 1
        logger.info("手动批量处理完成: processed=%d, failed=%d", processed, failed)
        return {"processed": processed, "failed": failed}

    def _worker(self) -> None:
        """消费者线程主循环."""
        while not self._stop_event.is_set():
            try:
                task: VlmTask = self._queue.get(timeout=1)
            except queue.Empty:
                continue
            if task is None:  # poison pill
                break
            try:
                self._process_one(task)
            except Exception:
                logger.exception("VLM 任务处理异常: screenshot_id=%d", task.screenshot_id)
                if task.retry_count == 0:
                    task.retry_count = 1
                    self._queue.put(task)  # 重试一次
                    logger.info("VLM 任务已重新入队等待重试: screenshot_id=%d", task.screenshot_id)
                else:
                    logger.warning("VLM 任务处理失败已重试，放弃: screenshot_id=%d", task.screenshot_id)
            finally:
                self._queue.task_done()

    def _process_one(self, task: VlmTask) -> None:
        """处理单个 VLM 任务的核心逻辑."""
        store = task.store
        vision_llm = task.vision_llm
        privacy = task.privacy
        work_memory = task.work_memory

        # VLM 分析
        analysis = None
        if vision_llm is not None:
            try:
                analysis = vision_llm.analyze_screenshot(
                    image_path=task.file_path,
                    app_name=task.app_name,
                    window_title=task.window_title,
                    timestamp=task.timestamp,
                    memory_context=task.memory_context,
                )
                logger.info(
                    "🎯 VLM 分析: [%s] %s (置信度: %.0f%%)",
                    analysis.category, analysis.activity, analysis.confidence * 100,
                )
            except Exception:
                logger.exception("VLM 分析失败，回退到规则引擎: screenshot_id=%d", task.screenshot_id)

        # 构建事件摘要
        if analysis and analysis.confidence > 0.3:
            activity = analysis.activity
            category = analysis.category
            detail = analysis.detail
            project = analysis.project
            productive = analysis.is_productive
            technologies = analysis.technologies
            task_phase = analysis.task_phase
            context_switch = analysis.context_switch
            context_note = analysis.context_note
        else:
            logger.info("VLM 置信度不足/失败，使用规则引擎: screenshot_id=%d", task.screenshot_id)
            activity = f"{task.app_name} — {task.window_title}"
            category = "未分类"
            detail = f"应用: {task.app_name}, 窗口: {task.window_title}"
            project = ""
            productive = True
            technologies = []
            task_phase = ""
            context_switch = False
            context_note = ""

        # 插入事件
        if store:
            event_id = store.insert_work_event(
                screenshot_id=task.screenshot_id,
                timestamp=task.timestamp,
                activity=activity,
                category=category,
                detail=detail,
                project=project,
                is_productive=productive,
                technologies=technologies,
                task_phase=task_phase,
                context_switch=context_switch,
                context_note=context_note,
                raw_response=analysis.raw_response if analysis else "",
            )
            # 标记已处理
            with store._write_lock:
                conn = store._get_conn()
                conn.execute(
                    "UPDATE screenshots SET vlm_processed=1 WHERE id=?",
                    (task.screenshot_id,),
                )
                conn.commit()

            logger.info("✅ 事件已记录: [%s] %s", category, activity)

            # 更新工作记忆
            if work_memory:
                work_memory.on_event_analyzed({
                    "id": event_id,
                    "timestamp": task.timestamp.isoformat(),
                    "activity": activity,
                    "category": category,
                    "detail": detail,
                    "project": project,
                })
