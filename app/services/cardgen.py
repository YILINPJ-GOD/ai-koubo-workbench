"""文字要点卡片生成：Pillow 本地绘制，纸白快报风（克制排版，避免廉价渐变感）。"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = (1080, 1440)
PAPER = (247, 245, 240)     # 暖白纸底
INK = (24, 24, 27)          # 近黑标题
GRAY = (120, 113, 108)      # 石灰灰副文
ACCENT = (217, 45, 32)      # 快报红
LINE = (214, 211, 205)      # 细线

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyhbd.ttc",   # 微软雅黑粗体
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines, buf = [], ""
    for ch in text:
        if draw.textlength(buf + ch, font=font) > max_width and buf:
            lines.append(buf)
            buf = ch
        else:
            buf += ch
    if buf:
        lines.append(buf)
    return lines


def make_card(title: str, point: str, footer: str, out_path: Path) -> Path:
    """生成一张竖版数据快报卡：纸白底、黑字大标题、红色要点块。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    w, h = SIZE
    img = Image.new("RGB", SIZE, PAPER)
    draw = ImageDraw.Draw(img)
    pad = 96

    # 顶部：黑色小方块标 + 右侧编号线
    tag_font = _font(34)
    tag = "AI 快报"
    tag_w = draw.textlength(tag, font=tag_font)
    draw.rectangle([pad, 88, pad + tag_w + 52, 156], fill=INK)
    draw.text((pad + 26, 100), tag, font=tag_font, fill=PAPER)
    draw.line([(w - pad - 320, 122), (w - pad, 122)], fill=LINE, width=2)

    # 标题：超大黑字，两行内
    title = (title or "").strip() or "重点"
    size = 116
    font = _font(size)
    while size > 48:
        font = _font(size)
        if len(_wrap(draw, title, font, w - pad * 2)) <= 2:
            break
        size -= 8
    y = 340
    for line in _wrap(draw, title, font, w - pad * 2)[:2]:
        draw.text((pad, y), line, font=font, fill=INK)
        y += size + 26

    # 标题下红色短块（视觉锚点）
    y += 28
    draw.rectangle([pad, y, pad + 132, y + 14], fill=ACCENT)
    y += 90

    # 要点：左侧细竖线 + 深灰中字
    if point:
        font_p = _font(48)
        plines = _wrap(draw, point.strip(), font_p, w - pad * 2 - 36)[:5]
        bar_h = len(plines) * 76
        draw.rectangle([pad, y + 8, pad + 8, y + bar_h], fill=LINE)
        for line in plines:
            draw.text((pad + 40, y), line, font=font_p, fill=(63, 61, 58))
            y += 76

    # 底部：细线 + 脚注
    draw.line([(pad, h - 150), (w - pad, h - 150)], fill=LINE, width=2)
    if footer:
        font_f = _font(30)
        draw.text((pad, h - 118), footer, font=font_f, fill=GRAY)

    img.save(out_path, "PNG")
    return out_path
