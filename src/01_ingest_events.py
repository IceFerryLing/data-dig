from __future__ import annotations
from bs4 import BeautifulSoup
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
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

RAW_DIR = Path("data") / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

EVENTS_PATH = RAW_DIR / "events_raw.csv"
MARKET_PATH = RAW_DIR / "market_daily.csv"
TRADING_STATUS_PATH = RAW_DIR / "trading_status.csv"


def _load_env_file(env_path: Path) -> None:
    """轻量读取 .env（不依赖额外库）。"""
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        key = k.strip()
        val = v.strip()
        if not key:
            continue
        # 已在系统环境变量中设置的值优先，不强行覆盖
        os.environ.setdefault(key, val)

SEED_TS_CODES: List[str] = [
    "600519.SH", "601318.SH", "000001.SZ", "300750.SZ", "002594.SZ", "601012.SH", "300059.SZ", "600036.SH",
    "601166.SH", "000333.SZ", "000651.SZ", "600276.SH", "600887.SH", "603259.SH", "600900.SH", "601398.SH",
    "601288.SH", "601988.SH", "601939.SH", "601857.SH", "601088.SH", "600030.SH", "601888.SH", "000858.SZ",
    "002415.SZ", "300124.SZ", "300308.SZ", "300760.SZ", "688981.SH", "688111.SH", "603986.SH", "600031.SH",
    "600089.SH", "000002.SZ", "600048.SH", "600436.SH", "600745.SH", "601899.SH", "601225.SH", "600438.SH",
]


