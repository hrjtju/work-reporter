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
    Layer 3: OCR 内容检测 — 中成本，正则匹配敏感模式后模糊
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

        # Layer 3
        cd = privacy_config.get("content_detection", {})
        self.ocr_enabled = cd.get("enabled", False)
        self.tesseract_path = cd.get("tesseract_path", "")
        self.ocr_language = cd.get("language", "chi_sim+eng")
        self.blur_radius = cd.get("blur_radius", 25)

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

    # ── Layer 3: OCR 内容检测 ─────────────────────────

    def detect_sensitive_content(self, image: Image.Image) -> list[dict]:
        """OCR 识别屏幕文字，检测敏感内容.

        Args:
            image: PIL Image 对象

        Returns:
            检测到的敏感内容列表 [{"pattern": str, "match": str, "region": (x,y,w,h)}, ...]
        """
        if not self.ocr_enabled or not self.patterns:
            return []

        matches: list[dict] = []

        # 尝试加载 Tesseract
        try:
            import pytesseract
            if self.tesseract_path:
                pytesseract.pytesseract.tesseract_cmd = self.tesseract_path

            # OCR 识别（同时获取位置信息）
            ocr_data = pytesseract.image_to_data(
                image,
                lang=self.ocr_language,
                output_type=pytesseract.Output.DICT,
            )
        except ImportError:
            logger.warning("pytesseract 未安装，跳过 Layer3 OCR 检测")
            return []
        except Exception as e:
            logger.warning("Tesseract OCR 不可用 (%s)，跳过 Layer3", e)
            return []

        # 遍历每个识别到的文本块
        n_boxes = len(ocr_data["text"])
        for i in range(n_boxes):
            text = ocr_data["text"][i].strip()
            if not text:
                continue

            for pattern_name, pattern in self.patterns.items():
                match = pattern.search(text)
                if match:
                    x = ocr_data["left"][i]
                    y = ocr_data["top"][i]
                    w = ocr_data["width"][i]
                    h = ocr_data["height"][i]

                    matches.append({
                        "pattern": pattern_name,
                        "match": match.group(),
                        "region": (x, y, w, h),
                        "confidence": ocr_data["conf"][i],
                    })
                    logger.info(
                        "Layer3 检测到敏感内容: %s = '%s' (置信度: %d)",
                        pattern_name, match.group(), ocr_data["conf"][i],
                    )

        self.ocr_match_count += len(matches)
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
        """执行完整的三层隐私过滤.

        这是推荐的外部调用接口，依次执行 Layer 1 → Layer 2 → Layer 3.

        Args:
            image_path: 截图文件路径
            app_name: 活跃应用名称
            window_title: 活跃窗口标题

        Returns:
            PrivacyResult 包含过滤决策和修改后的图片
        """
        # Layer 1: 窗口检查
        window_result = self.check_window(app_name, window_title)
        if window_result.should_skip:
            return window_result

        # 加载图片
        img = Image.open(image_path)

        # Layer 2: 区域模糊
        img, blurred = self.apply_region_blur(img)

        # Layer 3: OCR 内容检测
        ocr_matches = self.detect_sensitive_content(img)
        if ocr_matches:
            img = self.apply_content_blur(img, ocr_matches)

        # 保存修改后的图片（覆盖原图）
        img.save(image_path, "PNG", optimize=True)

        return PrivacyResult(
            should_skip=False,
            filtered_image=img,
            blurred_regions=blurred,
            ocr_matches=ocr_matches,
        )

    def get_stats(self) -> dict:
        """返回过滤统计信息."""
        return {
            "skip_count": self.skip_count,
            "blur_count": self.blur_count,
            "ocr_match_count": self.ocr_match_count,
        }
