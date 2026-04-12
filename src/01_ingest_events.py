from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.error import URLError, HTTPError
from urllib.request import Request
from urllib.request import urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

EVENTS_PATH = RAW_DIR / "events_raw.csv"
MARKET_PATH = RAW_DIR / "market_daily.csv"


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe_text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _to_event_id(title: str, source: str, publish_time: str, url: str) -> str:
    key = f"{title}|{source}|{publish_time}|{url}".encode("utf-8", errors="ignore")
    return hashlib.md5(key).hexdigest()


def _urlopen_bytes(url: str, timeout: int = 12) -> bytes:
    # 某些网络环境（学校/公司代理）会导致证书链异常，这里提供兼容模式。
    context = ssl.create_default_context()
    if os.getenv("IGNORE_SSL_VERIFY", "1") == "1":
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "text/html,application/json,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "close",
        },
    )

    with urlopen(req, timeout=timeout, context=context) as resp:  # nosec - data collection for competition
        return resp.read()


def _fetch_xml(url: str, timeout: int = 12) -> bytes:
    return _urlopen_bytes(url, timeout=timeout)


def fetch_events_from_rss() -> List[Dict[str, str]]:
    """抓取公开政府新闻列表页，兼容 RSS 不稳定场景。"""
    html_sources: List[Tuple[str, str]] = [
        ("gov_cn_yaowen", "http://www.gov.cn/yaowen/index.htm"),
        ("gov_cn_zhengce_zuixin", "http://www.gov.cn/zhengce/zuixin.htm"),
        ("sina_finance_roll", "https://finance.sina.com.cn/roll/"),
    ]

    crawl_time = _now_iso()
    rows: List[Dict[str, str]] = []

    # 主数据源：新浪财经滚动新闻 JSON API
    sina_api = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=30&page=1"
    try:
        payload = json.loads(_urlopen_bytes(sina_api, timeout=15).decode("utf-8", errors="ignore"))
        data_items = (((payload or {}).get("result") or {}).get("data") or [])
        for it in data_items:
            title = str(it.get("title") or "").strip()
            if len(title) < 8:
                continue
            link = str(it.get("url") or "").strip()
            if not link:
                continue
            intro = str(it.get("intro") or "").strip()
            ctime = str(it.get("ctime") or "").strip()

            rows.append(
                {
                    "event_id": _to_event_id(title, "sina_roll_api", ctime, link),
                    "title": title,
                    "content": intro,
                    "source": "sina_roll_api",
                    "publish_time": ctime,
                    "url": link,
                    "crawl_time": crawl_time,
                    "version": "v1",
                }
            )
    except (URLError, HTTPError, TimeoutError, json.JSONDecodeError) as e:
        print(f"[WARN] 新浪事件API抓取失败: {e}")

    for source_name, page_url in html_sources:
        try:
            html = _urlopen_bytes(page_url).decode("utf-8", errors="ignore")

            # 处理 gov 页面常见 JS 跳转
            redirect_match = re.search(r'window\.location\.href\s*=\s*"([^"]+)"', html)
            if redirect_match:
                target = redirect_match.group(1).strip()
                if target.startswith("/"):
                    target = f"https://www.gov.cn{target}"
                html = _urlopen_bytes(target).decode("utf-8", errors="ignore")

            # 粗粒度抽取：抓取带 href 的标题项
            anchor_pattern = re.compile(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
            for href, txt in anchor_pattern.findall(html):
                title = re.sub(r"<[^>]+>", "", txt).strip()
                if len(title) < 8:
                    continue
                if any(k in title for k in ["首页", "更多", "专题", "视频", "图片"]):
                    continue
                if "+listArrP" in title or "ICP备" in title or "公网安备" in title:
                    continue

                link = href.strip()
                if link.startswith("/"):
                    link = f"http://www.gov.cn{link}"
                elif link.startswith("./"):
                    link = page_url.rsplit("/", 1)[0] + "/" + link[2:]

                # 不同来源做更细过滤
                if source_name.startswith("gov_cn"):
                    if "gov.cn" not in link:
                        continue
                    # gov 页面对新闻正文链接特征过滤
                    if not any(x in link for x in ["/yaowen/", "/zhengce/"]):
                        continue
                if source_name == "sina_finance_roll":
                    if "finance.sina.com.cn" not in link:
                        continue
                    if not any(x in link for x in ["/stock/", "/money/", "/china/"]):
                        continue

                row = {
                    "event_id": _to_event_id(title, source_name, "", link),
                    "title": title,
                    "content": "",
                    "source": source_name,
                    "publish_time": "",
                    "url": link,
                    "crawl_time": crawl_time,
                    "version": "v1",
                }
                rows.append(row)

        except (URLError, HTTPError, TimeoutError) as e:
            print(f"[WARN] 事件抓取失败: {page_url} -> {e}")
            continue

    # 去重
    uniq: Dict[str, Dict[str, str]] = {}
    for r in rows:
        uniq[r["event_id"]] = r

    return list(uniq.values())


def _read_csv_text(url: str, timeout: int = 12) -> str:
    return _urlopen_bytes(url, timeout=timeout).decode("utf-8", errors="ignore")


def _to_float(x: str) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch_market_from_stooq() -> List[Dict[str, str]]:
    """
    使用 stooq 公开日线作为“无 token 可运行”的行情样本。
    注意：正式赛题建议后续切换到 Tushare/JoinQuant 全量数据。
    """
    symbols = {
        "600519.SH": "600519.SS",
        "601318.SH": "601318.SS",
        "000001.SZ": "000001.SZ",
        "300750.SZ": "300750.SZ",
        "002594.SZ": "002594.SZ",
    }

    all_rows: List[Dict[str, str]] = []

    for ts_code, y_symbol in symbols.items():
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{y_symbol}?range=5y&interval=1d"
        try:
            payload = json.loads(_urlopen_bytes(url).decode("utf-8", errors="ignore"))
            chart = payload.get("chart", {})
            result = (chart.get("result") or [None])[0]
            if not result:
                print(f"[WARN] 行情为空: {ts_code}")
                continue

            timestamps = result.get("timestamp") or []
            quote = (((result.get("indicators") or {}).get("quote") or [{}])[0])
            opens = quote.get("open") or []
            highs = quote.get("high") or []
            lows = quote.get("low") or []
            closes = quote.get("close") or []
            vols = quote.get("volume") or []

            if not timestamps:
                print(f"[WARN] 无时间序列: {ts_code}")
                continue

            prev_close: float | None = None

            for i, ts in enumerate(timestamps):
                date = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
                open_ = _to_float(str(opens[i])) if i < len(opens) else None
                high = _to_float(str(highs[i])) if i < len(highs) else None
                low = _to_float(str(lows[i])) if i < len(lows) else None
                close = _to_float(str(closes[i])) if i < len(closes) else None
                vol = _to_float(str(vols[i])) if i < len(vols) else None

                if not date or open_ is None or close is None:
                    continue

                pct = ""
                if prev_close not in (None, 0):
                    pct_val = (close - prev_close) / prev_close * 100
                    pct = f"{pct_val:.6f}"

                all_rows.append(
                    {
                        "ts_code": ts_code,
                        "trade_date": date,
                        "open": f"{open_:.6f}",
                        "close": f"{close:.6f}",
                        "high": f"{(high if high is not None else open_):.6f}",
                        "low": f"{(low if low is not None else open_):.6f}",
                        "vol": f"{(vol if vol is not None else 0):.0f}",
                        "amount": "",
                        "pct_chg": pct,
                    }
                )

                prev_close = close

        except (URLError, HTTPError, TimeoutError, json.JSONDecodeError) as e:
            print(f"[WARN] 行情抓取失败: {ts_code} -> {e}")
            continue

    # 兜底：若日线全部失败，则抓取新浪实时接口，至少生成当日可用样本
    if not all_rows:
        print("[INFO] 启用新浪实时行情兜底...")
        sina_codes = ["sh600519", "sh601318", "sz000001", "sz300750", "sz002594"]
        sina_url = f"http://hq.sinajs.cn/list={','.join(sina_codes)}"
        try:
            text = _urlopen_bytes(sina_url, timeout=12).decode("gbk", errors="ignore")
            for line in text.splitlines():
                m = re.search(r"var hq_str_(\w+)='([^']*)'", line)
                if not m:
                    continue
                code, body = m.group(1), m.group(2)
                parts = body.split(",")
                if len(parts) < 32:
                    continue

                # 新浪字段：name, open, prev_close, current, high, low, ..., vol, amount, date, time
                open_ = _to_float(parts[1])
                prev_close = _to_float(parts[2])
                close = _to_float(parts[3])
                high = _to_float(parts[4])
                low = _to_float(parts[5])
                vol = _to_float(parts[8])
                amount = _to_float(parts[9])
                trade_date = (parts[30] or datetime.now().date().isoformat()).strip()

                if open_ is None or close is None:
                    continue

                pct = ""
                if prev_close not in (None, 0):
                    pct = f"{(close - prev_close) / prev_close * 100:.6f}"

                ts_code = (
                    f"{code[2:]}.SH" if code.startswith("sh") else f"{code[2:]}.SZ"
                )
                all_rows.append(
                    {
                        "ts_code": ts_code,
                        "trade_date": trade_date,
                        "open": f"{open_:.6f}",
                        "close": f"{close:.6f}",
                        "high": f"{(high if high is not None else open_):.6f}",
                        "low": f"{(low if low is not None else open_):.6f}",
                        "vol": f"{(vol if vol is not None else 0):.0f}",
                        "amount": f"{(amount if amount is not None else 0):.2f}",
                        "pct_chg": pct,
                    }
                )
        except (URLError, HTTPError, TimeoutError) as e:
            print(f"[WARN] 新浪兜底失败: {e}")

    return all_rows


def write_csv(path: Path, headers: List[str], rows: List[Dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in headers})


def main() -> None:
    print("[INFO] 开始抓取事件数据（RSS）...")
    events = fetch_events_from_rss()
    write_csv(EVENTS_PATH, EVENT_HEADERS, events)
    print(f"[INFO] events_raw 已写入: {EVENTS_PATH} | rows={len(events)}")

    print("[INFO] 开始抓取行情样本（stooq）...")
    market_rows = fetch_market_from_stooq()
    write_csv(MARKET_PATH, MARKET_HEADERS, market_rows)
    print(f"[INFO] market_daily 已写入: {MARKET_PATH} | rows={len(market_rows)}")

    if len(market_rows) == 0:
        print("[HINT] 当前行情为空，建议配置TUSHARE_TOKEN后切换到Tushare全量抓取。")


if __name__ == "__main__":
    main()
