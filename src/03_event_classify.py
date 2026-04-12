from __future__ import annotations

import csv
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple, Optional

ROOT = Path(".")
PROCESSED_DIR = ROOT / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

EVENTS_CLEAN_PATH = PROCESSED_DIR / "events_clean.csv"
EVENTS_STRUCTURED_PATH = PROCESSED_DIR / "events_structured.csv"

# 可选：如果存在人工/模型预评估结果，将优先复用（按 event_id 对齐）
EVENTS_EVALUATED_JSON_PATH = ROOT / "events_evaluated.json"
EVENTS_EVALUATED_CSV_PATH = ROOT / "events_evaluated.csv"

CN_TZ = timezone(timedelta(hours=8))

INPUT_HEADERS = [
    "event_id",
    "title",
    "content",
    "source",
    "publish_time",
    "url",
    "crawl_time",
    "version",
]

OUTPUT_HEADERS = [
    "event_id",
    "title",
    "content",
    "source",
    "publish_time",
    "url",
    "crawl_time",
    "version",
    "duration_type",
    "duration_score",
    "subject_type",
    "subject_score",
    "predictability_type",
    "predictability_score",
    "industry_tag_lv1",
    "industry_tag_lv2",
    "industry_score",
    "sentiment_polarity",
    "sentiment_confidence",
    "sentiment_score",
    "impact_scope_type",
    "impact_scope_score",
    "event_score",
    "event_score_breakdown",
]

SUBJECT_KEYWORDS: Dict[str, List[str]] = {
    "政策类": ["国务院", "发改委", "证监会", "央行", "办法", "意见", "通知", "征求意见", "会议", "发布会", "政策"],
    "公司类": ["公告", "年报", "季报", "收购", "回购", "减持", "董事会", "净利润", "亏损", "停牌", "复牌"],
    "行业类": ["行业", "产业", "论坛", "峰会", "景气", "产能", "供需", "渗透率", "龙头"],
    "地缘类": ["中东", "伊朗", "以色列", "战争", "冲突", "制裁", "停火", "外交", "霍尔木兹"],
    "宏观类": ["CPI", "PPI", "GDP", "社融", "PMI", "宏观", "经济数据", "利率", "通胀"],
}

SUBJECT_SCORE_MAP = {
    "政策类": 90,
    "公司类": 85,
    "行业类": 75,
    "地缘类": 65,
    "宏观类": 70,
}

DURATION_KEYWORDS: Dict[str, List[str]] = {
    "长尾型": ["五年", "长期", "规划", "战略", "制度", "改革", "机制"],
    "中期型": ["季度", "中期", "全年", "产能", "扩产", "投资"],
    "短期型": ["下周", "本周", "月度", "即将", "召开"],
    "脉冲型": ["快讯", "突发", "辟谣", "传闻", "回应"],
}

DURATION_SCORE_MAP = {
    "脉冲型": 40,
    "短期型": 60,
    "中期型": 80,
    "长尾型": 100,
}

PREDICTABILITY_KEYWORDS: Dict[str, List[str]] = {
    "预披露型": ["将", "即将", "召开", "预告", "按机制", "发布会", "下周", "日程"],
    "半预期型": ["会议", "论坛", "计划", "推进", "座谈"],
    "突发型": ["突发", "紧急", "爆发", "事故", "被立案", "未能达成", "警告"],
}

PREDICTABILITY_SCORE_MAP = {
    "突发型": 40,
    "半预期型": 65,
    "预披露型": 85,
}

INDUSTRY_KEYWORDS: Dict[str, List[str]] = {
    "新能源": ["新能源", "锂电", "光伏", "储能", "风电", "电池", "充电桩"],
    "科技": ["AI", "人工智能", "芯片", "半导体", "算力", "机器人", "智能驾驶"],
    "能源": ["石油", "天然气", "成品油", "煤炭", "油价", "炼化"],
    "金融": ["银行", "证券", "基金", "保险", "债券", "利率"],
    "消费": ["消费", "零售", "餐饮", "白酒", "旅游", "家电"],
    "医药": ["医药", "创新药", "医疗", "器械", "医院", "生物"],
    "军工": ["军工", "国防", "航天", "导弹", "雷达", "军贸"],
    "周期": ["钢铁", "有色", "化工", "建材", "地产", "基建", "水泥"],
    "汽车": ["汽车", "车企", "整车", "乘用车", "自动驾驶", "智驾"],
    "宏观": ["宏观", "经济", "政策", "规划", "发展改革"],
}

