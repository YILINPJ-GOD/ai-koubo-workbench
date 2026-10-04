"""口播稿硬校验：字数档位与风险词扫描（纯逻辑）。"""
import re

# 档位字数区间（2026-10-03 用户反馈：15s 偏短，提额到 80-110，抖音快语速）
LENGTH_RANGES = {"15s": (80, 110), "30s": (130, 200), "60s": (270, 360)}

# 口语标点：计数时排除
_COUNT_SKIP = re.compile("[\\s，。！？、：；,.!?;:\"“”‘’（）()《》<>\\[\\]【】…—·~～\\-]")

# 风险词（广告法绝对化用语 + 口播常见限流表述），按出现位置标注
RISK_PATTERNS = [
    (r"最好|最佳|最强|最大|最高|最低|最快|最优|最全|最牛", "改为具体数据或客观描述"),
    (r"史上最[^，。、！？]{0,8}", "删除「史上最」表述"),
    (r"全网第一|业界第一|行业第一|全球第一", "改为可证实的「率先/首批」"),
    (r"(?<!首创之)首款", "确认官方出处，否则改为「首批之一」"),
    (r"顶级|极致|极品|终极", "改为客观描述"),
    (r"绝对|百分之百|100%有效", "删除绝对化承诺"),
    (r"秒杀|吊打|碾压(?!级)", "改为「明显优于」并给出依据"),
    (r"必赚|稳赚|保本|稳赢", "删除收益承诺类表述"),
    (r"根治|包治|治愈率|神药|包好", "删除医疗功效表述"),
    (r"国家级|世界领先|全球领先", "确认有权威出处，否则删除"),
]


def count_script_chars(text: str) -> int:
    return len(_COUNT_SKIP.sub("", text or ""))


def check_lengths(scripts: dict) -> dict:
    """scripts: {"15s": "..."} → {"15s": {"ok":, "count":, "range":(60,90)}}"""
    out = {}
    for slot, (lo, hi) in LENGTH_RANGES.items():
        count = count_script_chars(scripts.get(slot, ""))
        out[slot] = {"ok": lo <= count <= hi, "count": count, "range": (lo, hi)}
    return out


def lengths_feedback(checks: dict) -> str:
    """给 LLM 的重试反馈。"""
    rows = []
    for slot, c in checks.items():
        lo, hi = c["range"]
        mark = "OK" if c["ok"] else f"不合格(当前{c['count']}字，要求{lo}-{hi}字)"
        rows.append(f"{slot}: {mark}")
    return "；".join(rows)


def scan_risks(text: str) -> list[dict]:
    """返回 [{word, suggestion, index}]，同一词只报首次。"""
    found, seen = [], set()
    for pattern, suggestion in RISK_PATTERNS:
        for m in re.finditer(pattern, text or ""):
            word = m.group(0)
            if word in seen:
                continue
            seen.add(word)
            found.append({"word": word, "suggestion": suggestion, "index": m.start()})
    return sorted(found, key=lambda r: r["index"])


DEFAULT_CHECKLIST = [
    "口播稿字数与时长档位匹配",
    "开头3秒有钩子（悬念/反问/数字）",
    "无绝对化用语与收益承诺",
    "关键数据在配文/字幕中已标注来源",
    "结尾有互动引导（关注/评论）",
]
