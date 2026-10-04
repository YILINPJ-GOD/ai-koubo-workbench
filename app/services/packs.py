"""素材包生成：口播稿 + 分镜字幕 + 配图 + 发布文案 + 安全检查，一次成套。"""
import hashlib
import json
from datetime import datetime
from pathlib import Path

from ..config import load_config
from .. import llm as llm_mod
from ..db import connect, execute, query_one
from ..llm import LLMError
from ..services import cardgen
from ..services.hotspots import get_hotspot, hotspot_sources, mark_packed
from ..services.script_checks import (
    DEFAULT_CHECKLIST,
    check_lengths,
    lengths_feedback,
    scan_risks,
)
from ..services.styles import copied_fragment

# 字数重试仍不达标时，自动换更强的模型重写失败档位（glm-4-flash 常不守字数）
ESCALATE_MODEL = "glm-4-air"

SYSTEM_SCRIPTS = (
    "你是抖音AI资讯口播创作者的写稿搭档。人设：{tone}，像朋友聊天，口语化、有梗有情绪。"
    "你写的稿子是念给观众听的，不是给人看的文章。本次任务只写三档口播稿，别的不用管。"
    "\n\n【去AI腔·硬规则】"
    "\n1. 禁止这些词：首先/其次/再次/最后/总而言之/综上所述/值得一提的是/不仅如此/随着…的发展/在…的背景下"
    "\n2. 禁止排比堆砌和四字成语连用；每句不超过20字，一句话只说一件事"
    "\n3. 多用口语词：好家伙/直接/干脆/说白了/讲真/这波/压根；该断就断，别用长从句"
    "\n4. 用具体数字代替形容词：不写'大幅提升'，写'跑分从82干到91'"
    "\n5. 禁止绝对化用语：最/第一/秒杀/史诗级/炸裂/必赚（会被平台限流）"
    "\n6. 口播稿里绝对不能出现 #话题标签，那是发布文案的事"
    "\n\n【各档结构与字数（数着写字，交稿前自己数一遍）】"
    "\n15秒（80-110字）：一个钩子直接炸场 + 一个最核心的信息（带数字）+ 一句互动，只讲一个点"
    "\n30秒（130-200字）：钩子 + 2个信息点（先事实后影响，各带数字）+ 互动"
    "\n60秒（270-360字）：研报快报式，信息密度拉满：一句总钩子 → 3~4条要点快报"
    "（每条2~3句：先通报事实带数字，再一句锐评影响）→ 一句总评 → 互动引导"
    "不够字数就把原文里的细节展开讲：背景、对比、数字、对普通人的影响"
    "\n\n【内容来源】sources.fulltext 是来源文章原文，稿子的实质内容从这里面挖，"
    "摘要只是索引。"
    "\n\n【范例·照这个口语感和长度写（主题不同，别抄内容）】"
    "\n15s（约90字）：好家伙，某大厂半夜偷偷上线新模型，跑分直接干到91！上一代才82，这提升幅度有点离谱了。想白嫖的，评论区扣个1！"
    "\n30s（约160字）：昨晚科技圈炸了：某大厂发布新一代模型，没开发布会直接上线。先说能力，官方评测里写作和推理两项全涨，长任务总算不中途掉链子了。再说价格，API直接砍半，这摆明了要抢开发者。普通用户再等俩月，各种App背后估计都得换成它。想第一时间试的，关注我，下期出教程！"
    "\n60秒（约300字）：今天聊个大事：某大厂发布新模型。三个重点。第一，能力，官方跑分里代码、数学、推理全线上涨，以前跑到一半断的活儿现在一口气干完。第二，价格，API直接砍半，能力涨价格还降，摆明了要把开发者全圈进自家生态。第三，速度，响应快了不少，实时对话基本没延迟。这意味着啥？接下来两个月，你会看到一堆App悄悄变聪明。对咱们普通人，建议把常用工具换成新版试试，写代码做表格的提升是能直接摸到的。觉得有用点个关注，明天出实测对比！"
    "\n\n严格输出 JSON：{{\"scripts\":{{\"15s\":\"\",\"30s\":\"\",\"60s\":\"\"}}}}{extra}"
)

