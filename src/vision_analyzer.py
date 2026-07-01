"""视觉分析模块 — 通过 OpenAI 兼容 API 调用本地 VLM (Ollama) 分析截图"""

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from typing import Any, Optional

import requests
from PIL import Image

logger = logging.getLogger(__name__)


# ── 数据类 ──────────────────────────────────────────────

@dataclass
class VisionAnalysisResult:
    """多模态模型分析结果."""

    activity: str = ""            # 简短描述（≤30字）
    category: str = ""            # 创作构建|阅读查阅|沟通协作|分析计算|会议讨论|设计绘图|学习研究|娱乐休闲|其他
    detail: str = ""              # 详细描述，含截图具体内容
    project: str = ""             # 推断的项目/知识领域
    technologies: list = field(default_factory=list)   # 识别的软件工具
    task_phase: str = ""          # 刚启动|进行中|快完成|在检查修改|在探索尝试
    context_switch: bool = False  # 是否切换了任务
    context_note: str = ""        # 与之前工作的关联说明
    is_productive: bool = True    # 是否为生产性活动
    confidence: float = 0.0       # 置信度 0-1
    raw_response: str = ""        # 原始返回


@dataclass
class DailyReportResult:
    """日报生成结果."""

    content: str = ""             # 完整日报内容
    summary: str = ""             # 一句话总结
    tomorrow_plan: str = ""       # 明日计划


@dataclass
class WeeklyReportResult:
    """周报生成结果."""

    content: str = ""             # 完整周报内容
    summary: str = ""             # 一周总结
    key_achievements: list[str] = field(default_factory=list)  # 关键成果
    next_week_plan: str = ""      # 下周计划


# ── 常量 ────────────────────────────────────────────────

SYSTEM_PROMPT = """你是桌面截图分析器。你的唯一任务是观察截图并返回一个 JSON 对象描述用户活动。

## 铁律
你的整个回复必须是一个 JSON 对象。不要输出任何 JSON 之外的内容——不要 markdown 代码块、不要解释、不要思考过程、不要前缀后缀文字。如果无法分析，也必须返回 JSON（confidence 设为 0）。

## JSON 结构（严格按此格式输出）
{
  "activity": "用户具体在做什么，必须包含截图中的实质性内容名称（30字内），格式：'动作 具体主题'。例：'阅读约瑟夫森结临界电流论文'、'用Python编写VAE损失函数'、'学习实变函数勒贝格积分定义'",
  "category": "严格从以下9选1：创作构建|阅读查阅|沟通协作|分析计算|会议讨论|设计绘图|学习研究|娱乐休闲|其他",
  "detail": "详细描述截图内容：(1)可见的具体文字/标题/概念/文件名 (2)软件及界面区域 (3)操作状态 (4)任务上下文。尽量具体，至少100字。",
  "project": "推断的任务领域或项目名",
  "technologies": ["使用的软件或工具"],
  "task_phase": "刚启动|进行中|快完成|在检查修改|在探索尝试",
  "context_switch": false,
  "context_note": "与历史上下文的关系",
  "is_productive": true,
  "confidence": 0.85
}

## 9个分类
- 创作构建: 写文档/写代码/做PPT/画图/P图/写邮件/剪视频
- 阅读查阅: 看网页/读PDF/浏览文档/查资料/刷信息流
- 沟通协作: 聊天/视频通话/在线会议/评论讨论/协同编辑
- 分析计算: 做报表/跑脚本/调试/数值计算/数据透视
- 会议讨论: Zoom/Teams/腾讯会议/飞书等视频会议
- 设计绘图: Figma/PS/AI/CAD/白板/思维导图/流程图
- 学习研究: 看教程/听课/读论文/读教科书/做笔记/背单词/做题
- 娱乐休闲: 视频/游戏/音乐/社交媒体/购物 → is_productive=false
- 其他: 系统设置/文件管理/终端操作等

## 要点
- activity 和 detail 必须包含截图中的实质性内容：文字标题、概念名称、文件名、代码中的类名/函数名、数据字段名等
- category 必须严格用上面9个值，不能自创。不确定用"其他"
- 学习研究类同样要具体：提取学科、知识点、材料名、学习方式
- task_phase: 空白/新建→刚启动，内容半满→进行中，接近完成→快完成，在对比→在检查修改，翻菜单/试参数→在探索尝试
- confidence: 能看清具体内容→0.8+，只能推测→0.5，完全无法判断→0.0"""

# 用户消息模板 — 包含动态上下文
USER_CONTEXT_TEMPLATE = """应用: {app_name} | 窗口: {window_title} | 时间: {timestamp}
历史: {memory_context}"""

