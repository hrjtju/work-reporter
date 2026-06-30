"""视觉分析模块 — 调用本地 MiniCPM-V-4.6 多模态模型分析截图"""

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

import requests
from PIL import Image

logger = logging.getLogger(__name__)


# ── 数据类 ──────────────────────────────────────────────

@dataclass
class VisionAnalysisResult:
    """多模态模型分析结果."""

    activity: str = ""            # 简短描述（≤40字）
    category: str = ""            # 编码|文档|沟通|浏览|会议|设计|调试|研究|其他
    detail: str = ""              # 详细描述，含文件/函数/文档名（≥200字）
    project: str = ""             # 推断的项目名称
    technologies: list = field(default_factory=list)   # 识别的技术栈
    task_phase: str = ""          # 开始|进行中|收尾|调试|审查
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

SCREENSHOT_ANALYSIS_PROMPT = """你是一个桌面活动识别助手。请仔细观察这张屏幕截图，精确描述用户正在做什么。你的核心价值在于**从截图中提取尽可能具体的主题和内容**，而不是给出泛泛的类别标签。

你的回复必须是一个纯 JSON 对象（以左花括号开始、以右花括号结束）。不要输出任何 JSON 之外的内容——不要 markdown 代码块、不要解释、不要前缀或后缀文字。如果无法分析，也必须返回 JSON（confidence 设为 0）。

**注意：请直接输出 JSON，不要进行冗长的内部推理。你的思维过程应当简洁，把主要 token 预算留给 JSON 输出。**

## 已知信息
- 活跃应用：{app_name}
- 窗口标题：{window_title}
- 截图时间：{timestamp}

## 历史上下文
{memory_context}

## 核心原则：主题 > 类别

以下是你必须区分的两个层次：

| 差的描述（太泛） | 好的描述（具体） |
|---|---|
| 「阅读文档」 | 「阅读关于约瑟夫森结临界电流测量的实验论文」 |
| 「写代码」 | 「用 Python 实现变分自编码器的损失函数」 |
| 「学习」 | 「学习实变函数——正在看勒贝格积分的定义和性质」 |
| 「浏览网页」 | 「在 arXiv 上搜索 quantum annealing 的最新预印本」 |
| 「处理数据」 | 「用 Excel 透视表统计 Q2 各区域的销售额分布」 |
| 「开会」 | 「参加周例会，讨论产品搜索功能的性能优化方案」 |
| 「画图」 | 「在 Figma 中调整登录页面的按钮间距和配色」 |
| 「写文档」 | 「撰写磁通量子比特退相干机制的综述段落」 |

**关键规则：activity 和 detail 中必须包含截图上能看到的实质性内容名称**
— 文献标题/论文主题、数学概念名称、代码中的类名/函数名/算法名、数据表的字段名和数值范围、设计图中具体的 UI 组件、聊天中的讨论话题、邮件主题、课程名称、书名……

## 你的任务

请仔细观察截图中可见的**一切文字和视觉线索**，逐层提取：

1. **第一层 — 主题内容**（最重要）：屏幕上在讨论什么具体话题？什么学科/领域/项目？提取所有可见的专有名词、标题、概念名称、公式符号、文件名、数据字段名。
2. **第二层 — 软件工具**：当前打开的是什么软件或网页？什么版本或界面区域？
3. **第三层 — 操作状态**：用户正在阅读？正在输入编辑？正在滚动浏览？正在拖拽调整？光标/选中状态在哪里？
4. **第四层 — 上下文关联**：结合窗口标题、标签页标题、URL、文件路径、邮件主题行等，推断当前任务属于哪个项目或主题。

## 输出格式

```json
{{
  "activity": "用一句话精确描述用户当前在做什么，必须包含具体主题（30字以内）。格式：'[动作] [具体主题]'，如'阅读约瑟夫森结临界电流论文'、'用Python编写VAE损失函数'、'学习实变函数勒贝格积分'",
  "category": "必须严格从以下9个值中选择一个，不允许使用其他值：创作构建、阅读查阅、沟通协作、分析计算、会议讨论、设计绘图、学习研究、娱乐休闲、其他",
  "detail": "详细描述，必须包含：(1)从截图中识别到的具体文字/标题/概念/文件名 (2)使用的软件及界面区域 (3)用户操作状态 (4)推断的任务上下文。至少150字。如果看不清某些文字，描述能看到的任何片段而不是直接放弃。",
  "project": "推断的具体任务或知识领域（如'约瑟夫森结实验研究'、'实变函数课程'、'Q2销售数据分析'、'登录页面重设计'、'量子退火文献调研'）",
  "technologies": ["当前使用的软件、工具、平台名称"],
  "task_phase": "刚启动|进行中|快完成|在检查修改|在探索尝试",
  "context_switch": false,
  "context_note": "与最近一次活动的关系",
  "is_productive": true,
  "confidence": 0.85
}}
```

## 判断指引

### category 分类

- **创作构建** 🔵 — 内容在从无到有地产生 — 写文档/做表/写代码/剪视频/做PPT/画图/P图/写邮件
- **阅读查阅** 🟢 — 内容在被消费理解 — 看网页/读PDF/浏览文档/查资料/刷信息流
- **沟通协作** 🟠 — 与人交流 — 聊天/视频通话/在线会议/评论讨论/协同编辑
- **分析计算** 🟣 — 处理和分析数据 — 做报表/跑脚本看结果/调试/数值计算/数据透视
- **会议讨论** 🔴 — 视频会议界面 — Zoom/Teams/腾讯会议/飞书等
- **设计绘图** 🩵 — 视觉创作 — Figma/PS/AI/CAD/白板/思维导图/流程图
- **学习研究** 🔷 — 系统性知识获取 — 看教程/听课/读论文/读教科书/做笔记/背单词/做题/看教学视频/查维基百科。**此类必须给出与其他类别同等详细的分析，不能敷衍**
- **娱乐休闲** 🩷 — 消遣 — 视频/游戏/音乐/社交媒体/购物 → is_productive 设为 false
- **其他** ⚪ — 无法归入上述类别 — 系统设置/文件管理/终端操作等

**重要：category 字段的值必须是上述9个标签之一（中文，完全一致），不能自创、不能缩写、不能拼接。如果无法确定，使用"其他"。**

### 学习研究类专属指引（重要）

当截屏属于学习研究时，你必须像分析编程项目一样精确。需要从截图中提取：

1. **学科/领域**：什么学科？（数学、物理、计算机、历史、语言、医学……）
2. **具体知识点**：哪个定理/概念/公式/历史事件/语法点？（如"勒贝格控制收敛定理"、"约瑟夫森效应"、"西班牙语虚拟语气"）
3. **学习材料**：什么书/论文/课程/视频？（提取书名、论文标题、课程名、视频标题）
4. **学习方式**：在看书？在看视频？在做题？在做笔记？在查维基百科？
5. **进度**：第几章？第几页？做到第几题？

**差 vs 好**：
| 差 | 好 |
|---|---|
| 「学习数学」 | 「学习实变函数，阅读勒贝格积分章节中关于控制收敛定理的证明」 |
| 「看物理论文」 | 「阅读 PRL 论文，关于超导量子比特中约瑟夫森结的退相干机制」 |
| 「看视频」 | 「在 Bilibili 看 3Blue1Brown 的线性代数本质系列第 5 集」 |
| 「背单词」 | 「用 Anki 背 GRE 高频词汇，当前在 deck 'Barron 3500'」 |
| 「做笔记」 | 「用 Obsidian 整理量子力学笔记，正在写谐振子升降算符推导」 |

### 其他参数

- task_phase：空白/新建=刚启动，内容半满=进行中，内容完整接近完成=快完成，在对比修改=检查修改，在翻菜单/试参数=探索尝试
- context_switch：对比历史上下文，如果话题、任务领域、软件类型与最近记录明显不同，设为 true
- confidence：画面清晰且能明确判断具体主题 → 0.8+；只能推测大致方向 → 0.5 左右；完全无法判断 → 0.0

再次强调：只输出纯 JSON（左花括号开始，右花括号结束）。不要添加任何其他内容。"""

