from __future__ import annotations

import csv
import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(".")
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

EVENTS_RAW_PATH = RAW_DIR / "events_raw.csv"
MARKET_RAW_PATH = RAW_DIR / "market_daily.csv"

EVENTS_CLEAN_PATH = PROCESSED_DIR / "events_clean.csv"
MARKET_CLEAN_PATH = PROCESSED_DIR / "market_daily_clean.csv"

EVENT_HEADERS = [
    "event_id",
    "title",
    "content",
    "source",
    "publish_time",
    "url",
    "crawl_time",
    "version",
]

MARKET_HEADERS = [
    "ts_code",
    "trade_date",
    "open",
    "close",
    "high",
    "low",
    "vol",
    "amount",
    "pct_chg",
]

CN_TZ = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _safe_strip(v: object) -> str:
    return str(v or "").strip()


def _clean_text(s: str) -> str:
    t = _safe_strip(s)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"&nbsp;|&amp;|&lt;|&gt;", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _to_event_id(title: str, source: str, publish_time: str, url: str) -> str:
    key = f"{title}|{source}|{publish_time}|{url}".encode("utf-8", errors="ignore")
    return hashlib.md5(key).hexdigest()


def _normalize_datetime(raw: str, *, default_empty: str = "") -> str:
    s = _safe_strip(raw)
    if not s:
        return default_empty

    # epoch 秒
    if s.isdigit() and len(s) >= 10:
        try:
            ts = int(s[:10])
            return datetime.fromtimestamp(ts, tz=CN_TZ).isoformat(timespec="seconds")
        except (ValueError, OSError):
            pass

    # YYYY-MM-DD
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=CN_TZ)
            return dt.isoformat(timespec="seconds")
        except ValueError:
            return default_empty

    # YYYYMMDD
    if re.fullmatch(r"\d{8}", s):
        try:
            dt = datetime.strptime(s, "%Y%m%d").replace(tzinfo=CN_TZ)
            return dt.isoformat(timespec="seconds")
        except ValueError:
            return default_empty

    # ISO
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CN_TZ)
        else:
            dt = dt.astimezone(CN_TZ)
        return dt.isoformat(timespec="seconds")
    except ValueError:
        return default_empty


def _normalize_trade_date(raw: str) -> str:
    s = _safe_strip(raw)
    if not s:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    if re.fullmatch(r"\d{8}", s):
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return ""


def _normalize_ts_code(raw: str) -> str:
    s = _safe_strip(raw).upper()
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", s):
        return s
    return ""


def _to_float_str(raw: str, decimals: int | None = 6) -> str:
    s = _safe_strip(raw)
    if not s:
        return ""
    try:
        x = float(s)
    except ValueError:
        return ""
    if decimals is None:
        return str(x)
    return f"{x:.{decimals}f}"


def _to_float(raw: str) -> float | None:
    s = _safe_strip(raw)
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_int_like_str(raw: str) -> str:
    s = _safe_strip(raw)
    if not s:
        return ""
    try:
        return f"{float(s):.0f}"
    except ValueError:
        return ""


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


def clean_events(rows: List[Dict[str, str]]) -> Tuple[List[Dict[str, str]], Dict[str, int]]:
    now_iso = _now_iso()
    by_event_id: Dict[str, Dict[str, str]] = {}
    dropped_empty = 0

    for r in rows:
        title = _clean_text(r.get("title", ""))
        url = _safe_strip(r.get("url", ""))
        if not title or not url:
            dropped_empty += 1
            continue

        content = _clean_text(r.get("content", ""))
        source = _safe_strip(r.get("source", ""))
        crawl_time = _normalize_datetime(r.get("crawl_time", ""), default_empty=now_iso) or now_iso
        publish_time = _normalize_datetime(r.get("publish_time", ""), default_empty="") or crawl_time
        version = _safe_strip(r.get("version", "")) or "v1"

        event_id = _safe_strip(r.get("event_id", ""))
        if not event_id:
            event_id = _to_event_id(title, source, publish_time, url)

        row = {
            "event_id": event_id,
            "title": title,
            "content": content,
            "source": source,
            "publish_time": publish_time,
            "url": url,
            "crawl_time": crawl_time,
            "version": version,
        }

        # 如果 event_id 冲突，保留信息量更高的那条
        old = by_event_id.get(event_id)
        if old is None:
            by_event_id[event_id] = row
        else:
            old_score = len(old.get("content", "")) + len(old.get("title", ""))
            new_score = len(content) + len(title)
            if new_score > old_score:
                by_event_id[event_id] = row

    # 二次去重：标题 + 发布时间 + URL
    uniq_key: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    for r in by_event_id.values():
        k = (r["title"], r["publish_time"], r["url"])
        uniq_key[k] = r

    cleaned = list(uniq_key.values())
    cleaned.sort(key=lambda x: (x.get("publish_time", ""), x.get("crawl_time", "")), reverse=True)

    stat = {
        "input_rows": len(rows),
        "dropped_empty": dropped_empty,
        "output_rows": len(cleaned),
    }
    return cleaned, stat


