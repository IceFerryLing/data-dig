from __future__ import annotations

import csv
import json
import os
import re
import ssl
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(".")
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

MARKET_CLEAN_PATH = PROCESSED_DIR / "market_daily_clean.csv"
MARKET_RAW_PATH = RAW_DIR / "market_daily.csv"
OUT_PATH = PROCESSED_DIR / "company_profile.csv"

OUT_HEADERS = ["ts_code", "company_name", "industry", "concept_tags", "list_date", "exchange"]


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        key = k.strip()
        val = v.strip()
        if key:
            os.environ.setdefault(key, val)


def _safe_strip(v: object) -> str:
    return str(v or "").strip()


def _read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, headers: List[str], rows: List[Dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in headers})


def _normalize_ts_code(raw: str) -> str:
    s = _safe_strip(raw).upper()
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", s):
        return s
    if re.fullmatch(r"\d{6}", s):
        if s.startswith(("6", "9")):
            return f"{s}.SH"
        if s.startswith(("0", "2", "3")):
            return f"{s}.SZ"
        if s.startswith(("4", "8")):
            return f"{s}.BJ"
    return ""


def _exchange_from_ts_code(ts_code: str) -> str:
    s = _normalize_ts_code(ts_code)
    if s.endswith(".SH"):
        return "SSE"
    if s.endswith(".SZ"):
        return "SZSE"
    if s.endswith(".BJ"):
        return "BSE"
    return ""


def _format_date_yyyymmdd(raw: str) -> str:
    s = _safe_strip(raw)
    if re.fullmatch(r"\d{8}", s):
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    return ""


def _collect_universe_ts_codes() -> List[str]:
    rows = _read_csv(MARKET_CLEAN_PATH)
    if not rows:
        rows = _read_csv(MARKET_RAW_PATH)

    out: List[str] = []
    seen: Set[str] = set()
    for r in rows:
        ts_code = _normalize_ts_code(r.get("ts_code", ""))
        if not ts_code or ts_code in seen:
            continue
        seen.add(ts_code)
        out.append(ts_code)

    out.sort()
    return out


def _tushare_call(token: str, api_name: str, params: Dict[str, str], fields: str) -> Dict[str, object]:
    payload = {
        "api_name": api_name,
        "token": token,
        "params": params,
        "fields": fields,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = Request(
        "https://api.tushare.pro",
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        },
        method="POST",
    )

    context = ssl.create_default_context()
    with urlopen(req, timeout=45, context=context) as resp:  # nosec - trusted official API
        raw = resp.read().decode("utf-8", errors="ignore")
    return json.loads(raw)


def _extract_items(resp: Dict[str, object]) -> List[List[object]]:
    if (resp or {}).get("code") not in (0, None):
        raise RuntimeError(f"Tushare API error: code={(resp or {}).get('code')} msg={(resp or {}).get('msg')}")
    return (((resp or {}).get("data") or {}).get("items") or [])


def _fetch_stock_basic(token: str) -> Dict[str, Dict[str, str]]:
    resp = _tushare_call(
        token,
        "stock_basic",
        {"exchange": "", "list_status": "L"},
        "ts_code,symbol,name,area,industry,list_date,market,exchange",
    )
    items = _extract_items(resp)

    out: Dict[str, Dict[str, str]] = {}
    for it in items:
        if len(it) < 8:
            continue
        ts_code, _symbol, name, _area, industry, list_date, _market, exchange = it
        t = _normalize_ts_code(str(ts_code))
        if not t:
            continue
        out[t] = {
            "company_name": _safe_strip(name),
            "industry": _safe_strip(industry) or "综合",
            "list_date": _format_date_yyyymmdd(str(list_date)),
            "exchange": {
                "SSE": "SSE",
                "SZSE": "SZSE",
                "BSE": "BSE",
                "SH": "SSE",
                "SZ": "SZSE",
                "BJ": "BSE",
            }.get(_safe_strip(exchange).upper(), _exchange_from_ts_code(t)),
        }
    return out


