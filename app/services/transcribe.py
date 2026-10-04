"""抖音等视频链接 → 文案：下载视频 + 本地语音转文字（faster-whisper）。

设计要点：
- HuggingFace 直连不稳，默认走 hf-mirror 镜像下载模型（国内可用）
- faster-whisper 用 PyAV 解码，无需系统安装 ffmpeg
- 下载与转写都可能较慢，由任务系统调度并汇报进度
"""
import os
import re
import shutil
import tempfile
from pathlib import Path

# 必须在导入 faster_whisper 前设置，否则模型走 HF 直连
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

MODEL_SIZE = "small"  # 中文口播清晰，small 准确率/体积平衡；首次下载约 500MB
_model = None


class TranscribeError(Exception):
    """链接解析/下载/转写失败，message 可直接展示。"""


def extract_url(raw: str) -> str:
    """从分享文本里提取第一个 URL（抖音分享文案是'文案链接'混排）。"""
    m = re.search(r"https?://[^\s，,。；;（）()【】\[\]）】\"']+", raw or "")
    return m.group(0).rstrip(".,。") if m else ""


def _ydl_progressHook():
    return None


def download_video(url: str, out_dir: Path) -> Path:
    """yt-dlp 下载视频（支持 v.douyin.com 分享链），返回本地文件路径。"""
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
        msg = str(e)
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