def clean_market(rows: List[Dict[str, str]]) -> Tuple[List[Dict[str, str]], Dict[str, int]]:
    dedup: Dict[Tuple[str, str], Dict[str, str]] = {}
    dropped_bad_key = 0

    for r in rows:
        ts_code = _normalize_ts_code(r.get("ts_code", ""))
        trade_date = _normalize_trade_date(r.get("trade_date", ""))
        if not ts_code or not trade_date:
            dropped_bad_key += 1
            continue

        open_ = _to_float_str(r.get("open", ""), 6)
        close = _to_float_str(r.get("close", ""), 6)
        high = _to_float_str(r.get("high", ""), 6)
        low = _to_float_str(r.get("low", ""), 6)
        vol = _to_int_like_str(r.get("vol", ""))
        amount = _to_float_str(r.get("amount", ""), 2)
        pct_chg = _to_float_str(r.get("pct_chg", ""), 6)

        # public 源常缺失 amount，用 vol * close 做近似回填
        if not amount:
            vol_f = _to_float(vol)
            close_f = _to_float(close)
            if vol_f is not None and close_f is not None:
                amount = f"{(vol_f * close_f):.2f}"

        # high/low 缺失时做轻量补全
        nums = [x for x in [open_, close, high, low] if x]
        if nums:
            vals = [float(x) for x in nums]
            if not high:
                high = f"{max(vals):.6f}"
            if not low:
                low = f"{min(vals):.6f}"

        row = {
            "ts_code": ts_code,
            "trade_date": trade_date,
            "open": open_,
            "close": close,
            "high": high,
            "low": low,
            "vol": vol,
            "amount": amount,
            "pct_chg": pct_chg,
        }

        dedup[(ts_code, trade_date)] = row

    cleaned = list(dedup.values())
    cleaned.sort(key=lambda x: (x["ts_code"], x["trade_date"]))

    stat = {
        "input_rows": len(rows),
        "dropped_bad_key": dropped_bad_key,
        "output_rows": len(cleaned),
    }
    return cleaned, stat


def _null_ratio(rows: List[Dict[str, str]], cols: List[str]) -> Dict[str, float]:
    if not rows:
        return {c: 1.0 for c in cols}
    out: Dict[str, float] = {}
    total = len(rows)
    for c in cols:
        miss = sum(1 for r in rows if not _safe_strip(r.get(c, "")))
        out[c] = miss / total
    return out


def main() -> None:
    events_raw = _read_csv(EVENTS_RAW_PATH)
    market_raw = _read_csv(MARKET_RAW_PATH)

    events_clean, event_stat = clean_events(events_raw)
    market_clean, market_stat = clean_market(market_raw)

    _write_csv(EVENTS_CLEAN_PATH, EVENT_HEADERS, events_clean)
    _write_csv(MARKET_CLEAN_PATH, MARKET_HEADERS, market_clean)

    event_null = _null_ratio(events_clean, ["event_id", "title", "source", "publish_time", "url"]) 
    market_null = _null_ratio(market_clean, ["ts_code", "trade_date", "open", "close", "high", "low", "vol"]) 

    print(f"[INFO] events_clean -> {EVENTS_CLEAN_PATH} | rows={event_stat['output_rows']} | dropped_empty={event_stat['dropped_empty']}")
    print(f"[INFO] market_daily_clean -> {MARKET_CLEAN_PATH} | rows={market_stat['output_rows']} | dropped_bad_key={market_stat['dropped_bad_key']}")
    print(f"[INFO] events 关键字段缺失率: {event_null}")
    print(f"[INFO] market 关键字段缺失率: {market_null}")


if __name__ == "__main__":
    main()