# 日报生成 prompt

# 日报生成 prompt
DAILY_REPORT_PROMPT = """你是工作日报撰写助手。根据以下活动记录生成 Markdown 日报。

日期：{date} | 事件数：{event_count} | 截图：{screenshot_count}

活动记录：
{events}

要求：
- 直接输出 Markdown（不要 JSON 外壳，不要 markdown 代码围栏）
- # 标题为日期
- ## 今日工作摘要 — 一段话概括
- ## 工作内容 — 按项目分组的详细记录
- ## 时间分布 — 类别占比表格
- ## 备注 — 如有中断/切换模式可提及"""

WEEKLY_REPORT_PROMPT = """你是工作周报撰写助手。根据以下数据生成 Markdown 周报。

周期：{week_start} ~ {week_end} | 事件数：{total_events}

日报摘要：
{daily_summaries}

类别分布：{category_distribution}
项目分布：{project_distribution}

要求：
- 直接输出 Markdown（不要 JSON 外壳，不要 markdown 代码围栏）
- # 标题为周期
- ## 本周总结
- ## 关键成果（列表）
- ## 类别分布（表格）
- ## 项目分布
- ## 下周计划建议"""


# ── 视觉分析器 ──────────────────────────────────────────

class VisionAnalyzer:
    """调用本地或远程多模态大模型进行截图分析.

    支持 OpenAI 兼容 API（llama-server / vLLM / Ollama 等）.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080/v1",
        model_name: str = "minicpm-v",
        api_key: str = "not-needed",
        timeout: int = 120,
        max_retries: int = 3,
    ):
        """
        Args:
            base_url: OpenAI 兼容 API 地址
            model_name: 模型名称
            api_key: API 密钥（本地部署通常不需要）
            timeout: 请求超时秒数
            max_retries: 最大重试次数
        """
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })

        # 统计
        self.call_count: int = 0
        self.error_count: int = 0

    def switch_model(self, model_name: str) -> None:
        """切换模型（用于 GPU 负载过高时降级）."""
        if model_name != self.model_name:
            logger.info("切换模型: %s -> %s", self.model_name, model_name)
            self.model_name = model_name

    def close(self) -> None:
        """关闭 HTTP session，释放连接池."""
        self._session.close()

    # ── 核心方法：截图分析 ────────────────────────────

    def analyze_screenshot(
        self,
        image_path: str,
        app_name: str = "",
        window_title: str = "",
        timestamp: Optional[datetime] = None,
        memory_context: str = "",
    ) -> VisionAnalysisResult:
        """分析单张截图，识别工作活动.

        大图自动切分为 2×2 瓦片以提高 VLM 对文字细节的识别能力.
        """
        # 编码图片为 base64 — 大图使用瓦片模式
        img = Image.open(image_path)
        try:
            use_tiles = max(img.size) > 1920

            if use_tiles:
                tiles = self._encode_image_tiles(img)
                if not tiles:
                    use_tiles = False
        finally:
            img.close()

        # 构建用户消息（动态上下文 + 图片）
        ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S") if timestamp else ""
        user_text = USER_CONTEXT_TEMPLATE.format(
            app_name=app_name or "未知",
            window_title=window_title or "未知",
            timestamp=ts_str,
            memory_context=memory_context or "（暂无历史上下文，这是今天的第一条记录）",
        )

        if use_tiles:
            user_text += (
                "\n注意：截图被切分成了 2×2 = 4 个瓦片。"
                "请综合所有瓦片分析：左上→右上→左下→右下。"
            )

        content: list[dict] = [{"type": "text", "text": user_text}]
        if use_tiles:
            for i, tile_b64 in enumerate(tiles):
                positions = ["左上", "右上", "左下", "右下"]
                label = positions[i] if i < len(positions) else f"瓦片{i+1}"
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{tile_b64}"},
                })
                logger.debug("瓦片 %d (%s): %d chars base64", i + 1, label, len(tile_b64))
        else:
            image_b64 = self._encode_image_single(img)
            if not image_b64:
                return VisionAnalysisResult(
                    activity=f"使用 {app_name or '未知应用'}",
                    category="其他",
                    detail="图片编码失败",
                    confidence=0.0,
                )
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
            })

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]

        # 调用 API（带 JSON 格式校验和重试）
        max_json_retries = 2
        for json_attempt in range(max_json_retries + 1):
            try:
                response = self._chat_completion(messages)
            except Exception as e:
                logger.error("Vision API 调用失败: %s", e)
                self.error_count += 1
                return VisionAnalysisResult(
                    activity=f"使用 {app_name or '未知应用'}",
                    category="其他",
                    detail=f"API 调用失败: {str(e)[:200]}",
                    confidence=0.0,
                )

            # 尝试提取并验证 JSON
            json_str = _extract_json(response)
            json_valid = False
            if json_str:
                try:
                    json.loads(json_str)
                    json_valid = True
                except json.JSONDecodeError:
                    pass

            if json_valid:
                result = self._parse_analysis_response(response)
                result.raw_response = response
                logger.debug(
                    "VLM 原始响应 (%d 字符, %s): %s",
                    len(response), "瓦片" if use_tiles else "单图", response[:200],
                )
                return result

            # JSON 无效 — 重试
            if json_attempt < max_json_retries:
                logger.warning(
                    "VLM 返回非 JSON 格式 (%d/%d)，请求修正...",
                    json_attempt + 1, max_json_retries,
                )
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": (
                        "你的回复不是有效的 JSON。请严格按照 system prompt 中的 JSON 格式输出。"
                        "只输出纯 JSON 对象，以 { 开头、以 } 结尾，不要任何其他内容。"
                    ),
                })
            else:
                logger.warning("VLM JSON 重试耗尽 (%d 次)，使用 fallback", max_json_retries)
                return VisionAnalysisResult(
                    activity=f"使用 {app_name or '未知应用'}",
                    category="其他",
                    detail=response[:300],
                    confidence=0.0,
                    raw_response=response,
                )

    # ── 报告生成方法 ──────────────────────────────────

    def generate_daily_report(
        self,
        events: list[dict],
        report_date: str,
        screenshot_count: int = 0,
    ) -> DailyReportResult:
        """使用 LLM 生成日报.

        Args:
            events: 工作事件列表
            report_date: 报告日期
            screenshot_count: 有效截图数量

        Returns:
            DailyReportResult
        """
        # 构建事件时间线文本
        events_text = ""
        for i, evt in enumerate(events[:100], 1):  # 最多100条
            ts = evt.get("timestamp", "")
            time_str = ""
            if ts:
                try:
                    t = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
                    time_str = t.strftime("%H:%M")
                except Exception:
                    pass
            proj = evt.get("project") or ""
            cat = evt.get("category") or ""
            detail = evt.get("detail") or ""
            events_text += f"{i}. [{time_str}] [{cat}] {evt.get('activity', '')}"
            if proj:
                events_text += f" (项目: {proj})"
            if detail:
                events_text += f" — {detail}"
            events_text += "\n"

        prompt = DAILY_REPORT_PROMPT.format(
            date=report_date,
            event_count=len(events),
            screenshot_count=screenshot_count,
            events=events_text or "暂无事件记录",
        )

        try:
            response = self._text_completion(prompt)
            # Markdown 验证：如果不是有效 markdown，重试
            md = self._validate_markdown(response)
            if md is None:
                logger.warning("日报生成返回非 markdown，重试...")
                retry_prompt = prompt + "\n\n上一次回复不是 Markdown 格式。请直接输出 Markdown 文本，不要 JSON 外壳。"
                response = self._text_completion(retry_prompt)
                md = self._validate_markdown(response) or response
            result = DailyReportResult(content=md, summary="", tomorrow_plan="")
            return result
        except Exception as e:
            logger.error("日报生成失败: %s", e)
            self.error_count += 1
            return DailyReportResult(
                content=f"日报生成失败: {e}",
                summary="生成失败",
                tomorrow_plan="",
            )

    def generate_weekly_report(
        self,
        daily_reports: list[dict],
        all_events: list[dict],
        week_start: str,
        week_end: str,
        category_stats: dict[str, int],
        project_stats: dict[str, int],
    ) -> WeeklyReportResult:
        """使用 LLM 生成周报."""
        # 构建日报摘要
        daily_summaries = ""
        for r in daily_reports[:7]:
            date_str = r.get("report_date", "")
            content = r.get("content", "")
            # 取前150字作为摘要
            summary = content[:150].replace("\n", " ").replace("#", "")
            daily_summaries += f"- {date_str}: {summary}...\n"

        cat_dist = "\n".join(f"- {k}: {v} 次" for k, v in sorted(category_stats.items(), key=lambda x: -x[1]))
        proj_dist = "\n".join(f"- {k}: {v} 次" for k, v in sorted(project_stats.items(), key=lambda x: -x[1]))

        prompt = WEEKLY_REPORT_PROMPT.format(
            week_start=week_start,
            week_end=week_end,
            total_events=len(all_events),
            daily_summaries=daily_summaries or "暂无日报",
            category_distribution=cat_dist or "无数据",
            project_distribution=proj_dist or "无数据",
        )

        try:
            response = self._text_completion(prompt)
            md = self._validate_markdown(response)
            if md is None:
                logger.warning("周报生成返回非 markdown，重试...")
                retry_prompt = prompt + "\n\n上一次回复格式错误。请直接输出 Markdown 文本。"
                response = self._text_completion(retry_prompt)
                md = self._validate_markdown(response) or response
            result = WeeklyReportResult(content=md)
            return result
        except Exception as e:
            logger.error("周报生成失败: %s", e)
            self.error_count += 1
            return WeeklyReportResult(
                content=f"周报生成失败: {e}",
                summary="生成失败",
            )

    # ── API 调用 ──────────────────────────────────────

    def _chat_completion(self, messages: list[dict]) -> str:
        """调用 /v1/chat/completions (带图片)."""
        for attempt in range(self.max_retries):
            try:
                resp = self._session.post(
                    f"{self.base_url}/chat/completions",
                    json={
                        "model": self.model_name,
                        "messages": messages,
                        "max_tokens": 16384,
                        "temperature": 0.1,
                    },
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                self.call_count += 1
                return data["choices"][0]["message"]["content"]
            except requests.exceptions.Timeout:
                logger.warning("Vision API 超时 (尝试 %d/%d)", attempt + 1, self.max_retries)
                if attempt == self.max_retries - 1:
                    raise
            except Exception:
                logger.warning("Vision API 调用失败 (尝试 %d/%d)", attempt + 1, self.max_retries)
                if attempt == self.max_retries - 1:
                    raise
        return ""

    @staticmethod
    def _validate_markdown(text: str) -> str | None:
        """验证并清洗 LLM 输出，提取纯 markdown。返回 None 表示需要重试."""
        if not text or not text.strip():
            return None
        t = text.strip()
        # 1. 剥离 markdown 代码围栏
        if "```" in t:
            start = t.find("```")
            end = t.rfind("```")
            if end > start:
                inner = t[start + 3:end].strip()
                # 跳过语言标记行 (```json, ```markdown 等)
                nl = inner.find("\n")
                if nl > 0 and nl < 20 and not inner[:nl].strip().startswith(("#", "-", "*", "|", ">")):
                    inner = inner[nl + 1:].strip()
                t = inner
        # 2. 优先检测 JSON 包裹（旧格式兼容）
        if t.startswith("{"):
            try:
                data = json.loads(t)
                if isinstance(data, dict) and "content" in data:
                    return data["content"]
            except (json.JSONDecodeError, KeyError):
                pass
        # 3. 检测是否包含 markdown 特征
        if re.search(r"^#+\s", t, re.MULTILINE) or "- " in t or "|" in t:
            return t
        # 4. 无法识别
        return None

    def _text_completion(self, prompt: str) -> str:
        """调用纯文本补全（内部方法，失败时抛异常）."""
        for attempt in range(self.max_retries):
            try:
                resp = self._session.post(
                    f"{self.base_url}/chat/completions",
                    json={
                        "model": self.model_name,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 8192,
                        "temperature": 0.3,
                    },
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                self.call_count += 1
                return data["choices"][0]["message"]["content"]
            except requests.exceptions.Timeout:
                if attempt == self.max_retries - 1:
                    raise
            except Exception:
                if attempt == self.max_retries - 1:
                    raise
        raise RuntimeError("Text completion failed after max retries")

    def complete_text(self, prompt: str) -> str:
        """Public wrapper: 调用纯文本补全，失败时返回空字符串（兼容旧调用方）."""
        try:
            return self._text_completion(prompt)
        except Exception:
            logger.exception("Text completion failed")
            return ""

    # ── 响应解析 ──────────────────────────────────────

    # 匹配 VLM 明确表示无法分析的模式
    _VLM_FAILURE_PATTERNS = [
        "无法识别", "无法提供", "无法确定", "无法判断", "无法看清",
        "cannot identify", "cannot determine", "cannot see", "cannot recognize",
        "unable to", "not clear", "不清楚", "看不清", "截图质量", "image quality",
        "low quality", "模糊", "blurry", "无法分析",
    ]

    # 允许的分类（必须与 prompt 中定义的保持一致）
    ALLOWED_CATEGORIES = {
        "创作构建", "阅读查阅", "沟通协作", "分析计算",
        "会议讨论", "设计绘图", "学习研究", "娱乐休闲", "其他",
    }

    def _parse_analysis_response(self, raw: str) -> VisionAnalysisResult:
        """从 LLM 响应中解析截图分析结果."""
        try:
            # 尝试提取 JSON 块
            json_str = _extract_json(raw)
            data = json.loads(json_str)

            activity = (data.get("activity") or "").strip()
            detail = (data.get("detail") or "").strip()
            confidence = float(data.get("confidence", 0.7))

            # 检测 VLM 的失败响应：如果 activity 或 detail 包含「无法识别」等字样
            combined = activity + " " + detail
            combined_lower = combined.lower()
            for pattern in self._VLM_FAILURE_PATTERNS:
                if pattern in combined_lower:
                    logger.info("VLM 返回失败标志 ('%s')，设为无效分析", pattern)
                    confidence = 0.0
                    break

            # 验证 category 是否为允许的值，不是则回退为"其他"
            category = (data.get("category") or "").strip()
            if category not in self.ALLOWED_CATEGORIES:
                logger.warning(
                    "VLM 返回了未知分类 '%s'，回退为'其他'", category
                )
                category = "其他"

            # 截断过长的 detail
            if len(detail) > 800:
                detail = detail[:797] + "..."

            return VisionAnalysisResult(
                activity=activity,
                category=category,
                detail=detail,
                project=data.get("project", ""),
                technologies=data.get("technologies", []),
                task_phase=data.get("task_phase", ""),
                context_switch=data.get("context_switch", False),
                context_note=data.get("context_note", ""),
                is_productive=data.get("is_productive", True),
                confidence=confidence,
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("解析 Vision 响应失败: %s, 原始: %s", e, raw[:200])
            return VisionAnalysisResult(
                activity=raw[:40].strip(),
                category="其他",
                detail="",
                confidence=0.0,
            )

    # ── 辅助 ──────────────────────────────────────────

    @staticmethod
    def _encode_image_single(img: Image.Image) -> str:
        """将 PIL Image 编码为单张 base64 JPEG 字符串.

        统一使用 JPEG 92（VLM 训练数据以 JPEG 为主，识别效果更好）;
        缩放到 1344px 以匹配大多数 VLM 视觉编码器的原生分辨率.
        """
        try:
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")

            max_dim = 1344
            if max(img.size) > max_dim:
                ratio = max_dim / max(img.size)
                new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
                img = img.resize(new_size, Image.LANCZOS)

            buf = BytesIO()
            img.save(buf, format="JPEG", quality=92)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception as e:
            logger.error("图片编码失败: %s", e)
            return ""

    @staticmethod
    def _encode_image_tiles(img: Image.Image, grid: int = 2) -> list[str]:
        """将大图切分为 grid×grid 瓦片，每片独立编码为 base64 JPEG.

        每个瓦片缩放到 1024px，确保 VLM 视觉编码器不会再次压缩丢失文字细节.
        """
        try:
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")

            w, h = img.size
            tile_w, tile_h = w // grid, h // grid
            tiles: list[str] = []

            for row in range(grid):
                for col in range(grid):
                    x1 = col * tile_w
                    y1 = row * tile_h
                    x2 = x1 + tile_w if col < grid - 1 else w
                    y2 = y1 + tile_h if row < grid - 1 else h

                    tile = img.crop((x1, y1, x2, y2))

                    # 缩放到 1024px 以内
                    tile_max = 1024
                    if max(tile.size) > tile_max:
                        ratio = tile_max / max(tile.size)
                        new_size = (int(tile.size[0] * ratio), int(tile.size[1] * ratio))
                        tile = tile.resize(new_size, Image.LANCZOS)

                    buf = BytesIO()
                    tile.save(buf, format="JPEG", quality=92)
                    tiles.append(base64.b64encode(buf.getvalue()).decode("utf-8"))

            logger.debug("图片切分为 %d×%d = %d 瓦片 (原图 %dx%d)", grid, grid, len(tiles), w, h)
            return tiles
        except Exception as e:
            logger.error("图片瓦片编码失败: %s", e)
            return []

    def check_health(self) -> bool:
        """检查 API 服务是否可用."""
        try:
            resp = self._session.get(
                f"{self.base_url}/models",
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False


def _extract_json(text: str) -> str:
    """从文本中提取 JSON 块."""
    # 尝试 ```json ... ``` 格式
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        return text[start:end].strip()
    # 尝试 ``` ... ``` 格式
    if "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        return text[start:end].strip()
    # 尝试直接找 { ... }
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        return text[brace_start:brace_end + 1]
    return text