POSITIVE_WORDS = ["增长", "突破", "利好", "上调", "获批", "发布", "成功", "提升", "启动", "签署"]
NEGATIVE_WORDS = ["下滑", "亏损", "立案", "处罚", "暴跌", "紧张", "冲突", "失败", "风险", "警告", "摘牌"]

IMPACT_SCOPE_KEYWORDS: Dict[str, List[str]] = {
    "市场级": ["全市场", "宏观", "全国", "国务院", "央行", "证监会", "战争", "中东", "利率", "通胀"],
    "板块级": ["行业", "板块", "产业", "赛道", "新能源", "半导体", "银行", "石油", "汽车"],
    "产业链局部": ["上游", "中游", "下游", "供应链", "零部件", "材料"],
    "单公司事件": ["公司", "公告", "董事会", "年报", "季报", "回购", "减持", "收购"],
}

IMPACT_SCORE_MAP = {
    "单公司事件": 40,
    "产业链局部": 65,
    "板块级": 80,
    "市场级": 95,
}

INDUSTRY_LV1_ALIASES = {
    "n/a": "宏观",
    "none": "宏观",
    "general": "宏观",
    "general market": "宏观",
    "未知": "宏观",
    "全行业": "宏观",
    "全市场": "宏观",
    "通用": "宏观",
}


def _safe_strip(v: object) -> str:
    return str(v or "").strip()


def _parse_float_with_default(v: object, default: float) -> float:
    s = _safe_strip(v)
    if not s:
        return default
    try:
        return float(s)
    except ValueError:
        return default


def _parse_int_with_default(v: object, default: int) -> int:
    s = _safe_strip(v)
    if not s:
        return default
    try:
        return int(float(s))
    except ValueError:
        return default


def _normalize_industry_lv1(label: str) -> str:
    s = _safe_strip(label)
    if not s:
        return "宏观"
    alias = INDUSTRY_LV1_ALIASES.get(s.lower())
    if alias:
        return alias
    return s


def _read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"missing file: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, headers: List[str], rows: List[Dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in headers})


def _parse_datetime(raw: str) -> datetime:
    s = _safe_strip(raw)
    if not s:
        return datetime.now(CN_TZ)

    # epoch 秒
    if s.isdigit() and len(s) >= 10:
        try:
            return datetime.fromtimestamp(int(s[:10]), tz=CN_TZ)
        except (ValueError, OSError):
            pass

    # YYYY-MM-DD
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=CN_TZ)
        except ValueError:
            return datetime.now(CN_TZ)

    # ISO
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CN_TZ)
        else:
            dt = dt.astimezone(CN_TZ)
        return dt
    except ValueError:
        return datetime.now(CN_TZ)


def _normalize_publish_time(raw: str) -> str:
    return _parse_datetime(raw).isoformat(timespec="seconds")


def _score_by_keywords(text: str, keyword_map: Dict[str, List[str]], default_label: str) -> Tuple[str, int]:
    score_map: Dict[str, int] = {k: 0 for k in keyword_map.keys()}
    for label, kws in keyword_map.items():
        score_map[label] = sum(1 for kw in kws if kw.lower() in text.lower())

    best_label, best_score = default_label, -1
    for label, score in score_map.items():
        if score > best_score:
            best_label, best_score = label, score

    return best_label, max(best_score, 0)


def _infer_subject(text: str) -> Tuple[str, int]:
    label, _ = _score_by_keywords(text, SUBJECT_KEYWORDS, default_label="行业类")
    return label, SUBJECT_SCORE_MAP.get(label, 75)


def _infer_duration(text: str) -> Tuple[str, int]:
    label, _ = _score_by_keywords(text, DURATION_KEYWORDS, default_label="短期型")
    return label, DURATION_SCORE_MAP.get(label, 60)


def _infer_predictability(text: str) -> Tuple[str, int]:
    # 突发关键词优先，避免“即将+突发”混合时被误判为预披露
    sudden_hits = sum(1 for kw in PREDICTABILITY_KEYWORDS["突发型"] if kw.lower() in text.lower())
    if sudden_hits > 0:
        return "突发型", PREDICTABILITY_SCORE_MAP["突发型"]

    pre_hits = sum(1 for kw in PREDICTABILITY_KEYWORDS["预披露型"] if kw.lower() in text.lower())
    if pre_hits > 0:
        return "预披露型", PREDICTABILITY_SCORE_MAP["预披露型"]

    return "半预期型", PREDICTABILITY_SCORE_MAP["半预期型"]


