"""正文抽取与原文配图提取。

抓取链路：httpx 直连 → 失败（403/拦截/超时）自动降级 curl_cffi 浏览器伪装。
（openai.com 等官网对脚本 UA 返回 403，伪装 Chrome 后可通。）
"""
import os
import re
import shutil
import tempfile
from urllib.parse import urljoin

from .http import http

# curl_cffi 为可选依赖：中文安装路径会导致其证书加载失败，故复制到无中文的临时路径
_curl_ca: str | None = None


def _get_curl_cffi():
    global _curl_ca
    try:
        from curl_cffi import requests as creq
        import certifi

        if _curl_ca is None or not os.path.exists(_curl_ca):
            _curl_ca = os.path.join(tempfile.gettempdir(), "wb-cacert.pem")
            shutil.copyfile(certifi.where(), _curl_ca)
        return creq
    except Exception:  # noqa: BLE001 —— 未安装/初始化失败则无此兜底
        return None


def _fetch_html(url: str) -> str:
    """直连优先，被拦则浏览器伪装兜底。都失败抛异常。"""
    try:
        with http() as client:
            r = client.get(url)
            r.raise_for_status()
            return r.text
    except Exception as direct_err:  # noqa: BLE001
        creq = _get_curl_cffi()
        if creq is None:
            raise direct_err
        r = creq.get(url, impersonate="chrome124", timeout=25, verify=_curl_ca)
        if r.status_code != 200:
            raise direct_err
        return r.text


def extract_fulltext(url: str) -> tuple[str, list[str]]:
    """抓取文章页，返回 (正文文本, 图片URL列表)。失败返回 ("", [])。"""
    try:
        html = _fetch_html(url)
    except Exception:  # noqa: BLE001 —— 单篇失败静默降级为仅标题摘要
        return "", []
    return parse_article(html, url)


def parse_article(html: str, base_url: str) -> tuple[str, list[str]]:
    """正文图优先：trafilatura 正文模式自动剔除页面尾部相关文章/推荐噪音。

    正文模式提不到图时才回退 bs4 全页扫描。全部结果按 asset 去重
    （忽略查询参数与尺寸后缀，同图不同尺寸只留一张）。
    """
    import trafilatura
    from bs4 import BeautifulSoup

    text = trafilatura.extract(html, include_comments=False) or ""
    images: list[str] = []

    # 主路径：正文模式（<graphic src=...>），天然排除推荐位噪音图
    try:
        xml = trafilatura.extract(
            html, output_format="xml", include_images=True, include_comments=False
        )
        if xml:
            for src in re.findall(r'<graphic[^>]*src="([^"]+)"', xml)[:6]:
                full = urljoin(base_url, src)
                if _good_image(full):
                    images.append(full)
    except Exception:  # noqa: BLE001
        pass

    # 兜底：正文模式没图时全页扫描（og:image + img/source）
    if not images:
        try:
            soup = BeautifulSoup(html, "html.parser")
            og = soup.find("meta", property="og:image")
            if og and og.get("content"):
                images.append(urljoin(base_url, og["content"]))
            for tag in soup.find_all(["img", "source"]):
                src = _best_src(tag)
                if not src:
                    continue
                full = urljoin(base_url, src)
                if _good_image(full) and full not in images:
                    images.append(full)
        except Exception:  # noqa: BLE001
            pass
    else:
        # 正文模式也可能漏 og 主图，检查有没有；有则插最前
        try:
            soup = BeautifulSoup(html, "html.parser")
            og = soup.find("meta", property="og:image")
            if og and og.get("content"):
                og_url = urljoin(base_url, og["content"])
                if _asset_key(og_url) not in {_asset_key(u) for u in images}:
                    images.insert(0, og_url)
        except Exception:  # noqa: BLE001
            pass

    deduped = _dedupe_images(images)
    return text.strip(), deduped[:8]


_SIZE_SUFFIX_RE = re.compile(r"(-?\d{2,4}x\d{2,4})(\.[a-z]{3,4})$", re.I)
_VENDOR_SUFFIX_RE = re.compile(r"![a-z0-9.]+$", re.I)  # 爱范儿等站的 !720 缩略后缀


def _asset_key(url: str) -> str:
    """同图识别键：路径去查询串、去 CDN 尺寸后缀/缩略后缀，小写。"""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    path = parts.path.lower()
    path = _VENDOR_SUFFIX_RE.sub("", path)
    m = _SIZE_SUFFIX_RE.search(path)
    if m:
        path = path[: m.start()] + m.group(2)
    return parts.netloc + path


def _dedupe_images(images: list[str]) -> list[str]:
    """同 asset 去重：保留 w= 最大（或最长 URL，近似分辨率最高）的版本。"""
    best: dict[str, str] = {}
    order: list[str] = []
    for u in images:
        key = _asset_key(u)
        if key not in best:
            best[key] = u
            order.append(key)
            continue
        old = best[key]
        if _image_width(u) > _image_width(old):
            best[key] = u
    return [best[k] for k in order]


def _image_width(url: str) -> int:
    m = re.search(r"[?&]w=(\d+)", url)
    return int(m.group(1)) if m else 0


_SRCSET_RE = re.compile(r"(\S+)(?:\s+([\d.]+)w)?")


def _best_src(tag) -> str:
    """从 img/source 标签取最优 src：srcset 里最大宽度 > data-src* > src。"""
    srcset = tag.get("srcset") or tag.get("data-srcset") or ""
    if srcset:
        best, best_w = "", -1.0
        for m in _SRCSET_RE.finditer(srcset):
            url, w = m.group(1), m.group(2)
            width = float(w) if w else 0.0
            if width > best_w and url and not url.startswith("data:"):
                best, best_w = url, width
        if best:
            return best
    return (
        tag.get("src")
        or tag.get("data-src")
        or tag.get("data-original")
        or ""
    )


_BAD_IMG_RE = re.compile(
    r"\.(svg|gif)(\?|$)|logo|icon|avatar|sprite|data:|qrcode|qr-code|weixin|weibo-qrcode|推广|(?:^|[/=])ad-",
    re.I,
)


def _good_image(src: str) -> bool:
    if src.startswith("data:"):
        return False
    return not _BAD_IMG_RE.search(src)
