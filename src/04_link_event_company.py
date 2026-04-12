from __future__ import annotations

import csv
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(".")
PROCESSED_DIR = ROOT / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

EVENTS_STRUCTURED_PATH = PROCESSED_DIR / "events_structured.csv"
COMPANY_PROFILE_PATH = PROCESSED_DIR / "company_profile.csv"
MARKET_CLEAN_PATH = PROCESSED_DIR / "market_daily_clean.csv"
OUT_PATH = PROCESSED_DIR / "event_company_links.csv"

CN_TZ = timezone(timedelta(hours=8))

OUT_HEADERS = [
    "event_id",
    "ts_code",
    "mention_score",
    "industry_score",
    "equity_score",
    "co_move_score",
    "link_score",
    "score_breakdown",
]


def _safe_strip(v: object) -> str:
    return str(v or "").strip()


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

    if s.isdigit() and len(s) >= 10:
        try:
            return datetime.fromtimestamp(int(s[:10]), tz=CN_TZ)
        except (ValueError, OSError):
            pass

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=CN_TZ)
        except ValueError:
            return datetime.now(CN_TZ)

    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CN_TZ)
        else:
            dt = dt.astimezone(CN_TZ)
        return dt
    except ValueError:
        return datetime.now(CN_TZ)


def _normalize_name_aliases(company_name: str) -> List[str]:
    name = _safe_strip(company_name)
    if not name:
        return []

    aliases = [name]
    for suffix in ["股份有限公司", "股份", "有限公司", "集团", "控股", "公司", "Ａ", "A"]:
        x = name.replace(suffix, "")
        x = _safe_strip(x)
        if len(x) >= 2 and x not in aliases:
            aliases.append(x)

    return aliases


def _extract_stock_codes(text: str) -> List[str]:
    # 识别 600519.SH / 000001.SZ / 600519
    out: List[str] = []

    for m in re.findall(r"\b(\d{6}\.(?:SH|SZ|BJ))\b", text, flags=re.IGNORECASE):
        out.append(m.upper())

    for m in re.findall(r"(?<!\d)((?:00|30|60|68)\d{4})(?!\d)", text):
        if m.startswith(("60", "68")):
            out.append(f"{m}.SH")
        else:
            out.append(f"{m}.SZ")

    dedup: List[str] = []
    seen = set()
    for x in out:
        if x not in seen:
            seen.add(x)
            dedup.append(x)
    return dedup


def _mention_score(event_text: str, title: str, ts_code: str, company_name: str) -> Tuple[float, Dict[str, object]]:
    event_text_lower = event_text.lower()
    title_lower = title.lower()

    ts_code_hit = ts_code.lower() in event_text_lower
    pure_code = ts_code.split(".")[0]
    pure_code_hit = pure_code in event_text

    aliases = _normalize_name_aliases(company_name)
    title_name_hits = [a for a in aliases if a.lower() in title_lower and len(a) >= 2]
    text_name_hits = [a for a in aliases if a.lower() in event_text_lower and len(a) >= 2]

    score = 0.0
    if ts_code_hit:
        score = max(score, 100.0)
    if pure_code_hit:
        score = max(score, 90.0)
    if title_name_hits:
        score = max(score, 95.0)
    if text_name_hits:
        score = max(score, 85.0)

    # 标题里仅命中简称，稍微提权
    if title_name_hits and not ts_code_hit:
        score = max(score, 92.0)

    return score, {
        "ts_code_hit": ts_code_hit,
        "pure_code_hit": pure_code_hit,
        "title_name_hits": title_name_hits[:3],
        "text_name_hits": text_name_hits[:3],
    }