# 日报生成 prompt
DAILY_REPORT_PROMPT = """你是一个专业的工作报告撰写助手。以下是用户今天的工作活动记录：

日期：{date}
总事件数：{event_count}
有效截图：{screenshot_count}

活动时间线：
{events}

请根据以上记录生成一份简洁专业的日报，以 JSON 格式返回：

```json
{{
  "content": "完整的日报内容（Markdown 格式）",
  "summary": "一句话总结今天的工作（30字以内）",
  "tomorrow_plan": "根据今日进展推断的明日计划"
}}
```

要求：
- 按项目分组组织内容
- 突出关键成果和进展
- 语气专业但不过于正式
- 如果有明显的中断/切换模式，可以提及"""

# 周报生成 prompt
WEEKLY_REPORT_PROMPT = """你是一个专业的工作报告撰写助手。以下是用户本周的工作数据汇总：

周期：{week_start} ~ {week_end}
总事件数：{total_events}

每日日报摘要：
{daily_summaries}

类别分布：
{category_distribution}

项目分布：
{project_distribution}

请根据以上数据生成一份专业的周报，以 JSON 格式返回：

```json
{{
  "content": "完整的周报内容（Markdown 格式）",
  "summary": "一周工作总结（50字以内）",
  "key_achievements": ["成果1", "成果2", "成果3"],
  "next_week_plan": "下周计划建议"
}}
```"""


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
        use_tiles = max(img.size) > 1920

        if use_tiles:
            tiles = self._encode_image_tiles(img)
            if not tiles:
                use_tiles = False

        # 构建 prompt
        ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S") if timestamp else ""
        prompt = SCREENSHOT_ANALYSIS_PROMPT.format(
            app_name=app_name or "未知",
            window_title=window_title or "未知",
            timestamp=ts_str,
            memory_context=memory_context or "（暂无历史上下文，这是今天的第一条记录）",
        )

        if use_tiles:
            prompt += (

                "\n\n注意：这张截图被切分成了 2×2 = 4 个瓦片发送给你。"
                "请综合所有瓦片的内容进行分析：左上瓦片 → 右上瓦片 → 左下瓦片 → 右下瓦片。"
                "每张瓦片都是原图的一部分，请拼接理解整体屏幕内容。"
            )

        # 构建消息
        content: list[dict] = [{"type": "text", "text": prompt}]
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

        messages = [{"role": "user", "content": content}]

        # 调用 API
        try:
            response = self._chat_completion(messages)
            result = self._parse_analysis_response(response)
            result.raw_response = response
            logger.debug(
                "VLM 原始响应 (%d 字符, %s): %s",
                len(response), "瓦片" if use_tiles else "单图", response[:200],
            )
            return result
        except Exception as e:
            logger.error("Vision API 调用失败: %s", e)
            self.error_count += 1
            return VisionAnalysisResult(
                activity=f"使用 {app_name or '未知应用'}",
                category="其他",
                detail=f"分析失败: {str(e)[:200]}",
                confidence=0.0,
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
            result = self._parse_report_response(response, "daily")
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
            result = self._parse_report_response(response, "weekly")
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
                        "max_tokens": 8192,
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

    def _text_completion(self, prompt: str) -> str:
        """调用纯文本补全."""
        for attempt in range(self.max_retries):
            try:
                resp = self._session.post(
                    f"{self.base_url}/chat/completions",
                    json={
                        "model": self.model_name,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 4096,
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

    def _parse_report_response(self, raw: str, report_type: str) -> Any:
        """从 LLM 响应中解析报告.

        尝试顺序：json.loads → 手动字符串遍历提取 content 字段 → 去代码围栏后直接用.
        """
        # 尝试 1: 标准 JSON 解析
        try:
            json_str = _extract_json(raw)
            data = json.loads(json_str)
            if report_type == "daily":
                return DailyReportResult(
                    content=data.get("content", raw),
                    summary=data.get("summary", ""),
                    tomorrow_plan=data.get("tomorrow_plan", ""),
                )
            else:
                return WeeklyReportResult(
                    content=data.get("content", raw),
                    summary=data.get("summary", ""),
                    key_achievements=data.get("key_achievements", []),
                    next_week_plan=data.get("next_week_plan", ""),
                )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("JSON 解析报告响应失败: %s, 尝试手动提取", e)

        # 尝试 2: 手动从 JSON 文本中提取 "content" 字段值
        content = _extract_json_field(raw, "content")
        if content and len(content) > 50:
            logger.info("手动提取 content 字段成功 (%d 字符)", len(content))
            if report_type == "daily":
                return DailyReportResult(content=content, summary="")
            return WeeklyReportResult(content=content, summary="")

        # 尝试 3: 去除代码围栏后直接使用
        clean = _strip_markdown_fences(raw)
        logger.warning("content 字段提取失败，使用原始响应")
        if report_type == "daily":
            return DailyReportResult(content=clean, summary="解析失败")
        return WeeklyReportResult(content=clean, summary="解析失败")

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


def _extract_json_field(text: str, field_name: str) -> str:
    """从 LLM 返回的 JSON 文本中手动提取指定字段的值.

    在 json.loads 因转义问题失败时使用。逐个字符遍历，
    正确处理 \\n, \\t, \\", \\\\ 等 JSON 转义序列。
    """
    # 找到字段名的起始位置
    pattern = f'"{field_name}"\\s*:\\s*"'
    match = re.search(pattern, text)
    if not match:
        return ""

    start = match.end()
    result: list[str] = []
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            next_ch = text[i + 1]
            if next_ch == "n":
                result.append("\n")
            elif next_ch == "t":
                result.append("\t")
            elif next_ch == '"':
                result.append('"')
            elif next_ch == "\\":
                result.append("\\")
            elif next_ch == "r":
                result.append("\r")
            else:
                result.append(ch + next_ch)  # 未知转义，保留
            i += 2
        elif ch == '"':
            # 检查是否是该字段的结束引号（后随 , 或 } 加可选空白）
            rest = text[i + 1 : i + 20].lstrip("\r\n\t ")
            if rest and rest[0] in (",", "}"):
                break
            result.append(ch)
            i += 1
        else:
            result.append(ch)
            i += 1

    return "".join(result)


def _strip_markdown_fences(text: str) -> str:
    """去除 Markdown 代码围栏和可能的 JSON 外壳."""
    # 去掉 ```json / ``` 围栏
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.rfind("```")
        if end > start:
            text = text[start:end]
    elif text.startswith("```"):
        nl = text.find("\n")
        if nl > 0:
            text = text[nl + 1 :]
        if text.rstrip().endswith("```"):
            text = text[: text.rfind("```")]

    # 如果仍然看起来是 JSON，尝试提取 content 字段
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        extracted = _extract_json_field(text, "content")
        if extracted:
            return extracted

    return text.strip()