def _infer_industry(text: str) -> Tuple[str, str, int]:
    hits: List[Tuple[str, int, List[str]]] = []
    lower = text.lower()
    for lv1, kws in INDUSTRY_KEYWORDS.items():
        matched = [kw for kw in kws if kw.lower() in lower]
        if matched:
            hits.append((lv1, len(matched), matched))

    if not hits:
        return "宏观", "综合", 40

    hits.sort(key=lambda x: x[1], reverse=True)
    best_lv1, best_cnt, best_matched = hits[0]

    lv2_keywords: List[str] = []
    for _, _, m in hits[:2]:
        lv2_keywords.extend(m)

    lv2_unique = []
    for k in lv2_keywords:
        if k not in lv2_unique:
            lv2_unique.append(k)

    # 命中越多，行业分越高；限制区间 [40, 95]
    industry_score = min(95, 40 + best_cnt * 15)
    return _normalize_industry_lv1(best_lv1), ", ".join(lv2_unique[:4]) if lv2_unique else "综合", industry_score


def _infer_sentiment(text: str) -> Tuple[int, float, int]:
    pos_hits = sum(1 for w in POSITIVE_WORDS if w.lower() in text.lower())
    neg_hits = sum(1 for w in NEGATIVE_WORDS if w.lower() in text.lower())

    if pos_hits == neg_hits:
        polarity = 0
    elif pos_hits > neg_hits:
        polarity = 2 if (pos_hits - neg_hits) >= 2 else 1
    else:
        polarity = -2 if (neg_hits - pos_hits) >= 2 else -1

    if polarity == 2:
        sentiment_score = 100
    elif polarity == 1:
        sentiment_score = 75
    elif polarity == 0:
        sentiment_score = 50
    elif polarity == -1:
        sentiment_score = 35
    else:
        sentiment_score = 20

    conf_raw = 0.6 + 0.1 * min(4, max(pos_hits, neg_hits))
    confidence = min(1.0, max(0.5, conf_raw))

    return polarity, confidence, sentiment_score


def _infer_impact_scope(text: str, subject_type: str) -> Tuple[str, int]:
    # 先按关键词打分
    scope_hits: Dict[str, int] = {k: 0 for k in IMPACT_SCOPE_KEYWORDS.keys()}
    lower = text.lower()
    for scope, kws in IMPACT_SCOPE_KEYWORDS.items():
        scope_hits[scope] = sum(1 for kw in kws if kw.lower() in lower)

    scope = max(scope_hits.items(), key=lambda x: x[1])[0]

    # 结合主体类型微调
    if scope_hits.get(scope, 0) == 0:
        if subject_type in ("宏观类", "地缘类"):
            scope = "市场级"
        elif subject_type == "公司类":
            scope = "单公司事件"
        else:
            scope = "板块级"

    return scope, IMPACT_SCORE_MAP.get(scope, 80)


def _time_decay_score(publish_time: str, now: datetime) -> float:
    dt = _parse_datetime(publish_time)
    days = max(0.0, (now - dt).total_seconds() / 86400)
    # 指数衰减：当天接近100，约30天衰减至~36，最低保底20
    score = 100.0 * math.exp(-days / 30.0)
    return max(20.0, min(100.0, score))


def _heat_score(title: str, content: str, source: str) -> float:
    tlen = len(_safe_strip(title))
    clen = len(_safe_strip(content))
    exclam = title.count("!") + title.count("！")

    base = 40.0
    base += min(20.0, tlen / 4.0)
    base += min(20.0, clen / 80.0)
    base += min(10.0, exclam * 2.0)

    # 来源微调（官方源通常影响更广）
    source_lower = source.lower()
    if any(k in source_lower for k in ["csrc", "ndrc", "gov", "pbc"]):
        base += 10.0

    return max(20.0, min(100.0, base))


