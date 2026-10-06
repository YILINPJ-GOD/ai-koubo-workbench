"""抖音等视频链接 → 文案：下载视频 + 本地语音转文字（faster-whisper）。

设计要点：
- HuggingFace 直连不稳，默认走 hf-mirror 镜像下载模型（国内可用）
- faster-whisper 用 PyAV 解码，无需系统安装 ffmpeg
- 下载与转写都可能较慢，由任务系统调度并汇报进度
- 抖音下载限制严：yt-dlp 需要真实浏览器的匿名验证 cookie（ttwid/s_v_web_id），
  而新版 Chrome/Edge 的 cookie 加密取不出来 → 兜底方案是拉起本机 Chrome/Edge
  无头模式渲染视频页，从渲染结果里抠出播放直链直接下载（免登录免 cookie）
"""
import html
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from ..pipeline.http import UA as _UA

# 必须在导入 faster_whisper 前设置，否则模型走 HF 直连
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

MODEL_SIZE = "small"  # 中文口播清晰，small 准确率/体积平衡；首次下载约 500MB
_model = None

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_DOUYIN_MARKS = ("douyin.com", "iesdouyin.com")

# Windows 上常见的 Chromium 系浏览器，有其一即可（Edge 随系统自带，基本必有）
_CHROMIUM_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)


class TranscribeError(Exception):
    """链接解析/下载/转写失败，message 可直接展示。"""


def extract_url(raw: str) -> str:
    """从分享文本里提取第一个 URL（抖音分享文案是'文案链接'混排）。"""
    m = re.search(r"https?://[^\s，,。；;（）()【】\[\]）】\"']+", raw or "")
    return m.group(0).rstrip(".,。") if m else ""


def _is_douyin(url: str) -> bool:
    return any(mark in (url or "").lower() for mark in _DOUYIN_MARKS)


def _find_chromium() -> str:
    for p in _CHROMIUM_PATHS:
        if os.path.exists(p):
            return p
    return ""


def _resolve_douyin_id(url: str) -> str:
    """从视频页/短链里解析 aweme id。

    短链（v.douyin.com）的跳转行为不稳定：多数时候302到首页，偶尔才给视频页——
    多试几次抓"好窗口"。
    """
    m = re.search(r"/(?:video|note)/(\d{5,})", url or "")
    if m:
        return m.group(1)
    try:
        import httpx

        for _ in range(3):
            r = httpx.get(
                url.strip(), headers={"User-Agent": _UA}, follow_redirects=True, timeout=15
            )
            m = re.search(r"/(?:video|note)/(\d{5,})", str(r.url)) or re.search(
                r"/(?:video|note)/(\d{5,})", r.text[:200000]
            )
            if m:
                return m.group(1)
            if "douyin.com" in str(r.url) and str(r.url).rstrip("/").endswith("douyin.com"):
                time.sleep(1.2)  # 被跳到首页了：歇一下再试
                continue
            break
    except Exception:  # noqa: BLE001 —— 短链解析失败走统一报错
        pass
    return ""