def _fetch_concept_tags(token: str, ts_codes: List[str]) -> Dict[str, List[str]]:
    """
    可选：概念标签。
    Tushare concept_detail 支持 ts_code 查询，这里按股票逐只查询（稳定但较慢）。
    通过环境变量 COMPANY_PROFILE_ENABLE_CONCEPTS 控制是否启用。
    """
    enable = os.getenv("COMPANY_PROFILE_ENABLE_CONCEPTS", "0").strip() == "1"
    if not enable:
        return {}

    max_symbols = int(os.getenv("COMPANY_PROFILE_MAX_CONCEPT_SYMBOLS", "120"))
    targets = ts_codes[:max_symbols]

    out: Dict[str, List[str]] = {t: [] for t in targets}
    for idx, ts_code in enumerate(targets, start=1):
        try:
            resp = _tushare_call(
                token,
                "concept_detail",
                {"ts_code": ts_code},
                "id,concept_name,ts_code,name,in_date,out_date",
            )
            items = _extract_items(resp)
            tags = []
            for it in items:
                if len(it) < 2:
                    continue
                c_name = _safe_strip(it[1])
                if c_name and c_name not in tags:
                    tags.append(c_name)
            out[ts_code] = tags
        except (URLError, HTTPError, TimeoutError, RuntimeError, json.JSONDecodeError):
            # 单只失败不影响全局
            out[ts_code] = out.get(ts_code, [])
            continue

        if idx % 30 == 0:
            print(f"[INFO] concept tags progress: {idx}/{len(targets)}")

    return out


def build_company_profile() -> Tuple[List[Dict[str, str]], Dict[str, int]]:
    _load_env_file(ROOT / ".env")

    ts_codes = _collect_universe_ts_codes()
    if not ts_codes:
        return [], {"universe": 0, "from_tushare": 0, "from_fallback": 0}

    token = _safe_strip(os.getenv("TUSHARE_TOKEN", ""))
    use_tushare = bool(token and token != "your_tushare_token_here")

    tushare_map: Dict[str, Dict[str, str]] = {}
    concept_map: Dict[str, List[str]] = {}

    if use_tushare:
        try:
            tushare_map = _fetch_stock_basic(token)
            concept_map = _fetch_concept_tags(token, ts_codes)
        except (URLError, HTTPError, TimeoutError, RuntimeError, json.JSONDecodeError) as e:
            print(f"[WARN] Tushare 拉取失败，将降级生成骨架数据: {e}")
            tushare_map = {}
            concept_map = {}

    out: List[Dict[str, str]] = []
    from_tushare = 0
    from_fallback = 0

    for ts_code in ts_codes:
        base = tushare_map.get(ts_code)
        if base:
            from_tushare += 1
            company_name = base.get("company_name", "") or ts_code
            industry = base.get("industry", "") or "综合"
            list_date = base.get("list_date", "")
            exchange = base.get("exchange", "") or _exchange_from_ts_code(ts_code)
            tags = concept_map.get(ts_code, [])
            concept_tags = ";".join(tags)
        else:
            from_fallback += 1
            company_name = ts_code
            industry = "综合"
            list_date = ""
            exchange = _exchange_from_ts_code(ts_code)
            concept_tags = ""

        out.append(
            {
                "ts_code": ts_code,
                "company_name": company_name,
                "industry": industry,
                "concept_tags": concept_tags,
                "list_date": list_date,
                "exchange": exchange,
            }
        )

    out.sort(key=lambda x: x["ts_code"])
    stat = {
        "universe": len(ts_codes),
        "from_tushare": from_tushare,
        "from_fallback": from_fallback,
    }
    return out, stat


def main() -> None:
    rows, stat = build_company_profile()
    _write_csv(OUT_PATH, OUT_HEADERS, rows)

    print(
        f"[INFO] company_profile -> {OUT_PATH} | rows={len(rows)} | "
        f"tushare={stat['from_tushare']} | fallback={stat['from_fallback']}"
    )
    if stat["from_tushare"] == 0:
        print("[HINT] 未获取到Tushare公司信息，当前输出为骨架数据。请在 .env 中配置有效 TUSHARE_TOKEN。")
    if os.getenv("COMPANY_PROFILE_ENABLE_CONCEPTS", "0").strip() != "1":
        print("[HINT] 当前未启用概念标签抓取（concept_tags）。如需启用，在 .env 设置 COMPANY_PROFILE_ENABLE_CONCEPTS=1。")


if __name__ == "__main__":
    main()
