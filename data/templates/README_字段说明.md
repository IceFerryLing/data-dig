# 数据字段模板说明

## 1) events_raw_template.csv
- `event_id`: 事件唯一ID（建议uuid或来源+时间哈希）
- `title`: 标题
- `content`: 正文/摘要
- `source`: 来源站点
- `publish_time`: 发布时间（ISO格式）
- `url`: 原始链接
- `crawl_time`: 抓取时间
- `version`: 数据版本（如 v1）

## 2) market_daily_template.csv
- `ts_code`: 股票代码（如 600519.SH）
- `trade_date`: 交易日（YYYY-MM-DD）
- `open/close/high/low`: 价格
- `vol`: 成交量
- `amount`: 成交额
- `pct_chg`: 涨跌幅

## 3) trading_status_template.csv
- `is_suspended`: 是否停牌（0/1）
- `is_limit_up`: 是否涨停（0/1）
- `is_limit_down`: 是否跌停（0/1）
- `is_st`: 是否ST（0/1）

## 4) company_profile_template.csv
- `company_name`: 公司名称
- `industry`: 行业
- `concept_tags`: 概念标签（多个用 `;` 分隔）
- `list_date`: 上市日期
- `exchange`: 交易所（SSE/SZSE/BSE）

## 5) event_company_links_template.csv
- `mention_score`: 直接提及分
- `industry_score`: 行业匹配分
- `equity_score`: 股权关系分
- `co_move_score`: 共振关系分
- `link_score`: 综合关联分
- `score_breakdown`: 打分解释（JSON字符串）

## 6) result_submission_template.csv
- `event_name`: 事件名称
- `stock_code`: 标的代码
- `weight`: 资金比例（同周合计为 1.0）

---

## 推荐下一步
1. 先填充 `events_raw_template.csv` 与 `market_daily_template.csv`
2. 再运行清洗与标准化流程
3. 逐步产出 `event_company_links` 与提交文件
