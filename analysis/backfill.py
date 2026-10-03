"""补历史数据：代币每小时价格（GeckoTerminal）+ 美股每小时 / 每天价格（Yahoo Finance）

输出到 data/history/：
  token_hourly.csv  每个主要交易池过去约 6 个月的每小时 K 线（GeckoTerminal 免费接口的上限）
  stock_hourly.csv  每只股票过去 2 年的每小时 K 线（只含常规交易时段）
  stock_daily.csv   每只股票过去 2 年的每日开盘 / 收盘价
  btc_hourly.csv    比特币过去 2 年的每小时 K 线
  dividends.csv     每只股票过去 2 年的除息日和每股股息

用法：python analysis/backfill.py
重新运行会覆盖旧文件；大约需要 10 分钟（免费接口每分钟最多约 30 次请求）。
"""

import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "history"
CONFIG = json.loads((REPO / "collector" / "tokens.json").read_text(encoding="utf-8-sig"))
CACHE = json.loads((REPO / "collector" / "address-cache.json").read_text(encoding="utf-8-sig"))
USER_AGENT = "Mozilla/5.0 (tokenprice-backfill)"

DEEP_USD = 50_000          # 只补深池：浅池历史价格噪音太大
MAX_PAGES = 6              # 每页 1000 根小时 K 线，6 页足够覆盖免费接口的 6 个月
# GeckoTerminal 免费接口只能查最近 180 天，再往前会返回 401；留一天余量
CUTOFF = int(time.time()) - 179 * 86400
GT_NETWORK = {"solana": "solana", "ethereum": "eth", "bsc": "bsc"}
CG_PLATFORM = {"solana": "solana", "ethereum": "ethereum", "bsc": "binance-smart-chain"}


def get_json(url, retries=4):
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 404):   # 401 = 超出免费接口的 180 天范围；404 = 池子不存在
                return None
            if attempt == retries:
                raise
            time.sleep(20 * attempt)   # 429 限流时多等一会儿
        except Exception:
            if attempt == retries:
                raise
            time.sleep(5 * attempt)


def token_address(ticker, issuer, chain):
    """从配置和地址缓存里找出这个代币在这条链上的官方合约地址"""
    stock = next(s for s in CONFIG["stocks"] if s["ticker"] == ticker)
    cg_id = next(t["coingeckoId"] for t in stock["tokens"] if t["issuer"] == issuer)
    return CACHE[cg_id]["platforms"][CG_PLATFORM[chain]]


# ---------- 第 1 步：从实时采集的数据里挑出要补的深池 ----------
live = pd.concat(pd.read_csv(f) for f in sorted((REPO / "data").glob("2*/*/prices_*.csv")))
live = live[(live.trusted_quote == 1) & (live.anomaly == 0)]
pools = (live.groupby(["ticker", "token", "issuer", "chain", "dex", "quote", "pair_address"], as_index=False)
             .liquidity_usd.max())
pools = pools[(pools.liquidity_usd >= DEEP_USD) & pools.chain.isin(GT_NETWORK)]
print(f"要补的深池：{len(pools)} 个，覆盖 {pools.ticker.nunique()} 只股票")

# ---------- 第 2 步：代币每小时 K 线（GeckoTerminal） ----------
rows = []
for i, p in enumerate(pools.itertuples(), 1):
    addr = token_address(p.ticker, p.issuer, p.chain)
    base = (f"https://api.geckoterminal.com/api/v2/networks/{GT_NETWORK[p.chain]}/pools/{p.pair_address}"
            f"/ohlcv/hour?aggregate=1&limit=1000&currency=usd&token={addr}")
    before, n = None, 0
    for _ in range(MAX_PAGES):
        url = base + (f"&before_timestamp={before}" if before else "")
        data = get_json(url)
        time.sleep(2.2)   # 免费接口每分钟约 30 次
        candles = (data or {}).get("data", {}).get("attributes", {}).get("ohlcv_list", [])
        if not candles:
            break
        for ts, o, h, l, c, v in candles:
            rows.append((p.ticker, p.token, p.issuer, p.chain, p.dex, p.quote, p.pair_address, ts, o, h, l, c, v))
        n += len(candles)
        before = min(c[0] for c in candles)
        if len(candles) < 1000 or before <= CUTOFF:
            break
    print(f"  [{i}/{len(pools)}] {p.token:8} {p.chain:9} {p.dex:10} {n:5} 根 K 线")

tok = pd.DataFrame(rows, columns=["ticker", "token", "issuer", "chain", "dex", "quote", "pair_address",
                                  "ts", "open", "high", "low", "close", "volume_usd"])
tok["ts_utc"] = pd.to_datetime(tok.ts, unit="s", utc=True)
tok = tok.drop(columns="ts").drop_duplicates(["pair_address", "ts_utc"]).sort_values(["pair_address", "ts_utc"])

# ---------- 第 3 步：美股每小时 / 每天价格（Yahoo） ----------
def yahoo(ticker, interval, rng):
    for host in ("query1", "query2"):
        data = get_json(f"https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}?interval={interval}&range={rng}")
        if data:
            break
    r = data["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    df = pd.DataFrame({"ticker": ticker, "ts_utc": pd.to_datetime(r["timestamp"], unit="s", utc=True),
                       "open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"],
                       "volume": q["volume"]})
    return df.dropna(subset=["open", "close"])

hourly, daily = [], []
for s in CONFIG["stocks"]:
    hourly.append(yahoo(s["ticker"], "1h", "730d"))
    daily.append(yahoo(s["ticker"], "1d", "2y"))
    time.sleep(1)
def dividends(ticker):
    """除息日和每股股息。除息日开盘价会机械性下跌，回归时要剔除"""
    data = get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=2y&events=div")
    events = data["chart"]["result"][0].get("events", {}).get("dividends", {})
    return pd.DataFrame([{"ticker": ticker, "ex_date": pd.to_datetime(e["date"], unit="s", utc=True)
                          .tz_convert("America/New_York").date(), "amount": e["amount"]} for e in events.values()])

divs = pd.concat([dividends(s["ticker"]) for s in CONFIG["stocks"]], ignore_index=True)
btc_h = yahoo("BTC-USD", "1h", "730d")   # 比特币 24 小时交易，用作周末的共同因素控制变量
btc_h = btc_h[btc_h.ts_utc.dt.minute == 0]   # 去掉最后一根还没走完的 K 线
stock_h = pd.concat(hourly, ignore_index=True)
stock_d = pd.concat(daily, ignore_index=True)
# 日线的时间戳是纽约时间当天开盘时刻，转成交易日期更好用
stock_d["date"] = stock_d.ts_utc.dt.tz_convert("America/New_York").dt.date

# ---------- 保存 ----------
OUT.mkdir(parents=True, exist_ok=True)
tok.to_csv(OUT / "token_hourly.csv", index=False)
stock_h.to_csv(OUT / "stock_hourly.csv", index=False)
stock_d.to_csv(OUT / "stock_daily.csv", index=False)
btc_h.to_csv(OUT / "btc_hourly.csv", index=False)
divs.to_csv(OUT / "dividends.csv", index=False)
print(f"\n代币小时线：{len(tok):,} 行，{tok.ts_utc.min():%Y-%m-%d} 到 {tok.ts_utc.max():%Y-%m-%d}")
print(f"股票小时线：{len(stock_h):,} 行；股票日线：{len(stock_d):,} 行")
print(f"已保存到 {OUT.relative_to(REPO)}")
