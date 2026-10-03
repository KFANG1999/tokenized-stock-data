# 股票代币价格数据集

每 10 分钟通过 GitHub Actions 自动采集一次代币化美股（xStocks、Ondo）在各条链 DEX 上的价格，并与 Yahoo Finance 的真实股价对比。

- 采集脚本：[`collector/collect.py`](collector/collect.py)（只用 Python 标准库）
- 股票列表与过滤参数：[`collector/tokens.json`](collector/tokens.json)
- 数据：`data/年/月/prices_年-月-日.csv`，按 UTC 日期分文件

## 字段说明

| 字段 | 含义 |
|---|---|
| `ts_utc` | 采集时间（UTC） |
| `ticker` | 对应美股代码 |
| `token` / `issuer` | 代币符号 / 发行方 |
| `chain` / `dex` / `quote` | 链 / 交易所 / 报价币 |
| `pair_address` | 交易池地址，可作为面板数据的个体 ID |
| `price_usd` | 代币价格（美元） |
| `ref_price` / `ref_time_utc` | 真实股价及其时间（休市时为最近收盘价） |
| `market_state` | 采集时美股时段：`PRE` / `REGULAR` / `POST` / `CLOSED` |
| `premium_pct` | 溢价 = (代币价格 − 真实股价) ÷ 真实股价 × 100 |
| `liquidity_usd` / `volume_h24` / `txns_h24` | 池子流动性 / 24 小时成交额 / 24 小时成交笔数 |
| `trusted_quote` | 报价币是否为主流币（USDC、SOL 等），1 = 是 |
| `anomaly` | 溢价绝对值是否超过阈值（默认 20%），1 = 异常 |

只记录官方合约地址（来自 CoinGecko）且流动性 ≥ $5,000 的池子。可疑池子不删除，只打标记，分析时自行决定是否剔除。

数据来源：CoinGecko、DexScreener、Yahoo Finance。仅供学习研究，不构成投资建议。