def _render_douyin_dom(exe: str, page_url: str, profile: Path) -> str:
    """无头浏览器渲染抖音页面，返回 DOM 文本（失败返回空串）。"""
    cmd = [
        exe, "--headless", "--disable-gpu", "--no-first-run", "--disable-extensions",
        f"--user-data-dir={profile}", "--window-size=1300,1700",
        "--virtual-time-budget=20000", "--dump-dom", page_url,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="ignore",
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return proc.stdout or ""


def _douyin_download(url: str, out_dir: Path, video_id_hint: str = "") -> Path:
    """无头浏览器方案：渲染抖音视频页 → 抠播放直链 → 免 cookie 下载。

    video_id_hint 优先用 yt-dlp 报错里带出的ID（短链它解得动，只是没 cookie 下载不了）；
    否则自己从链接里解析；再不行就把原始链接交给浏览器自己跳。
    """
    import httpx

    exe = _find_chromium()
    if not exe:
        raise TranscribeError("本机没找到 Chrome/Edge，拉取不了抖音视频；可直接粘贴文案文字进行拆解")

    pages = []
    video_id = video_id_hint or _resolve_douyin_id(url)
    if video_id:
        pages.append(f"https://www.douyin.com/video/{video_id}")
    if url.strip() not in pages:
        pages.append(url.strip())

    play_url = ""
    for page_url in pages:
        profile = Path(tempfile.mkdtemp(prefix="wb-chrome-"))
        try:
            dom = html.unescape(_render_douyin_dom(exe, page_url, profile)).replace("\\u0026", "&")
        finally:
            shutil.rmtree(profile, ignore_errors=True)
        m = re.search(r"https://www\.douyin\.com/aweme/v1/play/\?[^\"'<\s]+", dom)
        if m:
            play_url = m.group(0)
            break
    if not play_url:
        raise TranscribeError(
            "这条链接解析不到视频：可能是图文帖/已删除的内容；"
            "v.douyin.com 短链偶尔会被抖音拦截，可等一两分钟重试，"
            "或在抖音里打开视频点「分享→复制链接」重新粘贴；也可以直接粘贴文案文字进行拆解"
        )

    out = out_dir / "source_video.mp4"
    try:
        written = 0
        with httpx.stream(
            "GET", play_url,
            headers={"User-Agent": _UA, "Referer": "https://www.douyin.com/"},
            follow_redirects=True, timeout=60,
        ) as r:
            r.raise_for_status()
            ctype = (r.headers.get("content-type") or "").lower()
            if "video" not in ctype and "octet-stream" not in ctype:
                raise TranscribeError("抖音视频地址被风控拦截，请重试一次；或直接粘贴文案文字进行拆解")
            with open(out, "wb") as f:
                for chunk in r.iter_bytes(256 * 1024):
                    written += len(chunk)
                    if written > 300 * 1024 * 1024:
                        raise TranscribeError("视频超过300MB，太大了；建议换一条或直接粘贴文案")
                    f.write(chunk)
    except httpx.HTTPError as e:
        raise TranscribeError(f"视频文件下载失败（{str(e)[:60]}），请重试一次；或直接粘贴文案") from e
    if written < 50 * 1024:
        raise TranscribeError("下载到的视频不完整，请重试一次；或直接粘贴文案文字进行拆解")
    return out


def download_video(url: str, out_dir: Path) -> Path:
    """yt-dlp 下载视频（支持 v.douyin.com 分享链），抖音失败自动走无头浏览器兜底。"""
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(out_dir / "source_video.%(ext)s")
    opts = {
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 2,
        "max_filesize": 300 * 1024 * 1024,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
    except Exception as e:  # noqa: BLE001 —— 各类下载异常统一转译
        msg = _ANSI_RE.sub("", str(e))  # yt-dlp 报错常带 ANSI 色码，界面上会变乱码
        if _is_douyin(url):
            # yt-dlp 解析阶段已经把短链换成了视频ID，报错里就带着——借来当无头浏览器的入口
            m = re.search(r"\[Douyin\]\s*(\d{5,})", msg)
            hint = m.group(1) if m else ""
            try:
                return _douyin_download(url, out_dir, hint)
            except TranscribeError:
                raise  # 兜底给出的提示已可直接展示
            except Exception as e2:  # noqa: BLE001
                raise TranscribeError(
                    f"抖音视频下载失败：{str(e2)[:60]}。也可以直接粘贴文案文字进行拆解"
                ) from e2
        if "unsupported" in msg.lower() or "no video" in msg.lower():
            raise TranscribeError("这个链接解析不到视频，请确认是抖音/快手/B站的视频分享链接") from e
        raise TranscribeError(f"视频下载失败：{msg[:80]}。也可以直接粘贴文案文字进行拆解") from e
    p = Path(filename)
    if not p.exists():
        found = sorted(out_dir.glob("source_video.*"))
        if not found:
            raise TranscribeError("视频下载失败：未生成本地文件")
        p = found[0]
    return p


def get_model():
    global _model
    if _model is None:
        try:
            from faster_whisper import WhisperModel

            _model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
        except Exception as e:  # noqa: BLE001
            raise TranscribeError(
                f"语音转写组件加载失败：{str(e)[:80]}。首次使用需联网下载模型（约500MB），请重试一次"
            ) from e
    return _model


def transcribe(audio_path: Path) -> str:
    """视频/音频文件 → 口播文案文本。"""
    model = get_model()
    try:
        segments, info = model.transcribe(
            str(audio_path),
            language="zh",
            beam_size=5,
            vad_filter=True,
            initial_prompt="以下是普通话口播视频的内容。",
        )
        parts = [seg.text.strip() for seg in segments]
    except Exception as e:  # noqa: BLE001
        raise TranscribeError(f"语音转写失败：{str(e)[:80]}") from e
    return "".join(parts).strip()


def extract_text_from_link(url: str, progress=None) -> str:
    """完整流程：下载视频 → 转写 → 清理临时文件 → 返回文案。"""
    text_url = extract_url(url)
    if not text_url:
        raise TranscribeError("没有识别到链接，请粘贴包含视频链接的分享文本")
    tmp_dir = Path(tempfile.mkdtemp(prefix="wb-video-"))
    try:
        if progress:
            progress("正在解析链接并下载视频…")
        video = download_video(text_url, tmp_dir)
        if progress:
            progress("正在语音转文字（首次使用需下载模型，请耐心等待）…")
        text = transcribe(video)
        if not text:
            raise TranscribeError("视频里没听出有效语音内容，请换一条或直接粘贴文案")
        return text
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
