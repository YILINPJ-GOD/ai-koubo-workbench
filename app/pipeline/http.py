"""抓取公共设施：统一 HTTP 客户端与错误类型。"""
import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124 Safari/537.36"
)
TIMEOUT = 12.0


class SourceError(Exception):
    """单源抓取失败，message 记入健康记录并就地展示。"""


def http() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": UA},
        timeout=TIMEOUT,
        follow_redirects=True,
    )


def get_text(client: httpx.Client, url: str, referer: str = "") -> str:
    try:
        r = client.get(url, headers={"Referer": referer} if referer else None)
        r.raise_for_status()
        return r.text
    except httpx.HTTPStatusError as e:
        raise SourceError(f"HTTP {e.response.status_code}") from e
    except Exception as e:  # noqa: BLE001
        raise SourceError(f"{type(e).__name__}: {str(e)[:60]}") from e


def get_json(client: httpx.Client, url: str, referer: str = "") -> dict:
    text = get_text(client, url, referer)
    import json

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise SourceError("返回的不是 JSON") from e
