# data-dig（事件驱动型股市策略数据管线）

本项目用于比赛场景下的基础数据采集与落盘，当前可输出：

- `data/raw/events_raw.csv`：事件原始表
- `data/raw/market_daily.csv`：日线行情表
- `data/raw/trading_status.csv`：交易状态表

## 快速开始

1. 复制示例配置并填写密钥（可选但推荐）
   - 复制 `.env.example` 为 `.env`
   - 填写 `TUSHARE_TOKEN`
2. 激活虚拟环境后运行抓取脚本
   - 入口：`src/01_ingest_events.py`
3. 结果会写入 `data/raw/`

> 未配置 `TUSHARE_TOKEN` 时，脚本会自动降级到公开源，保证可运行。

## 目录说明

- `src/`：抓取与处理代码
- `data/templates/`：提交模板与字段说明
- `data/raw/`：抓取产物（已在 `.gitignore` 中忽略）
- `outputs/`：结果输出目录（已忽略）
- `docs/`：赛题文档、实施方案、接口说明

## 文档入口

- `docs/README.md`：文档导航
- `docs/数据接口密钥获取与配置指南.md`：密钥获取与配置
- `docs/代码技术实施步骤.md`：实施步骤