EVENT_SOURCE_SPECS: List[Dict[str, object]] = [
    # 政策类/权威新闻
    {"source": "gov_cn_yaowen", "url": "https://www.gov.cn/yaowen/liebiao/", "allow_domains": ["gov.cn"], "allow_paths": ["/yaowen/", "/lianbo/"]},
    {"source": "gov_cn_zhengce", "url": "https://www.gov.cn/zhengce/zuixin/", "allow_domains": ["gov.cn"], "allow_paths": ["/zhengce/", "/content/"]},
    {"source": "ndrc_xwfb", "url": "https://www.ndrc.gov.cn/xwdt/xwfb/", "allow_domains": ["ndrc.gov.cn"], "allow_paths": ["/xwdt/xwfb/", "/xxgk/"]},
    {"source": "csrc_news", "url": "https://www.csrc.gov.cn/csrc/c100028/common_xq_list.shtml", "allow_domains": ["csrc.gov.cn"], "allow_paths": ["/csrc/"]},
    # 公司公告/行为
    {"source": "cninfo_notice", "url": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search", "allow_domains": ["cninfo.com.cn"], "allow_paths": ["/new/disclosure/", "disclosure/list/search"]},
    {"source": "sse_announcement", "url": "https://www.sse.com.cn/disclosure/listedinfo/announcement/", "allow_domains": ["sse.com.cn"], "allow_paths": ["/disclosure/listedinfo/", "/disclosure/announcement/"]},
    {"source": "szse_disclosure", "url": "https://www.szse.cn/disclosure/listed/notice/index.html", "allow_domains": ["szse.cn"], "allow_paths": ["/disclosure/listed/", "/disclosure/memo/"]},
    # 行业/技术/财经新闻
    {"source": "eastmoney_industry", "url": "https://finance.eastmoney.com/a/cywjh.html", "allow_domains": ["finance.eastmoney.com"], "allow_paths": ["/a/", "/news/"]},
    {"source": "kr_newsflashes", "url": "https://36kr.com/newsflashes", "allow_domains": ["36kr.com"], "allow_paths": ["/newsflashes/", "/information/"]},
    {"source": "sina_finance_roll", "url": "https://finance.sina.com.cn/roll/", "allow_domains": ["finance.sina.com.cn"], "allow_paths": ["/stock/", "/money/", "/china/", "/roll/"]},
    # 新增主流新闻/财经/行业站点（参考AllNewsSpider/News-Detector）
    {"source": "sohu_news", "url": "https://www.sohu.com/news/", "allow_domains": ["sohu.com"], "allow_paths": ["/news/", "/a/"]},
    {"source": "ifeng_news", "url": "https://news.ifeng.com/", "allow_domains": ["ifeng.com"], "allow_paths": ["/news/", "/a/", "/c/"]},
    {"source": "163_news", "url": "https://news.163.com/", "allow_domains": ["163.com"], "allow_paths": ["/news/", "/special/"]},
    {"source": "thepaper_news", "url": "https://www.thepaper.cn/channel_25951", "allow_domains": ["thepaper.cn"], "allow_paths": ["/newsDetail_forward/", "/channel_25951"]},
    {"source": "caixin_scroll", "url": "https://www.caixin.com/search/newscroll", "allow_domains": ["caixin.com"], "allow_paths": ["/20", "/search/newscroll", "/finance/", "/china/", "/international/"]},
    {"source": "yicai_news", "url": "https://www.yicai.com/news/", "allow_domains": ["yicai.com"], "allow_paths": ["/news/", "/brief/", "/live/"]},
    {"source": "yicai_brief", "url": "https://www.yicai.com/brief/", "allow_domains": ["yicai.com"], "allow_paths": ["/brief/", "/news/"]},
    {"source": "jiemian_news", "url": "https://www.jiemian.com/lists/3.html", "allow_domains": ["jiemian.com"], "allow_paths": ["/article/", "/lists/"]},
    {"source": "stcn_news", "url": "https://www.stcn.com/news/", "allow_domains": ["stcn.com"], "allow_paths": ["/news/", "/article/"]},
    {"source": "cls_news", "url": "https://www.cls.cn/v2/roll/", "allow_domains": ["cls.cn"], "allow_paths": ["/v2/roll/", "/detail/"]},
    {"source": "yicai_live", "url": "https://www.yicai.com/live/", "allow_domains": ["yicai.com"], "allow_paths": ["/live/"]},
    {"source": "cctv_news", "url": "https://news.cctv.com/", "allow_domains": ["cctv.com"], "allow_paths": ["/news/", "/2024/", "/2025/", "/2026/"]},
    {"source": "xinhuanet_news", "url": "http://www.xinhuanet.com/fortune/", "allow_domains": ["xinhuanet.com"], "allow_paths": ["/fortune/", "/politics/", "/local/"]},
]


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

TRADING_STATUS_HEADERS = [
    "ts_code",
    "trade_date",
    "is_suspended",
    "is_limit_up",
    "is_limit_down",
    "is_st",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe_text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _to_event_id(title: str, source: str, publish_time: str, url: str) -> str:
    key = f"{title}|{source}|{publish_time}|{url}".encode("utf-8", errors="ignore")
    return hashlib.md5(key).hexdigest()


def _infer_publish_time_from_url(url: str) -> str:
    """从常见新闻URL推断发布日期，返回YYYY-MM-DD。"""
    s = (url or "").strip()
    patterns = [
        r"(20\d{2})-(\d{2})-(\d{2})",  # 2026-04-12
        r"/((20\d{2})(\d{2})(\d{2}))/",  # /202604/
        r"/a/(20\d{2})(\d{2})(\d{2})",  # /a/20260412...
        r"/t(20\d{2})(\d{2})(\d{2})_",  # /t20260407_
    ]
    for p in patterns:
        m = re.search(p, s)
        if not m:
            continue
        g = m.groups()
        if len(g) == 3:
            y, mo, d = g
        elif len(g) >= 4:
            y, mo, d = g[1], g[2], g[3]
        else:
            continue
        return f"{y}-{mo}-{d}"
    return ""


def _urlopen_bytes(url: str, timeout: int = 12) -> bytes:
    # 某些网络环境（学校/公司代理）会导致证书链异常，这里提供兼容模式。
    context = ssl.create_default_context()
    if os.getenv("IGNORE_SSL_VERIFY", "1") == "1":
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/json,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "close",
    }
    # 针对 yicai 站点增加 Referer 伪装
    if "yicai.com" in url:
        headers["Referer"] = "https://www.yicai.com/"

    req = Request(url, headers=headers)

    retries = int(os.getenv("HTTP_RETRIES", "4"))  # 默认重试次数提升
    last_err: Exception | None = None
    for _ in range(max(1, retries + 1)):
        try:
            with urlopen(req, timeout=timeout, context=context) as resp:  # nosec - data collection for competition
                return resp.read()
        except HTTPError:
            # 4xx/5xx通常非瞬时问题，直接抛出
            raise
        except (URLError, TimeoutError, ssl.SSLError) as e:
            last_err = e
            continue

    if last_err is not None:
        raise last_err
    raise URLError(f"request failed with unknown error: {url}")


def _fetch_xml(url: str, timeout: int = 12) -> bytes:
    return _urlopen_bytes(url, timeout=timeout)


def _contains_any(text: str, candidates: List[str]) -> bool:
    t = (text or "").lower()
    return any(c.lower() in t for c in candidates)


def _source_page_urls(source_name: str, base_url: str, max_pages: int) -> List[str]:
    urls: List[str] = [base_url]
    if max_pages <= 1:
        return urls

    if source_name == "ndrc_xwfb":
        urls.extend(urljoin(base_url, f"index_{i}.html") for i in range(1, max_pages))
    elif source_name == "eastmoney_industry":
        urls.extend(urljoin(base_url, f"cywjh_{i}.html") for i in range(2, max_pages + 1))
    elif source_name == "csrc_news":
        # 该站分页参数较隐式，常见静态页后缀模式
        urls.extend(urljoin(base_url, f"common_xq_list_{i}.shtml") for i in range(1, max_pages))
    # sse/szse栏目分页路径不稳定（常见并非index_i.html），这里先抓首页避免大量404。
    elif source_name == "kr_newsflashes":
        base = base_url if base_url.endswith("/") else f"{base_url}/"
        urls.extend(urljoin(base, f"catalog/{i}") for i in range(1, max_pages))

    return list(dict.fromkeys(urls))


def _normalize_link(href: str, base_url: str) -> str:
    link = (href or "").strip()
    if not link:
        return ""
    if link.startswith("javascript:") or link.startswith("#"):
        return ""
    return urljoin(base_url, link)


def _extract_events_from_html(
    *,
    html: str,
    source_name: str,
    page_url: str,
    crawl_time: str,
    allow_domains: List[str],
    allow_paths: List[str],
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    # 优先用BeautifulSoup解析新闻链接和标题
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", href=True)
    for a in anchors:
        href = a.get("href", "").strip()
        title = a.get_text(strip=True)
        if len(title) < 8:
            continue
        if any(x in title for x in ["listArrP", "'+", "+'", "${"]):
            continue
        if any(k in title for k in ["首页", "更多", "专题", "视频", "图片", "登录", "注册"]):
            continue
        if any(k in title for k in ["ICP备", "公网安备", "版权所有", "联系我们"]):
            continue
        link = _normalize_link(href, page_url)
        if not link:
            continue
        if any(x in link for x in ["listArrP", "'+", "+'", "${"]):
            continue
        if allow_domains and not _contains_any(link, allow_domains):
            continue
        if allow_paths and not _contains_any(link, allow_paths):
            continue
        # 尝试提取正文内容（如有）
        content = ""
        try:
            # 若a标签有data-content属性或父节点有摘要，优先取
            if a.has_attr("data-content"):
                content = a["data-content"]
            elif a.parent and a.parent.name in ["li", "div"]:
                content = a.parent.get_text(strip=True)
        except Exception:
            pass
        rows.append({
            "event_id": _to_event_id(title, source_name, _infer_publish_time_from_url(link), link),
            "title": title,
            "content": content,
            "source": source_name,
            "publish_time": _infer_publish_time_from_url(link),
            "url": link,
            "crawl_time": crawl_time,
            "version": "v1",
        })
    return rows


def fetch_events_from_rss() -> List[Dict[str, str]]:
    """抓取公开政府新闻列表页，兼容 RSS 不稳定场景。"""
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

    max_pages = int(os.getenv("EVENT_MAX_PAGES", "6"))

    for spec in EVENT_SOURCE_SPECS:
        source_name = str(spec["source"])
        base_url = str(spec["url"])
        allow_domains = [str(x) for x in (spec.get("allow_domains") or [])]
        allow_paths = [str(x) for x in (spec.get("allow_paths") or [])]
        page_urls = _source_page_urls(source_name, base_url, max_pages=max_pages)

        for page_url in page_urls:
            try:
                html = _urlopen_bytes(page_url).decode("utf-8", errors="ignore")

                # 处理 gov 页面常见 JS 跳转
                redirect_match = re.search(r'window\.location\.href\s*=\s*"([^"]+)"', html)
                if redirect_match:
                    target = redirect_match.group(1).strip()
                    if target and not re.match(r"^https?://", target, re.I):
                        target = urljoin(page_url, target)
                    html = _urlopen_bytes(target).decode("utf-8", errors="ignore")

                rows.extend(
                    _extract_events_from_html(
                        html=html,
                        source_name=source_name,
                        page_url=page_url,
                        crawl_time=crawl_time,
                        allow_domains=allow_domains,
                        allow_paths=allow_paths,
                    )
                )

            except (URLError, HTTPError, TimeoutError, ValueError) as e:
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


def _format_tushare_date(yyyymmdd: str) -> str:
    if len(yyyymmdd) == 8 and yyyymmdd.isdigit():
        return f"{yyyymmdd[0:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
    return yyyymmdd


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
    raw = urlopen(req, timeout=30).read()  # nosec - competition data collection
    return json.loads(raw.decode("utf-8", errors="ignore"))


def fetch_market_from_tushare(token: str) -> Tuple[List[Dict[str, str]], Dict[str, bool]]:
    """有token时优先使用Tushare，抓取更接近比赛需求的结构化行情。"""
    today = datetime.now().date()
    start_days = int(os.getenv("MARKET_LOOKBACK_DAYS", "900"))
    start_day = (today.fromordinal(today.toordinal() - start_days)).strftime("%Y%m%d")
    end_day = today.strftime("%Y%m%d")
    max_trade_days = int(os.getenv("MARKET_MAX_TRADE_DAYS", "500"))

    st_map: Dict[str, bool] = {}
    rows: List[Dict[str, str]] = []

    # 股票基础信息（用于ST标记）
    basic = _tushare_call(
        token,
        "stock_basic",
        {"exchange": "", "list_status": "L"},
        "ts_code,name",
    )
    if (basic or {}).get("code") not in (0, None):
        raise RuntimeError(f"Tushare stock_basic调用失败: code={(basic or {}).get('code')} msg={(basic or {}).get('msg')}")
    basic_items = (((basic or {}).get("data") or {}).get("items") or [])
    for ts_code, name in basic_items:
        st_map[str(ts_code)] = ("ST" in str(name).upper())

    # 交易日历
    cal = _tushare_call(
        token,
        "trade_cal",
        {"exchange": "SSE", "start_date": start_day, "end_date": end_day, "is_open": "1"},
        "cal_date",
    )
    if (cal or {}).get("code") not in (0, None):
        raise RuntimeError(f"Tushare trade_cal调用失败: code={(cal or {}).get('code')} msg={(cal or {}).get('msg')}")
    dates = [str(x[0]) for x in ((((cal or {}).get("data") or {}).get("items") or [])) if x and x[0]]
    if not dates:
        return [], st_map
    dates = dates[-max_trade_days:]

    # 按交易日抓取全市场日线
    for d in dates:
        try:
            daily = _tushare_call(
                token,
                "daily",
                {"trade_date": d},
                "ts_code,trade_date,open,high,low,close,vol,amount,pct_chg",
            )
            if (daily or {}).get("code") not in (0, None):
                print(f"[WARN] Tushare daily调用失败: {d} -> code={(daily or {}).get('code')} msg={(daily or {}).get('msg')}")
                continue
            items = (((daily or {}).get("data") or {}).get("items") or [])
            for it in items:
                if len(it) < 9:
                    continue
                ts_code, trade_date, open_, high, low, close, vol, amount, pct = it
                rows.append(
                    {
                        "ts_code": str(ts_code),
                        "trade_date": _format_tushare_date(str(trade_date)),
                        "open": f"{float(open_):.6f}" if open_ is not None else "",
                        "close": f"{float(close):.6f}" if close is not None else "",
                        "high": f"{float(high):.6f}" if high is not None else "",
                        "low": f"{float(low):.6f}" if low is not None else "",
                        "vol": f"{float(vol):.0f}" if vol is not None else "",
                        "amount": f"{float(amount):.2f}" if amount is not None else "",
                        "pct_chg": f"{float(pct):.6f}" if pct is not None else "",
                    }
                )
        except (URLError, HTTPError, TimeoutError, ValueError, json.JSONDecodeError) as e:
            print(f"[WARN] Tushare日线抓取失败: {d} -> {e}")

    return rows, st_map


def fetch_market_from_stooq() -> List[Dict[str, str]]:
    """
    使用 stooq 公开日线作为“无 token 可运行”的行情样本。
    注意：正式赛题建议后续切换到 Tushare/JoinQuant 全量数据。
    """
    return fetch_market_from_public(SEED_TS_CODES)


def _normalize_ts_code(raw: str) -> str:
    s = (raw or "").strip().upper()
    if not s:
        return ""
    if re.fullmatch(r"\d{6}\.(SH|SZ)", s):
        return s
    if re.fullmatch(r"\d{6}", s):
        if s.startswith(("6", "9")):
            return f"{s}.SH"
        if s.startswith(("0", "3", "2")):
            return f"{s}.SZ"
    return ""


def _to_yahoo_symbol(ts_code: str) -> str:
    code = _normalize_ts_code(ts_code)
    if not code:
        return ""
    n, ex = code.split(".")
    return f"{n}.SS" if ex == "SH" else f"{n}.SZ"


def extract_ts_codes_from_events(events: List[Dict[str, str]]) -> List[str]:
    found: List[str] = []
    # A股常见代码段：00/30/60/68开头；避免把202604这类日期片段误识别为代码
    code_pat = re.compile(r"(?<!\d)((?:00|30|60|68)\d{4})(?!\d)")
    for e in events:
        for txt in [e.get("title", ""), e.get("content", ""), e.get("url", "")]:
            for c in code_pat.findall(txt or ""):
                cc = _normalize_ts_code(c)
                if cc:
                    found.append(cc)
    return list(dict.fromkeys(found))


def build_market_universe(events: List[Dict[str, str]]) -> List[str]:
    extra = extract_ts_codes_from_events(events)
    merged = list(dict.fromkeys(extra + SEED_TS_CODES))
    max_symbols = int(os.getenv("MARKET_MAX_SYMBOLS", "30"))
    return merged[:max_symbols]


def fetch_market_from_public(ts_codes: List[str]) -> List[Dict[str, str]]:
    """使用Yahoo公开接口批量抓取A股日线（无token可用）。"""
    symbols = {
        code: _to_yahoo_symbol(code)
        for code in ts_codes
        if _to_yahoo_symbol(code)
    }

    all_rows: List[Dict[str, str]] = []

    public_range = os.getenv("MARKET_PUBLIC_RANGE", "5y")
    for ts_code, y_symbol in symbols.items():
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{y_symbol}?range={public_range}&interval=1d"
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

        except KeyboardInterrupt:
            print(f"[WARN] 行情抓取中断: {ts_code} -> 跳过该标的继续")
            continue
        except (URLError, HTTPError, TimeoutError, json.JSONDecodeError, ssl.SSLError, ValueError) as e:
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


def build_trading_status_rows(
    market_rows: List[Dict[str, str]],
    st_map: Dict[str, bool] | None = None,
) -> List[Dict[str, str]]:
    """从行情推导交易状态；停牌先给默认0，后续可接交易所停复牌接口增强。"""
    st_map = st_map or {}
    out: List[Dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for r in market_rows:
        ts_code = (r.get("ts_code") or "").strip()
        trade_date = (r.get("trade_date") or "").strip()
        if not ts_code or not trade_date:
            continue
        key = (ts_code, trade_date)
        if key in seen:
            continue
        seen.add(key)

        pct = _to_float(r.get("pct_chg", ""))
        is_limit_up = 1 if pct is not None and pct >= 9.8 else 0
        is_limit_down = 1 if pct is not None and pct <= -9.8 else 0

        out.append(
            {
                "ts_code": ts_code,
                "trade_date": trade_date,
                "is_suspended": "0",
                "is_limit_up": str(is_limit_up),
                "is_limit_down": str(is_limit_down),
                "is_st": "1" if st_map.get(ts_code, False) else "0",
            }
        )

    return out


def write_csv(path: Path, headers: List[str], rows: List[Dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in headers})


def _read_existing_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _merge_by_keys(
    existing_rows: List[Dict[str, str]],
    new_rows: List[Dict[str, str]],
    key_fields: List[str],
    headers: List[str],
) -> List[Dict[str, str]]:
    merged: Dict[tuple, Dict[str, str]] = {}

    def _key_of(row: Dict[str, str]) -> tuple:
        return tuple((row.get(k, "") or "").strip() for k in key_fields)

    for r in existing_rows:
        k = _key_of(r)
        if any(k):
            merged[k] = {h: r.get(h, "") for h in headers}

    # 新抓取优先覆盖同键旧记录（例如字段更完整时）
    for r in new_rows:
        k = _key_of(r)
        if any(k):
            merged[k] = {h: r.get(h, "") for h in headers}

    return list(merged.values())


def main() -> None:
    _load_env_file(Path(".env"))
    merge_with_existing = os.getenv("MERGE_WITH_EXISTING", "1").strip() == "1"

    print("[INFO] 开始抓取事件数据（RSS）...")
    events = fetch_events_from_rss()
    if merge_with_existing:
        events_old = _read_existing_csv(EVENTS_PATH)
        events = _merge_by_keys(events_old, events, ["event_id"], EVENT_HEADERS)
    write_csv(EVENTS_PATH, EVENT_HEADERS, events)
    print(f"[INFO] events_raw 已写入: {EVENTS_PATH} | rows={len(events)}")

    token = os.getenv("TUSHARE_TOKEN", "").strip()
    market_universe = build_market_universe(events)
    st_map: Dict[str, bool] = {}

    if token and token != "your_tushare_token_here":
        print("[INFO] 检测到TUSHARE_TOKEN，优先抓取Tushare日线...")
        try:
            market_rows, st_map = fetch_market_from_tushare(token)
        except (URLError, HTTPError, TimeoutError, ValueError, json.JSONDecodeError, RuntimeError) as e:
            print(f"[WARN] Tushare抓取失败，降级到公开源: {e}")
            market_rows = fetch_market_from_public(market_universe)
    else:
        print(f"[INFO] 未配置TUSHARE_TOKEN，使用公开源批量行情样本（symbols={len(market_universe)}）...")
        market_rows = fetch_market_from_public(market_universe)

    if merge_with_existing:
        market_old = _read_existing_csv(MARKET_PATH)
        market_rows = _merge_by_keys(market_old, market_rows, ["ts_code", "trade_date"], MARKET_HEADERS)
    write_csv(MARKET_PATH, MARKET_HEADERS, market_rows)
    print(f"[INFO] market_daily 已写入: {MARKET_PATH} | rows={len(market_rows)}")

    trading_rows = build_trading_status_rows(market_rows, st_map=st_map)
    if merge_with_existing:
        trading_old = _read_existing_csv(TRADING_STATUS_PATH)
        trading_rows = _merge_by_keys(trading_old, trading_rows, ["ts_code", "trade_date"], TRADING_STATUS_HEADERS)
    write_csv(TRADING_STATUS_PATH, TRADING_STATUS_HEADERS, trading_rows)
    print(f"[INFO] trading_status 已写入: {TRADING_STATUS_PATH} | rows={len(trading_rows)}")

    if len(market_rows) == 0:
        print("[HINT] 当前行情为空，建议配置TUSHARE_TOKEN后切换到Tushare全量抓取。")


if __name__ == "__main__":
    main()