def _event_score(
    *,
    subject_score: int,
    duration_score: int,
    predictability_score: int,
    industry_score: int,
    sentiment_score: int,
    impact_scope_score: int,
    title: str,
    content: str,
    source: str,
    publish_time: str,
) -> Tuple[float, Dict[str, float]]:
    now = datetime.now(CN_TZ)
    time_decay = _time_decay_score(publish_time, now)
    heat = _heat_score(title, content, source)

    # 事件强度（结构属性）
    strength = (
        0.30 * subject_score
        + 0.20 * duration_score
        + 0.20 * predictability_score
        + 0.20 * industry_score
        + 0.10 * sentiment_score
    )

    # 综合分：强调强度 + 影响范围 + 热度 + 时效
    score = (
        0.45 * strength
        + 0.25 * impact_scope_score
        + 0.15 * heat
        + 0.15 * time_decay
    )

    score = max(0.0, min(100.0, score))
    breakdown = {
        "strength": round(strength, 2),
        "impact_scope": round(float(impact_scope_score), 2),
        "heat": round(heat, 2),
        "time_decay": round(time_decay, 2),
    }
    return round(score, 2), breakdown


def _load_evaluated_map(path: Path) -> Dict[str, Dict[str, object]]:
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    out: Dict[str, Dict[str, object]] = {}
    if not isinstance(data, list):
        return out

    for item in data:
        if not isinstance(item, dict):
            continue
        event_id = _safe_strip(item.get("event_id", ""))
        evaluation = item.get("evaluation")
        if event_id and isinstance(evaluation, dict):
            out[event_id] = evaluation
    return out