def _industry_score(
    event_industry_lv1: str,
    event_industry_lv2: str,
    event_text: str,
    company_industry: str,
    concept_tags: str,
) -> Tuple[float, Dict[str, object]]:
    lv1 = _safe_strip(event_industry_lv1)
    lv2_tokens = [t.strip().lower() for t in re.split(r"[,;，、]\s*", _safe_strip(event_industry_lv2)) if t.strip()]

    ci = _safe_strip(company_industry)
    ci_lower = ci.lower()
    concepts = [t.strip().lower() for t in _safe_strip(concept_tags).split(";") if t.strip()]

    score = 0.0
    detail: Dict[str, object] = {
        "lv1_match": False,
        "lv2_hits": [],
        "industry": ci,
        "concept_hit_count": 0,
    }

    if lv1 and lv1.lower() in ci_lower:
        score += 55.0
        detail["lv1_match"] = True

    lv2_hits = [t for t in lv2_tokens if (t in ci_lower or t in concepts or t in event_text.lower())]
    if lv2_hits:
        score += min(35.0, 15.0 + 10.0 * len(lv2_hits))
    detail["lv2_hits"] = lv2_hits[:5]

    concept_hit = sum(1 for t in lv2_tokens if t in concepts)
    if concept_hit > 0:
        score += min(20.0, 5.0 * concept_hit)
    detail["concept_hit_count"] = concept_hit

    # 若都没命中，用行业关键词弱匹配
    if score == 0.0 and ci:
        if any(k in event_text for k in [ci, ci.replace("行业", "")]):
            score = 40.0

    return min(100.0, score), detail


def _equity_score(event_text: str, mention_score: float) -> Tuple[float, Dict[str, object]]:
    text = event_text.lower()
    relation_words = ["控股", "子公司", "参股", "并购", "收购", "并表", "股东", "持股", "战略合作", "签署"]
    hits = [w for w in relation_words if w in text]

    if mention_score >= 85 and hits:
        score = 70.0 + min(20.0, 5.0 * len(hits))
    elif mention_score >= 85:
        score = 45.0
    elif hits:
        score = 30.0 + min(15.0, 5.0 * len(hits))
    else:
        score = 10.0

    return min(100.0, score), {"relation_hits": hits[:6]}


def _build_market_index(market_rows: List[Dict[str, str]]) -> Dict[str, Dict[str, Dict[str, float]]]:
    idx: Dict[str, Dict[str, Dict[str, float]]] = {}
    for r in market_rows:
        ts_code = _safe_strip(r.get("ts_code", ""))
        trade_date = _safe_strip(r.get("trade_date", ""))
        if not ts_code or not trade_date:
            continue

        try:
            pct = float(_safe_strip(r.get("pct_chg", "") or "0"))
        except ValueError:
            pct = 0.0
        try:
            vol = float(_safe_strip(r.get("vol", "") or "0"))
        except ValueError:
            vol = 0.0

        idx.setdefault(ts_code, {})[trade_date] = {"pct_chg": pct, "vol": vol}
    return idx


def _co_move_score(
    ts_code: str,
    publish_dt: datetime,
    market_idx: Dict[str, Dict[str, Dict[str, float]]],
) -> Tuple[float, Dict[str, object]]:
    by_date = market_idx.get(ts_code)
    if not by_date:
        return 20.0, {"reason": "no_market_data"}

    candidates = []
    for i in range(0, 4):
        d = (publish_dt.date() + timedelta(days=i)).isoformat()
        if d in by_date:
            candidates.append((d, by_date[d]))

    if not candidates:
        return 20.0, {"reason": "no_near_trade_date"}

    d, rec = candidates[0]
    abs_pct = abs(float(rec.get("pct_chg", 0.0)))
    vol = float(rec.get("vol", 0.0))

    # 价格振幅主导，共振次之
    score = 25.0 + min(55.0, abs_pct * 6.0)
    if vol > 0:
        score += min(20.0, math.log10(max(1.0, vol)) * 2.5)

    return min(100.0, score), {
        "trade_date": d,
        "abs_pct_chg": round(abs_pct, 4),
        "vol": int(vol),
    }