SYSTEM_ASSETS = (
    "你是抖音AI资讯口播创作者的后期搭档。基于给定的三档口播成稿，生成分镜字幕和发布文案。"
    "分镜字幕：每档 3~5 条，标注时间段（总时长对应该档口播稿），"
    "最值得上屏的数字/金句 gold=true，字幕文本要和口播稿内容对应。"
    "发布文案：3个可选标题（带悬念，≤25字）、话题标签数组（4~6个，#开头）、封面大字（≤10字）。"
    "文字卡片建议2-3张（title≤10字大字、point≤18字副说明）。"
    "自查清单 3~5 条。"
    '严格输出 JSON：{"captions":{"15s":[{"t":"0-3s","text":"","gold":false}],"30s":[],"60s":[]},'
    '"publish":{"titles":["","",""],"tags":["#AI"],"cover_text":""},'
    '"cards":[{"title":"","point":""}],"checklist":["..."]}'
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def build_pack_prompt(hotspot: dict, sources: list[dict], style: dict | None,
                      tone: str, extra: str) -> str:
    """来源按正文长度排序，前两篇给足原文（这是稿子的内容来源，不是摆设）。"""
    ranked = sorted(sources, key=lambda s: len(s.get("raw_text") or ""), reverse=True)
    material = []
    for s in ranked[:4]:
        raw = (s.get("raw_text") or "")[:1200]
        material.append(
            {
                "title": s["title"],
                "summary": s["summary"],
                "source_type": s["source_type"],
                "fulltext": raw,
            }
        )
    payload = {
        "event": {
            "title": hotspot["title"],
            "why": hotspot.get("why") or "",
            "angles": hotspot.get("angles", []),
            "suggested_length": hotspot.get("suggested_length", "30s"),
        },
        "sources": material,
        "写作指引": "fulltext 是来源文章原文，稿子的实质内容必须从这里面挖：最抓人的事实、"
        "数据、细节、比喻都可以改成你的口语表述（不要整段照抄）。摘要只是索引，原文才管饱。",
        "style_note": "如果下面给了风格模板，按它的结构骨架和节奏写，但语气仍用你自己的："
        + json.dumps(style["teardown"], ensure_ascii=False)
        if style else "",
        "sequel": (
            f"这是往期选题「{hotspot['sequel_title']}」的后续进展，开头自然带一句'上回说到…'式衔接"
            if hotspot.get("sequel_title") else ""
        ),
    }
    return json.dumps(payload, ensure_ascii=False)


def _parse_scripts(data: dict) -> dict:
    """从写稿响应中提取并清洗三档口播稿。"""
    raw = (data or {}).get("scripts", {}) or {}
    import re as _re

    out = {}
    for slot in ("15s", "30s", "60s"):
        text = str(raw.get(slot, "")).strip()
        text = _re.sub(r"#\S+", "", text).strip()  # 稿子里不该有话题标签
        out[slot] = text
    return out


def _parse_pack_scripts_only(data: dict, slots: list[str]) -> dict:
    """档位重试响应解析：只取要求的档位。"""
    scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
    scripts = scripts if isinstance(scripts, dict) else {}
    out = {}
    for slot in slots:
        v = scripts.get(slot)
        if v and str(v).strip():
            out[slot] = str(v).strip()
    return out


def _parse_pack(data: dict) -> dict:
    data = data if isinstance(data, dict) else {}
    scripts_raw = data.get("scripts")
    scripts_raw = scripts_raw if isinstance(scripts_raw, dict) else {}
    scripts = {k: str(scripts_raw.get(k, "")).strip() for k in ("15s", "30s", "60s")}
    captions_raw = data.get("captions", {})
    captions_raw = captions_raw if isinstance(captions_raw, dict) else {}
    captions = {}
    for slot in ("15s", "30s", "60s"):
        rows = captions_raw.get(slot, []) or []
        captions[slot] = [
            {"t": str(r.get("t", "")).strip(), "text": str(r.get("text", "")).strip(),
             "gold": bool(r.get("gold"))}
            for r in rows if str(r.get("text", "")).strip()
        ]
    pub = data.get("publish", {})
    pub = pub if isinstance(pub, dict) else {}
    publish = {
        "titles": [str(t).strip() for t in (pub.get("titles") or []) if str(t).strip()][:3],
        "tags": [str(t).strip() for t in (pub.get("tags") or []) if str(t).strip()][:6],
        "cover_text": str(pub.get("cover_text", "")).strip()[:20],
    }
    cards = [
        {"title": str(c.get("title", "")).strip()[:12], "point": str(c.get("point", "")).strip()[:24]}
        for c in (data.get("cards") or [])[:3]
        if str(c.get("title", "")).strip()
    ]
    checklist = [str(c).strip() for c in (data.get("checklist") or []) if str(c).strip()][:6]
    return {"scripts": scripts, "captions": captions, "publish": publish, "cards": cards,
            "checklist": checklist or DEFAULT_CHECKLIST}


def generate_pack(hotspot_id: int, style_id: int | None, llm) -> dict:
    """生成并落库（覆盖旧版），返回 {pack_id, warnings}。可抛 LLMError。"""
    hotspot = get_hotspot(hotspot_id)
    if not hotspot:
        raise ValueError("热点不存在")
    sources = hotspot_sources(hotspot_id)
    if hotspot.get("sequel_of"):
        past = query_one("SELECT title FROM hotspots WHERE id=?", (hotspot["sequel_of"],))
        hotspot["sequel_title"] = past["title"] if past else ""

    style = None
    style_note = ""
    style_source_text = ""
    if style_id:
        row = query_one("SELECT * FROM styles WHERE id=?", (style_id,))
        if row:
            style = {"id": row["id"], "name": row["name"], "teardown": json.loads(row["teardown"])}
            style_source_text = row["source_text"]
            style_note = f"套用了「{row['name']}」模板"

    cfg = load_config()
    pref = cfg.get("style_pref", {})
    extra = f"\n\n【用户补充要求（必须遵守）】{pref.get('extra', '')}" if pref.get("extra") else ""
    system_s = SYSTEM_SCRIPTS.format(tone=pref.get("tone", "轻松接地气"), extra=extra)
    user = build_pack_prompt(hotspot, sources, style, pref.get("tone", ""), pref.get("extra", ""))

    warnings = []
    # 第一步：只写三档口播稿（输出小，模型能专心把字数写够）
    data = llm.chat_json(system_s, user)
    pack = {"scripts": _parse_scripts(data), "captions": {}, "publish": {}, "cards": [],
            "checklist": list(DEFAULT_CHECKLIST)}

    # 字数硬校验：只重写不合格的档位（带来源指引），最多两轮
    checks = check_lengths(pack["scripts"])
    attempt = 0
    while not all(c["ok"] for c in checks.values()) and attempt < 2:
        attempt += 1
        failing = [slot for slot, c in checks.items() if not c["ok"]]
        feedback = lengths_feedback({s: checks[s] for s in failing})
        user_retry = (
            user
            + f"\n\n你上一版里 {'、'.join(failing)} 档字数不合格：{feedback}。"
            "请从 sources.fulltext 里挖具体事实和数字来充实内容（别只写一句反问），"
            "对照范例的长度写。"
            f'只重写不合格的档位。严格输出 JSON：{{"scripts":{{'
            + ",".join(f'"{s}":""' for s in failing)
            + "}}"
        )
        retry = _parse_pack_scripts_only(llm.chat_json(system_s, user_retry), failing)
        for slot in failing:
            if retry.get(slot):
                pack["scripts"][slot] = retry[slot]
        checks = check_lengths(pack["scripts"])

    # 模型升级：配置模型（如 flash）重试后仍不达标，失败档位换更强模型再试两轮
    if not all(c["ok"] for c in checks.values()):
        cfg_model = (load_config().get("model") or "").strip()
        api_key = getattr(llm, "api_key", "")
        if api_key and cfg_model != ESCALATE_MODEL:
            strong = llm_mod.LLMClient(api_key, ESCALATE_MODEL)
            for _ in range(3):
                if all(c["ok"] for c in checks.values()):
                    break
                failing = [slot for slot, c in checks.items() if not c["ok"]]
                feedback = lengths_feedback({s: checks[s] for s in failing})
                user_retry = (
                    user
                    + f"\n\n上一版 {'、'.join(failing)} 档字数不合格：{feedback}。"
                    "宁可多写细节也不能短：从 sources.fulltext 里把事件背景、数字对比、"
                    "对普通人的影响逐条展开，对照范例长度写。"
                    f'只重写不合格的档位。严格输出 JSON：{{"scripts":{{'
                    + ",".join(f'"{s}":""' for s in failing)
                    + "}}"
                )
                try:
                    retry = _parse_pack_scripts_only(strong.chat_json(system_s, user_retry), failing)
                except LLMError:
                    break  # 升级模型也不可用，保留现有稿子
                for slot in failing:
                    if retry.get(slot):
                        pack["scripts"][slot] = retry[slot]
                checks = check_lengths(pack["scripts"])

    # 口播稿里混入话题标签的，剥掉
    import re as _re

    for slot in ("15s", "30s", "60s"):
        cleaned = _re.sub(r"#\S+", "", pack["scripts"][slot]).strip()
        if cleaned != pack["scripts"][slot]:
            pack["scripts"][slot] = cleaned
    checks = check_lengths(pack["scripts"])
    if not all(c["ok"] for c in checks.values()):
        warnings.append("字数未完全达标：" + lengths_feedback(checks))

    # 风险词扫描 + 自动修复一轮（替换违禁词比标注更好）
    def _scan():
        found, seen = [], set()
        for slot in ("15s", "30s", "60s"):
            for r in scan_risks(pack["scripts"][slot]):
                if r["word"] not in seen:
                    seen.add(r["word"])
                    found.append({**r, "slot": slot})
        return found

    risks = _scan()
    if risks:
        risk_slots = sorted({r["slot"] for r in risks})
        word_list = "、".join(f"「{r['word']}」（{r['suggestion']}）" for r in risks)
        user_fix = (
            user
            + f"\n\n你上一版里有违禁词：{word_list}。只重写 {'、'.join(risk_slots)} 档，"
            "语义保持不变，换成合规说法。严格输出 JSON：{\"scripts\":{"
            + ",".join(f'"{s}":""' for s in risk_slots)
            + "}}"
        )
        try:
            fixed = _parse_pack_scripts_only(llm.chat_json(system_s, user_fix), risk_slots)
        except LLMError:
            fixed = {}
        for slot in risk_slots:
            if fixed.get(slot) and not scan_risks(fixed[slot]):
                pack["scripts"][slot] = fixed[slot]
        risks = _scan()
    if risks:
        warnings.append(f"口播稿里有 {len(risks)} 处风险词，已标注，建议替换后再录")

    # 第二步：基于成稿生成分镜字幕 + 发布文案 + 卡片建议
    try:
        system_a = SYSTEM_ASSETS
        user_a = json.dumps(
            {
                "event": {"title": hotspot["title"], "suggested_length": hotspot.get("suggested_length", "30s")},
                "final_scripts": pack["scripts"],
            },
            ensure_ascii=False,
        )
        assets = _parse_pack(llm.chat_json(system_a, user_a))
        pack["captions"] = assets["captions"]
        pack["publish"] = assets["publish"] or {
            "titles": [hotspot["title"][:25]], "tags": ["#AI"], "cover_text": hotspot["title"][:10],
        }
        pack["cards"] = assets["cards"]
        pack["checklist"] = assets["checklist"] or list(DEFAULT_CHECKLIST)
    except Exception as e:  # noqa: BLE001 —— 口播稿已保住，素材缺失可重生成
        warnings.append(f"字幕/发布文案生成失败（{str(e)[:40]}），口播稿已保留，可重新生成")
        pack["publish"] = {"titles": [hotspot["title"][:25]], "tags": ["#AI"], "cover_text": hotspot["title"][:10]}

    # 学结构不照搬：套模板时抽查与原文案的连续重合
    if style:
        for slot in ("15s", "30s", "60s"):
            frag = copied_fragment(pack["scripts"][slot], style_source_text)
            if frag:
                warnings.append(f"口播稿与模板原文有连续照搬片段（「{frag[:12]}…」），请重新生成或手动改写")
                break

    # 原文配图候选优先（官网/正文图，跨来源按 asset 去重，最多 6 张）
    candidates = []
    seen_assets = set()
    n_remote = 0
    for s in sorted(sources, key=lambda x: len(x.get("raw_text") or ""), reverse=True):
        urls = []
        try:
            urls = [u for u in json.loads(s.get("images") or "[]") if u]
        except (json.JSONDecodeError, TypeError):
            urls = []
        if s.get("image_url") and s["image_url"] not in urls:
            urls.insert(0, s["image_url"])  # og:image 通常是文章主图，排最前
        for url in urls:
            if n_remote >= 6:
                break
            key = _remote_asset_key(url)
            if key in seen_assets:
                continue
            seen_assets.add(key)
            n_remote += 1
            candidates.append({"type": "remote", "url": url, "label": f"官网配图{n_remote}"})
    # 文字卡片按需补位：原文图不足 2 张时才生成（来源图够用时不放，避免丑卡抢位）
    if n_remote < 2:
        cards_dir = card_dir()
        for i, c in enumerate(pack["cards"][:3]):
            out = cards_dir / f"hotspot{hotspot_id}_card{i + 1}.png"
            cardgen.make_card(c["title"], c["point"], "素材由 AI口播工作台 生成", out)
            candidates.append({"type": "card", "path": str(out), "label": f"文字卡片{i + 1}：{c['title']}"})

    selected = []
    remote_idx = [i for i, c in enumerate(candidates) if c["type"] == "remote"]
    if remote_idx:
        selected = remote_idx[:2]  # 默认选前两张官网图
    elif candidates:
        selected.append(0)

    pack_id = _upsert_pack(hotspot_id, pack, candidates, selected, risks, style_id,
                           style_note, all(c["ok"] for c in checks.values()))
    if style_id and style:
        execute("UPDATE styles SET used_count=used_count+1 WHERE id=?", (style_id,))
    mark_packed(hotspot_id)
    return {"pack_id": pack_id, "warnings": warnings}


def _remote_asset_key(url: str) -> str:
    """跨来源同图识别：去查询串/尺寸后缀/CDN缩略后缀，小写。"""
    import re
    from urllib.parse import urlsplit

    parts = urlsplit(url or "")
    path = parts.path.lower()
    path = re.sub(r"![a-z0-9.]+$", "", path)
    m = re.search(r"(-?\d{2,4}x\d{2,4})(\.[a-z]{3,4})$", path)
    if m:
        path = path[: m.start()] + m.group(2)
    return parts.netloc + path


def card_dir() -> Path:
    from ..config import images_dir

    d = images_dir() / "cards"
    d.mkdir(parents=True, exist_ok=True)
    return d


def remote_cache_path(url: str) -> Path:
    from ..config import images_dir

    h = hashlib.md5(url.encode()).hexdigest()[:16]
    d = images_dir() / "remote"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{h}.png"


def _upsert_pack(hotspot_id: int, pack: dict, candidates: list, selected: list,
                 risks: list, style_id, style_note: str, wc_ok: bool) -> int:
    conn = connect()
    try:
        row = conn.execute("SELECT id FROM packs WHERE hotspot_id=?", (hotspot_id,)).fetchone()
        values = (
            json.dumps(pack["scripts"], ensure_ascii=False),
            json.dumps(pack["captions"], ensure_ascii=False),
            json.dumps(candidates, ensure_ascii=False),
            json.dumps(selected, ensure_ascii=False),
            json.dumps(pack["publish"], ensure_ascii=False),
            json.dumps(risks, ensure_ascii=False),
            json.dumps(pack["checklist"], ensure_ascii=False),
            style_id,
            style_note,
            1 if wc_ok else 0,
        )
        if row:
            conn.execute(
                """UPDATE packs SET scripts=?, captions=?, image_candidates=?, image_selected=?,
                   publish=?, risks=?, checklist=?, style_id=?, style_note=?, wordcount_ok=?, created_at=?
                   WHERE hotspot_id=?""",
                values + (_now(), hotspot_id),
            )
            conn.commit()
            return row["id"]
        cur = conn.execute(
            """INSERT INTO packs(hotspot_id, scripts, captions, image_candidates, image_selected,
               publish, risks, checklist, style_id, style_note, wordcount_ok, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (hotspot_id, *values, _now()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def set_selected_images(pack_id: int, selected: list) -> None:
    execute("UPDATE packs SET image_selected=? WHERE id=?", (json.dumps(selected), pack_id))


def generation_unavailable_error(e: Exception) -> str:
    if isinstance(e, LLMError):
        return str(e)
    return f"生成失败：{e}"