def _discover_evaluated_csv_path() -> Optional[Path]:
    candidates = [
        EVENTS_EVALUATED_CSV_PATH,
        Path.home() / "Downloads" / "events_evaluated.csv",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _load_evaluated_csv(
    path: Optional[Path],
) -> Tuple[Dict[str, Dict[str, object]], Dict[str, Dict[str, str]]]:
    if path is None or not path.exists():
        return {}, {}

    try:
        rows = _read_csv(path)
    except (OSError, FileNotFoundError):
        return {}, {}

    eval_map: Dict[str, Dict[str, object]] = {}
    row_map: Dict[str, Dict[str, str]] = {}

    for r in rows:
        event_id = _safe_strip(r.get("event_id", ""))
        if not event_id:
            continue

        row_map[event_id] = {
            "title": _safe_strip(r.get("title", "")),
            "content": _safe_strip(r.get("content", "")),
            "source": _safe_strip(r.get("source", "")),
            "publish_time": _safe_strip(r.get("publish_time", "")),
            "url": _safe_strip(r.get("url", "")),
            "crawl_time": _safe_strip(r.get("crawl_time", "")),
            "version": _safe_strip(r.get("version", "")),
            "evaluation_reasoning": _safe_strip(r.get("evaluation_reasoning", "")),
        }

        eval_map[event_id] = {
            "duration_type": _safe_strip(r.get("evaluation_duration_type", "")),
            "duration_score": _safe_strip(r.get("evaluation_duration_score", "")),
            "subject_type": _safe_strip(r.get("evaluation_subject_type", "")),
            "subject_score": _safe_strip(r.get("evaluation_subject_score", "")),
            "predictability_type": _safe_strip(r.get("evaluation_predictability_type", "")),
            "predictability_score": _safe_strip(r.get("evaluation_predictability_score", "")),
            "industry_tag_lv1": _safe_strip(r.get("evaluation_industry_tag_lv1", "")),
            "industry_tag_lv2": _safe_strip(r.get("evaluation_industry_tag_lv2", "")),
            "industry_score": _safe_strip(r.get("evaluation_industry_score", "")),
            "sentiment_polarity": _safe_strip(r.get("evaluation_sentiment_polarity", "")),
            "sentiment_confidence": _safe_strip(r.get("evaluation_sentiment_confidence", "")),
            "sentiment_score": _safe_strip(r.get("evaluation_sentiment_score", "")),
            "impact_scope_type": _safe_strip(r.get("evaluation_impact_scope_type", "")),
            "impact_scope_score": _safe_strip(r.get("evaluation_impact_scope_score", "")),
        }

    return eval_map, row_map


def _build_from_external_eval(
    row: Dict[str, str],
    evaluation: Dict[str, object],
) -> Dict[str, str]:
    duration_type = _safe_strip(evaluation.get("duration_type", "")) or "短期型"
    duration_score = _parse_int_with_default(evaluation.get("duration_score", ""), 60)

    subject_type = _safe_strip(evaluation.get("subject_type", "")) or "行业类"
    subject_score = _parse_int_with_default(evaluation.get("subject_score", ""), 75)

    predictability_type = _safe_strip(evaluation.get("predictability_type", "")) or "半预期型"
    predictability_score = _parse_int_with_default(evaluation.get("predictability_score", ""), 65)

    industry_tag_lv1 = _normalize_industry_lv1(_safe_strip(evaluation.get("industry_tag_lv1", "")) or "宏观")
    industry_tag_lv2 = _safe_strip(evaluation.get("industry_tag_lv2", "")) or "综合"
    industry_score = _parse_int_with_default(evaluation.get("industry_score", ""), 60)

    sentiment_polarity = _parse_int_with_default(evaluation.get("sentiment_polarity", ""), 0)
    sentiment_confidence = _parse_float_with_default(evaluation.get("sentiment_confidence", ""), 0.8)
    sentiment_score = _parse_int_with_default(evaluation.get("sentiment_score", ""), 50)

    impact_scope_type = _safe_strip(evaluation.get("impact_scope_type", "")) or "板块级"
    impact_scope_score = _parse_int_with_default(evaluation.get("impact_scope_score", ""), 80)

    event_score, breakdown = _event_score(
        subject_score=subject_score,
        duration_score=duration_score,
        predictability_score=predictability_score,
        industry_score=industry_score,
        sentiment_score=sentiment_score,
        impact_scope_score=impact_scope_score,
        title=row.get("title", ""),
        content=row.get("content", ""),
        source=row.get("source", ""),
        publish_time=row.get("publish_time", ""),
    )

    return {
        "duration_type": duration_type,
        "duration_score": str(duration_score),
        "subject_type": subject_type,
        "subject_score": str(subject_score),
        "predictability_type": predictability_type,
        "predictability_score": str(predictability_score),
        "industry_tag_lv1": industry_tag_lv1,
        "industry_tag_lv2": industry_tag_lv2,
        "industry_score": str(industry_score),
        "sentiment_polarity": str(sentiment_polarity),
        "sentiment_confidence": f"{max(0.0, min(1.0, sentiment_confidence)):.2f}",
        "sentiment_score": str(sentiment_score),
        "impact_scope_type": impact_scope_type,
        "impact_scope_score": str(impact_scope_score),
        "event_score": f"{event_score:.2f}",
        "event_score_breakdown": json.dumps(breakdown, ensure_ascii=False),
    }


def classify_events(
    rows: List[Dict[str, str]],
    external_eval_map: Dict[str, Dict[str, object]] | None = None,
    external_row_map: Dict[str, Dict[str, str]] | None = None,
) -> Tuple[List[Dict[str, str]], Dict[str, int]]:
    external_eval_map = external_eval_map or {}
    external_row_map = external_row_map or {}

    out: List[Dict[str, str]] = []
    used_external = 0
    filled_content = 0
    filled_publish_time = 0

    for r in rows:
        base = {k: _safe_strip(r.get(k, "")) for k in INPUT_HEADERS}
        event_id = base.get("event_id", "")
        ext_row = external_row_map.get(event_id) if event_id else None

        if ext_row:
            # 缺失字段回填：仅在当前字段为空时补齐
            for field in ["title", "source", "url", "crawl_time", "version"]:
                if not _safe_strip(base.get(field, "")) and _safe_strip(ext_row.get(field, "")):
                    base[field] = _safe_strip(ext_row.get(field, ""))

            if not _safe_strip(base.get("content", "")):
                content_candidate = _safe_strip(ext_row.get("content", ""))
                if not content_candidate:
                    content_candidate = _safe_strip(ext_row.get("evaluation_reasoning", ""))
                if content_candidate:
                    base["content"] = content_candidate
                    filled_content += 1

            if not _safe_strip(base.get("publish_time", "")):
                publish_candidate = _safe_strip(ext_row.get("publish_time", ""))
                if not publish_candidate:
                    publish_candidate = _safe_strip(ext_row.get("crawl_time", ""))
                if publish_candidate:
                    base["publish_time"] = publish_candidate
                    filled_publish_time += 1

        # 最终兜底：publish_time 为空则使用 crawl_time
        if not _safe_strip(base.get("publish_time", "")) and _safe_strip(base.get("crawl_time", "")):
            base["publish_time"] = _safe_strip(base.get("crawl_time", ""))

        base["publish_time"] = _normalize_publish_time(base.get("publish_time", ""))

        text = " ".join([base.get("title", ""), base.get("content", ""), base.get("source", "")]).strip()

        ext = external_eval_map.get(event_id) if event_id else None

        if ext:
            cls = _build_from_external_eval(base, ext)
            used_external += 1
        else:
            subject_type, subject_score = _infer_subject(text)
            duration_type, duration_score = _infer_duration(text)
            predictability_type, predictability_score = _infer_predictability(text)
            industry_tag_lv1, industry_tag_lv2, industry_score = _infer_industry(text)
            sentiment_polarity, sentiment_confidence, sentiment_score = _infer_sentiment(text)
            impact_scope_type, impact_scope_score = _infer_impact_scope(text, subject_type)

            event_score, breakdown = _event_score(
                subject_score=subject_score,
                duration_score=duration_score,
                predictability_score=predictability_score,
                industry_score=industry_score,
                sentiment_score=sentiment_score,
                impact_scope_score=impact_scope_score,
                title=base.get("title", ""),
                content=base.get("content", ""),
                source=base.get("source", ""),
                publish_time=base.get("publish_time", ""),
            )

            cls = {
                "duration_type": duration_type,
                "duration_score": str(duration_score),
                "subject_type": subject_type,
                "subject_score": str(subject_score),
                "predictability_type": predictability_type,
                "predictability_score": str(predictability_score),
                "industry_tag_lv1": industry_tag_lv1,
                "industry_tag_lv2": industry_tag_lv2,
                "industry_score": str(industry_score),
                "sentiment_polarity": str(sentiment_polarity),
                "sentiment_confidence": f"{sentiment_confidence:.2f}",
                "sentiment_score": str(sentiment_score),
                "impact_scope_type": impact_scope_type,
                "impact_scope_score": str(impact_scope_score),
                "event_score": f"{event_score:.2f}",
                "event_score_breakdown": json.dumps(breakdown, ensure_ascii=False),
            }

        row_out = {**base, **cls}
        out.append(row_out)

    out.sort(key=lambda x: (x.get("publish_time", ""), x.get("event_score", "0")), reverse=True)

    stat = {
        "input_rows": len(rows),
        "output_rows": len(out),
        "used_external_eval": used_external,
        "filled_content": filled_content,
        "filled_publish_time": filled_publish_time,
    }
    return out, stat


def _null_ratio(rows: List[Dict[str, str]], cols: List[str]) -> Dict[str, float]:
    if not rows:
        return {c: 1.0 for c in cols}
    total = len(rows)
    out: Dict[str, float] = {}
    for c in cols:
        miss = sum(1 for r in rows if not _safe_strip(r.get(c, "")))
        out[c] = miss / total
    return out


def main() -> None:
    events_clean = _read_csv(EVENTS_CLEAN_PATH)
    eval_map = _load_evaluated_map(EVENTS_EVALUATED_JSON_PATH)

    evaluated_csv_path = _discover_evaluated_csv_path()
    csv_eval_map, csv_row_map = _load_evaluated_csv(evaluated_csv_path)

    # 优先级：JSON > CSV（若JSON不存在对应event_id，则用CSV）
    for k, v in csv_eval_map.items():
        eval_map.setdefault(k, v)

    events_structured, stat = classify_events(
        events_clean,
        external_eval_map=eval_map,
        external_row_map=csv_row_map,
    )
    _write_csv(EVENTS_STRUCTURED_PATH, OUTPUT_HEADERS, events_structured)

    key_null = _null_ratio(
        events_structured,
        [
            "event_id",
            "title",
            "publish_time",
            "subject_type",
            "industry_tag_lv1",
            "event_score",
        ],
    )

    print(
        f"[INFO] events_structured -> {EVENTS_STRUCTURED_PATH} | "
        f"rows={stat['output_rows']} | used_external_eval={stat['used_external_eval']}"
    )
    print(
        f"[INFO] 缺失补齐: content={stat['filled_content']} | "
        f"publish_time={stat['filled_publish_time']} | "
        f"evaluated_csv={'none' if evaluated_csv_path is None else evaluated_csv_path}"
    )
    print(f"[INFO] 关键字段缺失率: {key_null}")


if __name__ == "__main__":
    main()
