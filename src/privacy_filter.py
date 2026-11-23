"""隐私过滤模块 — 三层隐私保护：窗口过滤、区域模糊、OCR 内容检测"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)


# ── 数据类 ──────────────────────────────────────────────

@dataclass
class PrivacyResult:
    """隐私过滤结果."""

    should_skip: bool = False          # 是否完全跳过这张截图
    skip_reason: str = ""              # 跳过原因
    filtered_image: Image.Image | None = None  # 过滤后的图片（None 表示未加载或跳过）
    blurred_regions: list[str] = field(default_factory=list)  # 被模糊的区域描述
    ocr_matches: list[dict] = field(default_factory=list)     # OCR 检测到的敏感内容


@dataclass
class BlurRegion:
    """模糊区域定义."""

    name: str
    x_start: float  # 0.0 - 1.0
    x_end: float
    y_start: float  # 0.0 - 1.0
    y_end: float


# ── 隐私过滤器 ───────────────────────────────────────────

class PrivacyFilter:
    """三层隐私保护过滤器.

    Layer 1: 应用/窗口标题黑名单 — 零成本，最优先
    Layer 2: 预定义区域模糊 — 低成本（任务栏、地址栏等）
    Layer 3: OCR 内容门禁 — 中成本；识别屏幕文字后按关键词/正则判定，
             命中隐私、成人/R18 等内容时**不把截图送给云端 LLM**（默认整图删除）
    """

    def __init__(self, privacy_config: dict[str, Any]):
        """初始化隐私过滤器.

        Args:
            privacy_config: config.yaml 中 privacy 段的配置字典
        """
        # Layer 1
        self.app_blacklist: list[str] = [
            s.lower().strip() for s in privacy_config.get("app_blacklist", [])
        ]
        self.title_blacklist: list[str] = [
            s.lower().strip() for s in privacy_config.get("title_blacklist", [])
        ]

        # Layer 2
        self.blur_regions: list[BlurRegion] = []
        for region in privacy_config.get("blur_regions", []):
            self.blur_regions.append(BlurRegion(
                name=region.get("name", "unknown"),
                x_start=region.get("x_start", 0.0),
                x_end=region.get("x_end", 1.0),
                y_start=region.get("y_start", 0.0),
                y_end=region.get("y_end", 1.0),
            ))

        # Layer 3 —— OCR 内容门禁
        cd = privacy_config.get("content_detection", {})
        self.ocr_enabled = cd.get("enabled", False)
        self.ocr_engine = cd.get("engine", "windows_ocr")     # windows_ocr | tesseract
        self.win_ocr_language = cd.get("language", "zh-Hans-CN")
        self.tesseract_language = cd.get("tesseract_language", "chi_sim+eng")
        self.tesseract_path = cd.get("tesseract_path", "")
        self.blur_radius = cd.get("blur_radius", 25)
        self.max_image_dim = cd.get("max_image_dim", 4000)
        self.fail_closed = cd.get("fail_closed", False)        # OCR 不可用时是否也拦截（保守）
        self.on_hit = cd.get("on_hit", "delete")               # delete（整图删）| blur（仅打码）
        self.keyword_file = cd.get("keyword_file", "data/sensitive_keywords.txt")
        self.sensitive_keywords = [str(k) for k in cd.get("sensitive_keywords", [])]
        self._load_keyword_file()

        # OCR 后端能力探测（一次）
        self.ocr_ready: bool = False
        self.ocr_status: str = "未启用"
        self.ocr_error_count: int = 0
        if self.ocr_enabled:
            self.ocr_ready, self.ocr_status = self._probe_ocr_backend()
            if self.ocr_ready:
                logger.info("Layer3 OCR 门禁就绪: %s", self.ocr_status)
            else:
                logger.warning("⚠ Layer3 OCR 门禁不可用: %s（fail_closed=%s）", self.ocr_status, self.fail_closed)

        # 编译正则模式
        self.patterns: dict[str, re.Pattern] = {}
        for name, pattern_str in cd.get("patterns", {}).items():
            try:
                self.patterns[name] = re.compile(pattern_str, re.IGNORECASE)
            except re.error as e:
                logger.warning("正则表达式 '%s' 编译失败: %s", name, e)

        # 统计
        self.skip_count: int = 0
        self.blur_count: int = 0
        self.ocr_match_count: int = 0

    # ── Layer 1: 窗口过滤 ─────────────────────────────

    def check_window(self, app_name: str, window_title: str) -> PrivacyResult:
        """检查窗口是否在黑名单中.

        Returns:
            PrivacyResult: 如果 should_skip=True 则应跳过截图
        """
        result = PrivacyResult()

        # 检查应用名黑名单（子串匹配）
        app_lower = app_name.lower().strip()
        for blocked in self.app_blacklist:
            if blocked in app_lower:
                result.should_skip = True
                result.skip_reason = f"应用黑名单: 匹配 '{blocked}' in '{app_lower}'"
                self.skip_count += 1
                logger.info("Layer1 隐私跳过 — %s", result.skip_reason)
                return result

        # 检查窗口标题黑名单（子串匹配）
        title_lower = window_title.lower().strip()
        for blocked in self.title_blacklist:
            if blocked in title_lower:
                result.should_skip = True
                result.skip_reason = f"标题黑名单: 匹配 '{blocked}' in '{title_lower}'"
                self.skip_count += 1
                logger.info("Layer1 隐私跳过 — %s", result.skip_reason)
                return result

        return result

    # ── Layer 2: 区域模糊 ─────────────────────────────

    def apply_region_blur(self, image: Image.Image) -> tuple[Image.Image, list[str]]:
        """对预定义区域应用高斯模糊.

        Args:
            image: PIL Image 对象

        Returns:
            (模糊后的图像, 模糊区域名称列表)
        """
        if not self.blur_regions:
            return image, []

        img_w, img_h = image.size
        blurred_names: list[str] = []

        for region in self.blur_regions:
            # 计算像素坐标
            x1 = int(region.x_start * img_w)
            x2 = int(region.x_end * img_w)
            y1 = int(region.y_start * img_h)
            y2 = int(region.y_end * img_h)

            # 边界检查
            x1 = max(0, min(x1, img_w))
            x2 = max(0, min(x2, img_w))
            y1 = max(0, min(y1, img_h))
            y2 = max(0, min(y2, img_h))

            if x2 <= x1 or y2 <= y1:
                continue

            # 裁剪区域 → 模糊 → 贴回
            region_img = image.crop((x1, y1, x2, y2))
            blurred = region_img.filter(ImageFilter.GaussianBlur(radius=20))
            image.paste(blurred, (x1, y1, x2, y2))

            blurred_names.append(region.name)
            logger.debug("Layer2 区域模糊: %s (%d,%d)-(%d,%d)", region.name, x1, y1, x2, y2)

        self.blur_count += len(blurred_names)
        return image, blurred_names

    # ── Layer 3: OCR 内容门禁 ─────────────────────────

    # CJK 字符范围（含全角标点/空格），用于归一化 Windows OCR 的字间空格
    _CJK = r'\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef'

    @classmethod
    def normalize_ocr_text(cls, text: str) -> str:
        """归一化 OCR 文本.

        Windows OCR 会把中文按字切分并插入空格（"训练题" → "训 练 题"），
        不归一化则任何中文关键词都匹配不到，门禁形同虚设。
        """
        t = (text or "").replace("\u00a0", " ")
        t = re.sub(rf'(?<=[{cls._CJK}])\s+(?=[{cls._CJK}])', "", t)
        t = re.sub(rf'(?<=[{cls._CJK}])\s+(?=[A-Za-z0-9])', "", t)
        t = re.sub(rf'(?<=[A-Za-z0-9])\s+(?=[{cls._CJK}])', "", t)
        return re.sub(r"[ \t]+", " ", t)

    def _load_keyword_file(self) -> None:
        """从本地文件补充敏感词（一行一词，# 注释），仓库不跟踪该文件."""
        path = Path(self.keyword_file)
        if not path.is_file():
            return
        extra = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    extra.append(line)
        except Exception as e:
            logger.warning("读取敏感词文件失败 (%s): %s", self.keyword_file, e)
            return
        before = len(self.sensitive_keywords)
        seen = {k.lower() for k in self.sensitive_keywords}
        for k in extra:
            if k.lower() not in seen:
                self.sensitive_keywords.append(k)
                seen.add(k.lower())
        logger.info("敏感词表: 配置 %d + 文件 %d → 合计 %d", before, len(extra), len(self.sensitive_keywords))

    def _probe_ocr_backend(self) -> tuple[bool, str]:
        """探测 OCR 后端可用性（初始化时一次）."""
        if self.ocr_engine == "tesseract":
            return self._probe_tesseract()
        return self._probe_windows_ocr()

    def _probe_windows_ocr(self) -> tuple[bool, str]:
        try:
            from winrt.windows.media.ocr import OcrEngine
            from winrt.windows.globalization import Language
        except Exception as e:
            return False, f"winrt 未安装（{type(e).__name__}）"
        try:
            if OcrEngine.try_create_from_language(Language(self.win_ocr_language)) is None:
                try:
                    tags = [l.language_tag for l in OcrEngine.available_recognizer_languages]
                except Exception:
                    tags = []
                return False, f"系统缺 OCR 语言包 {self.win_ocr_language}（可用: {tags or '无'}）"
        except Exception as e:
            return False, f"创建 OCR engine 失败: {e}"
        return True, f"windows_ocr / {self.win_ocr_language}"

    def _probe_tesseract(self) -> tuple[bool, str]:
        try:
            import pytesseract
        except ImportError:
            return False, "pytesseract 未安装"
        if self.tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = self.tesseract_path
        try:
            pytesseract.get_tesseract_version()
        except Exception as e:
            return False, f"tesseract 二进制不可用（{e}）"
        return True, f"tesseract / {self.tesseract_language}"

    def _to_software_bitmap(self, image: Image.Image):
        from PIL import Image as _Image
        from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat
        from winrt.windows.storage.streams import DataWriter

        img = image.convert("RGBA")
        w, h = img.size
        if max(w, h) > self.max_image_dim:
            ratio = self.max_image_dim / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), _Image.LANCZOS)
            w, h = img.size
        b, g, r, a = img.split()
        data = _Image.merge("RGBA", (b, g, r, a)).tobytes()  # 通道重排为 BGRA
        writer = DataWriter()
        writer.write_bytes(data)
        return SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(), BitmapPixelFormat.BGRA8, w, h
        )

    def _ocr_windows(self, image: Image.Image) -> str:
        import asyncio
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.globalization import Language

        engine = OcrEngine.try_create_from_language(Language(self.win_ocr_language))
        if engine is None:
            raise RuntimeError(f"OCR engine 不可用: {self.win_ocr_language}")
        bitmap = self._to_software_bitmap(image)

        # recognize_async 返回 IAsyncOperation（awaitable 但不是 coroutine），
        # 必须包成 coroutine 再交给 asyncio.run，否则 TypeError 被上层吞成空串。
        async def _recognize():
            return await engine.recognize_async(bitmap)

        return asyncio.run(_recognize()).text

    def _ocr_tesseract(self, image: Image.Image) -> str:
        import pytesseract

        if self.tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = self.tesseract_path
        return pytesseract.image_to_string(image.convert("RGB"), lang=self.tesseract_language)

    def ocr_text(self, image: Image.Image) -> str:
        """OCR 一次，返回归一化文本；失败返回空串."""
        try:
            raw = self._ocr_tesseract(image) if self.ocr_engine == "tesseract" else self._ocr_windows(image)
        except Exception as e:
            self.ocr_error_count += 1
            logger.warning("Layer3 OCR 失败 (%d 次): %s", self.ocr_error_count, e)
            if self.ocr_error_count == 3:
                logger.error("⚠ Layer3 OCR 连续失败，门禁可能已失效（status=%s）", self.ocr_status)
            return ""
        return self.normalize_ocr_text(raw)

    def detect_sensitive_content(self, image: Image.Image) -> list[dict]:
        """OCR 门禁判定：命中隐私 / 成人 / R18 等内容则返回匹配列表.

        Returns:
            [{"type": "keyword"|"pattern", "name": str, "match": str}, ...]
            空列表 = 未命中，截图可以送云端 LLM
        """
        if not self.ocr_ready:
            return []
        text = self.ocr_text(image)
        if not text:
            return []

        matches: list[dict] = []
        low = text.lower()
        for kw in self.sensitive_keywords:
            if not kw:
                continue
            if re.fullmatch(r"[\x00-\x7f]+", kw):
                # 纯 ASCII 关键词按词边界匹配，避免 "jav" 命中 "JavaScript"
                if re.search(rf"(?<![a-z0-9]){re.escape(kw.lower())}(?![a-z0-9])", low):
                    matches.append({"type": "keyword", "name": kw, "match": kw})
            elif kw.lower() in low:
                matches.append({"type": "keyword", "name": kw, "match": kw})

        for name, pattern in self.patterns.items():
            m = pattern.search(text)
            if m:
                matches.append({"type": "pattern", "name": name, "match": m.group()[:40]})
        return matches

    def apply_content_blur(
        self, image: Image.Image, matches: list[dict]
    ) -> Image.Image:
        """对 OCR 检测到的敏感区域进行模糊.

        Args:
            image: PIL Image 对象
            matches: detect_sensitive_content 返回的匹配列表

        Returns:
            模糊后的图像
        """
        if not matches:
            return image

        for m in matches:
            x, y, w, h = m["region"]

            # 扩大模糊区域（确保覆盖完整文本）
            padding = 10
            x1 = max(0, x - padding)
            y1 = max(0, y - padding)
            x2 = min(image.width, x + w + padding)
            y2 = min(image.height, y + h + padding)

            # 裁剪 → 强模糊 → 贴回
            region_img = image.crop((x1, y1, x2, y2))
            blurred = region_img.filter(
                ImageFilter.GaussianBlur(radius=self.blur_radius)
            )
            image.paste(blurred, (x1, y1, x2, y2))

            logger.debug("Layer3 内容模糊: %s at (%d,%d,%d,%d)", m["pattern"], x1, y1, x2, y2)

        return image

    # ── 完整过滤流程 ──────────────────────────────────

    def process(
        self,
        image_path: str,
        app_name: str = "",
        window_title: str = "",
    ) -> PrivacyResult:
        """执行完整隐私过滤.

        顺序：Layer 1 窗口黑名单 → Layer 3 OCR 门禁（在原图上判定，模糊会降低识别率）
        → Layer 2 区域模糊（送云端前脱敏）。

        Layer 3 命中时返回 should_skip=True（默认 on_hit="delete"，由调用方删除截图），
        确保命中隐私/成人/R18 的截图不会被送去云端 LLM。
        """
        # Layer 1: 窗口检查
        window_result = self.check_window(app_name, window_title)
        if window_result.should_skip:
            return window_result

        img = Image.open(image_path)

        # Layer 3: OCR 内容门禁
        if self.ocr_enabled:
            if not self.ocr_ready:
                if self.fail_closed:
                    self.skip_count += 1
                    reason = f"Layer3 OCR 不可用且 fail_closed（{self.ocr_status}）"
                    logger.warning("Layer3 %s — 本条截图不送云端", reason)
                    return PrivacyResult(should_skip=True, skip_reason=reason)
                logger.warning("Layer3 OCR 不可用，本次跳过内容门禁: %s", self.ocr_status)
            else:
                matches = self.detect_sensitive_content(img)
                if matches:
                    self.ocr_match_count += len(matches)
                    self.skip_count += 1
                    names = ", ".join(sorted({m["name"] for m in matches}))
                    logger.info("Layer3 OCR 命中 [%s] — 截图不发送云端 LLM", names)
                    return PrivacyResult(
                        should_skip=True,
                        skip_reason=f"Layer3 OCR 命中: {names}",
                        ocr_matches=matches,
                    )

        # Layer 2: 区域模糊（送云端前的固定脱敏）
        img, blurred = self.apply_region_blur(img)
        img.save(image_path, "PNG", optimize=True)

        return PrivacyResult(
            should_skip=False,
            filtered_image=img,
            blurred_regions=blurred,
            ocr_matches=[],
        )

    def get_stats(self) -> dict:
        """返回过滤统计信息."""
        return {
            "skip_count": self.skip_count,
            "blur_count": self.blur_count,
            "ocr_match_count": self.ocr_match_count,
            "ocr_ready": self.ocr_ready,
            "ocr_status": self.ocr_status,
        }