def build_links(
    events_rows: List[Dict[str, str]],
    company_rows: List[Dict[str, str]],
    market_rows: List[Dict[str, str]],
) -> Tuple[List[Dict[str, str]], Dict[str, int]]:
    max_per_event = int(os.getenv("LINK_MAX_PER_EVENT", "5"))
    min_link_score = float(os.getenv("LINK_MIN_SCORE", "25"))
    force_fallback = os.getenv("LINK_FORCE_FALLBACK", "0") == "1"

    market_idx = _build_market_index(market_rows)

    out: List[Dict[str, str]] = []
    total_events = 0
    fallback_events = 0

    for ev in events_rows:
        total_events += 1
        event_id = _safe_strip(ev.get("event_id", ""))
        title = _safe_strip(ev.get("title", ""))
        content = _safe_strip(ev.get("content", ""))
        text = f"{title} {content} {_safe_strip(ev.get('url', ''))}".strip()

        publish_dt = _parse_datetime(_safe_strip(ev.get("publish_time", "")))
        event_industry_lv1 = _safe_strip(ev.get("industry_tag_lv1", ""))
        event_industry_lv2 = _safe_strip(ev.get("industry_tag_lv2", ""))

        parsed_codes = set(_extract_stock_codes(text))

        scored: List[Tuple[float, Dict[str, str]]] = []

        for cp in company_rows:
            ts_code = _safe_strip(cp.get("ts_code", ""))
            if not ts_code:
                continue

            company_name = _safe_strip(cp.get("company_name", ""))
            company_industry = _safe_strip(cp.get("industry", ""))
            concept_tags = _safe_strip(cp.get("concept_tags", ""))

            mention_score, mention_detail = _mention_score(text, title, ts_code, company_name)

            # 识别到明确股票代码时，未命中的公司直接弱化处理
            if parsed_codes and ts_code not in parsed_codes and mention_score < 85:
                mention_score = min(mention_score, 10.0)

            industry_score, industry_detail = _industry_score(
                event_industry_lv1,
                event_industry_lv2,
                text,
                company_industry,
                concept_tags,
            )
            equity_score, equity_detail = _equity_score(text, mention_score)
            co_move_score, co_move_detail = _co_move_score(ts_code, publish_dt, market_idx)

            link_score = (
                0.45 * mention_score
                + 0.25 * industry_score
                + 0.15 * equity_score
                + 0.15 * co_move_score
            )

            # 低置信度组合降权：既无文本提及、也无明显行业匹配
            if mention_score < 20 and industry_score < 40:
                link_score = min(link_score, 18.0)

            link_score = max(0.0, min(100.0, link_score))

            breakdown = {
                "mention_detail": mention_detail,
                "industry_detail": industry_detail,
                "equity_detail": equity_detail,
                "co_move_detail": co_move_detail,
                "weights": {
                    "mention": 0.45,
                    "industry": 0.25,
                    "equity": 0.15,
                    "co_move": 0.15,
                },
            }

            row = {
                "event_id": event_id,
                "ts_code": ts_code,
                "mention_score": f"{mention_score:.2f}",
                "industry_score": f"{industry_score:.2f}",
                "equity_score": f"{equity_score:.2f}",
                "co_move_score": f"{co_move_score:.2f}",
                "link_score": f"{link_score:.2f}",
                "score_breakdown": json.dumps(breakdown, ensure_ascii=False),
            }
            scored.append((link_score, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        kept = [r for s, r in scored if s >= min_link_score][:max_per_event]

        # 默认不兜底，避免引入噪声；可通过 LINK_FORCE_FALLBACK=1 开启
        if force_fallback and (not kept) and scored:
            kept = [scored[0][1]]
            fallback_events += 1

        out.extend(kept)

    stat = {
        "events": total_events,
        "links": len(out),
        "fallback_events": fallback_events,
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
    events_rows = _read_csv(EVENTS_STRUCTURED_PATH)
    company_rows = _read_csv(COMPANY_PROFILE_PATH)
    market_rows = _read_csv(MARKET_CLEAN_PATH)

    links, stat = build_links(events_rows, company_rows, market_rows)
    _write_csv(OUT_PATH, OUT_HEADERS, links)

    nulls = _null_ratio(links, ["event_id", "ts_code", "link_score", "score_breakdown"])
    print(
        f"[INFO] event_company_links -> {OUT_PATH} | "
        f"events={stat['events']} | links={stat['links']} | fallback_events={stat['fallback_events']}"
    )
    print(f"[INFO] 关键字段缺失率: {nulls}")


if __name__ == "__main__":
    main()
