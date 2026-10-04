"""智谱 LLM 统一封装：所有 AI 能力的地基。

- chat(): 纯文本，带重试
- chat_json(): 强制 JSON 输出，带修复解析与重试
- 错误统一抛 LLMError，中文消息可直接展示给用户
"""
import json
import re
import time


class LLMError(Exception):
    """AI 调用失败，message 为可直接展示的中文。"""


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def extract_json(text: str):
    """从 LLM 返回文本中提取 JSON（容忍代码围栏、前后缀说明、尾逗号）。"""
    if text is None:
        raise LLMError("AI 返回为空")
    candidates = []
    m = _FENCE_RE.search(text)
    if m:
        candidates.append(m.group(1))
    candidates.append(text)
    for cand in candidates:
        for trimmed in (cand.strip(), cand.strip().strip("`")):
            try:
                return json.loads(trimmed)
            except (json.JSONDecodeError, ValueError):
                pass
            start = min(
                (i for i in (trimmed.find("{"), trimmed.find("[")) if i >= 0),
                default=-1,
            )
            if start == -1:
                continue
            end = max(trimmed.rfind("}"), trimmed.rfind("]"))
            if end <= start:
                continue
            body = _TRAILING_COMMA_RE.sub(r"\1", trimmed[start : end + 1])
            try:
                return json.loads(body)
            except (json.JSONDecodeError, ValueError):
                continue
    raise LLMError("AI 返回的内容不是有效 JSON，已重试仍失败")


class LLMClient:
    def __init__(self, api_key: str, model: str = "glm-4-flash"):
        self.api_key = api_key
        self.model = model or "glm-4-flash"

    def _raw(self, system: str, user: str) -> str:
        from zhipuai import ZhipuAI  # 延迟导入，便于测试替换

        try:
            client = ZhipuAI(api_key=self.api_key, timeout=180.0)
        except TypeError:
            # 旧版 SDK 不支持 timeout 参数
            client = ZhipuAI(api_key=self.api_key)
        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.7,
            )
        except Exception as e:  # noqa: BLE001 —— SDK 异常类型不稳定，统一转译
            raise LLMError(_translate_error(e)) from e
        content = resp.choices[0].message.content if resp.choices else None
        return content or ""

    def chat(self, system: str, user: str, retries: int = 2) -> str:
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                out = self._raw(system, user)
                if out.strip():
                    return out
                last = LLMError("AI 返回为空")
            except LLMError as e:
                last = e
                if _is_auth_error(str(e)):
                    raise  # key 问题重试无意义，直接引导用户
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
        raise last or LLMError("AI 调用失败")

    def chat_json(self, system: str, user: str, retries: int = 2):
        prompt = user + "\n\n（只输出 JSON，不要输出任何其他文字或解释）"
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return extract_json(self.chat(system, prompt, retries=0))
            except LLMError as e:
                last = e
                if _is_auth_error(str(e)):
                    raise
                prompt = (
                    user
                    + f"\n\n（你上一次的输出无法解析为 JSON：{e}。"
                    "请只输出合法 JSON，不要有任何其他文字）"
                )
        raise last or LLMError("AI 调用失败")


def _is_auth_error(msg: str) -> bool:
    low = msg.lower()
    return any(k in low for k in ("401", "api key", "apikey", "鉴权", "令牌", "token"))


def _translate_error(e: Exception) -> str:
    msg = str(e)
    if _is_auth_error(msg):
        return "API key 无效或未授权，请到「设置」页检查智谱 API key"
    if "timeout" in msg.lower() or "timed out" in msg.lower():
        return "AI 请求超时，请检查网络后重试"
    if any(k in msg.lower() for k in ("429", "rate", "限流")):
        return "AI 调用触发限流，请稍后重试"
    if any(k in msg for k in ("欠费", "余额", "403", " arrears", "quota")):
        return "AI 账户余额不足或无权限，请到智谱开放平台检查"
    return f"AI 调用失败：{msg}"


_default: LLMClient | None = None
_default_sig: tuple[str, str] | None = None


def get_llm() -> LLMClient:
    """按当前配置返回客户端；未配置 key 时抛出可引导的 LLMError。"""
    global _default, _default_sig
    from .config import load_config

    cfg = load_config()
    key = (cfg.get("api_key") or "").strip()
    model = cfg.get("model") or "glm-4-flash"
    if not key:
        raise LLMError("尚未配置智谱 API key，请先到「设置」页完成配置")
    sig = (key, model)
    if _default is None or _default_sig != sig:
        _default = LLMClient(key, model)
        _default_sig = sig
    return _default
