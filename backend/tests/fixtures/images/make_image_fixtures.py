"""生成图片虚构样本和 manifest.yaml（M3-01）。

用法（在测试镜像中，backend 目录挂载为 /srv/backend）：
    python tests/fixtures/images/make_image_fixtures.py
依赖 Pillow、PyYAML 和中文字体 fonts-wqy-microhei（只在测试镜像中安装）。噪点用固定种子，
重复生成得到逐字节相同的文件。内容全部虚构。

生成的样本：
  quotation_clear.png  清晰的报价函（OCR 置信度高）
  quotation_lowq.jpg   低质量的报价函：分辨率低、模糊、有噪点、JPEG 高压缩，并带 EXIF 方向标记 6
                       （存储的像素是逆时针转了 90°，按 EXIF 校正后才是正的）

manifest.yaml 写明：图片尺寸（存储的与 EXIF 校正后的）、OCR 关键词（已去掉空格，比较前先去掉识别结果中的空白）、
置信度高低、必须出现的警告和必须被替换的敏感项。OCR 结果不要求逐字一致。
"""
import random
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT_DIR = Path(__file__).resolve().parent
FONT_PATH = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"

LINES = [
    ("报价函", 52),
    ("致：Fictional Metals Trading FZE", 36),
    ("编号：QT-2026-0508", 36),
    ("我司（星河虚构进出口有限公司）报价如下：", 36),
    ("针状焦，数量 100 吨，", 36),
    ("单价 USD 2,100.00，", 36),
    ("总金额 USD 210,000.00。", 36),
    ("报价有效期至 2026年4月30日。", 36),
    ("联系人：周示例  电话：13800138000", 36),
]

MUST_REPLACE = [
    {"text": "Fictional Metals Trading FZE", "category": "ORG"},
    {"text": "星河虚构进出口有限公司", "category": "ORG"},
    {"text": "QT-2026-0508", "category": "REFNO"},
    {"text": "USD 2,100.00", "category": "PRICE"},
    {"text": "USD 210,000.00", "category": "AMOUNT"},
    {"text": "周示例", "category": "PERSON"},
    {"text": "13800138000", "category": "PHONE"},
]
KEYWORDS = ["报价函", "QT-2026-0508", "USD2,100.00", "USD210,000.00", "13800138000"]


def draw_page(width: int, height: int, scale: float) -> Image.Image:
    img = Image.new("L", (width, height), 255)
    d = ImageDraw.Draw(img)
    y = int(110 * scale)
    for text, size in LINES:
        px = max(8, int(size * scale))
        d.text((int(90 * scale), y), text, fill=20, font=ImageFont.truetype(FONT_PATH, px, index=0))
        y += int(px * 1.9) + (int(24 * scale) if size > 40 else 0)
    return img


def make_clear() -> dict:
    img = draw_page(1000, 1400, 1.0).convert("RGB")
    img.save(OUT_DIR / "quotation_clear.png", "PNG", optimize=False)
    return {
        "file": "quotation_clear.png",
        "description": "清晰的 PNG 报价函：白底黑字，字号 36 px，无噪点。OCR 置信度高，不应出现低置信警告。",
        "format": "PNG",
        "stored_size": [1000, 1400],
        "display_size": [1000, 1400],
        "exif_orientation": None,
        "page_count": 1,
        "ocr_pages": 1,
        "ocr_conf": "high",
        "ocr_keywords": KEYWORDS,
        "warnings": [],
        "must_replace": MUST_REPLACE,
        "must_keep": ["2026年4月30日", "100 吨"],
    }


def make_lowq() -> dict:
    scale = 0.5
    upright = draw_page(int(1000 * scale), int(1400 * scale), scale)
    upright = upright.filter(ImageFilter.GaussianBlur(1.3))
    rng = random.Random(20260102)
    px = upright.load()
    for y in range(upright.height):
        for x in range(upright.width):
            px[x, y] = max(0, min(255, px[x, y] + rng.randint(-55, 55)))
    upright = upright.point(lambda v: 50 + int(v * 0.6)).convert("RGB")
    # EXIF 方向 6：显示时需顺时针转 90°。所以存储的像素 = 正的图片逆时针转 90°。
    stored = upright.rotate(90, expand=True)
    exif = Image.Exif()
    exif[0x0112] = 6
    stored.save(OUT_DIR / "quotation_lowq.jpg", "JPEG", quality=30, exif=exif.tobytes())
    return {
        "file": "quotation_lowq.jpg",
        "description": "低质量的 JPG 报价函：分辨率低（约 0.5 倍）、模糊、噪点、对比度低、JPEG 质量 30，带 EXIF 方向标记 6。必须先按 EXIF 校正方向再 OCR；预期出现低置信警告。",
        "format": "JPEG",
        "stored_size": list(stored.size),
        "display_size": list(upright.size),
        "exif_orientation": 6,
        "page_count": 1,
        "ocr_pages": 1,
        "ocr_conf": "low",
        "ocr_keywords": [],
        "warnings": ["图片 OCR 识别置信度低，请核对原件"],
        "must_replace": MUST_REPLACE,
        "must_keep": [],
    }


def main():
    samples = {"quotation_clear": make_clear(), "quotation_lowq": make_lowq()}
    manifest = {
        "generator": "make_image_fixtures.py",
        "note": "全部内容虚构。bbox 为 EXIF 校正后的原图像素。OCR 页只断言 ocr_keywords（已去掉空格）、置信度高低和警告。",
        "samples": samples,
    }
    (OUT_DIR / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8"
    )
    print("已生成", ", ".join(s["file"] for s in samples.values()))


if __name__ == "__main__":
    main()
