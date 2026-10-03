"""股票代币价格采集器（云端版）

每次运行抓取一次快照，追加到 data/年/月/prices_年-月-日.csv（按 UTC 日期分文件）。
流程和电脑上的 tokenprice.ps1 -Collect 一样：
  CoinGecko 官方合约地址 -> DexScreener 链上交易池 -> Yahoo Finance 真实股价 -> 计算溢价
只用 Python 标准库，不需要安装任何第三方包。
"""

import csv
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
CONFIG = json.loads((ROOT / "tokens.json").read_text(encoding="utf-8-sig"))
CACHE_FILE = ROOT / "address-cache.json"
USER_AGENT = "Mozilla/5.0 (tokenprice-collector)"

# CoinGecko 和 DexScreener 对同一条链的叫法不同，这里做个翻译
CHAIN_MAP = {
    "ethereum": "ethereum",
    "solana": "solana",
    "binance-smart-chain": "bsc",
    "arbitrum-one": "arbitrum",
    "base": "base",
    "polygon-pos": "polygon",
    "mantle": "mantle",
    "hyperevm": "hyperevm",
    "the-open-network": "ton",
}

COLUMNS = [
    "ts_utc", "ticker", "token", "issuer", "chain", "dex", "quote", "pair_address",
    "price_usd", "ref_price", "ref_time_utc", "market_state", "premium_pct",
    "liquidity_usd", "volume_h24", "txns_h24", "trusted_quote", "anomaly",
]

NOW = datetime.now(timezone.utc)


def get_json(url, retries=3, wait=5):
    """带重试的 GET 请求，返回解析后的 JSON"""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            if attempt == retries:
                raise
            time.sleep(wait * attempt)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- 第 1 步：官方合约地址（CoinGecko，结果缓存在 address-cache.json） ----------
cache = json.loads(CACHE_FILE.read_text(encoding="utf-8-sig")) if CACHE_FILE.exists() else {}
cache_changed = False


def official_addresses(coingecko_id):
    global cache_changed
    if coingecko_id not in cache:
        print(f"  从 CoinGecko 获取 {coingecko_id} 的官方地址...")
        info = get_json(
            f"https://api.coingecko.com/api/v3/coins/{coingecko_id}"
            "?localization=false&tickers=false&market_data=false&community_data=false&developer_data=false",
            retries=4, wait=30,  # 免费接口有频率限制，被限流时多等一会儿
        )
        cache[coingecko_id] = {"symbol": info["symbol"].upper(), "platforms": info["platforms"]}
        cache_changed = True
        time.sleep(3)
    return cache[coingecko_id]["platforms"]


# ---------- 第 2 步：真实美股价格（Yahoo Finance） ----------
def stock_price(ticker):
    last_error = None
    for host in ("query1", "query2"):   # 一个域名被限制时换另一个
        try:
            data = get_json(f"https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d")
            break
        except Exception as e:
            last_error = e
    else:
        raise last_error
    meta = data["chart"]["result"][0]["meta"]
    # 判断现在美股处于哪个时段：盘前 PRE / 盘中 REGULAR / 盘后 POST / 休市 CLOSED
    now = NOW.timestamp()
    period = meta["currentTradingPeriod"]
    state = "CLOSED"
    for name, key in (("REGULAR", "regular"), ("PRE", "pre"), ("POST", "post")):
        if period[key]["start"] <= now < period[key]["end"]:
            state = name
            break
    return {
        "price": float(meta["regularMarketPrice"]),
        "time_utc": iso(datetime.fromtimestamp(meta["regularMarketTime"], timezone.utc)),
        "state": state,
    }


# ---------- 第 3 步：链上交易池（DexScreener） ----------
def dex_pairs(chain, address):
    pairs = get_json(f"https://api.dexscreener.com/token-pairs/v1/{chain}/{address}") or []
    # 只保留"我们的代币是基础币"的交易对；EVM 地址大小写不敏感
    return [p for p in pairs if p["baseToken"]["address"].lower() == address.lower()]


# ---------- 主流程 ----------
rows = []
trusted_quotes = {q.upper() for q in CONFIG["trustedQuotes"]}

for stock in CONFIG["stocks"]:
    ticker = stock["ticker"]
    try:
        ref = stock_price(ticker)
    except Exception as e:
        print(f"[跳过] {ticker} 股价查询失败: {e}")
        continue
    print(f"{ticker}: ${ref['price']:.2f}  {ref['state']}")

    for token in stock["tokens"]:
        try:
            platforms = official_addresses(token["coingeckoId"])
        except Exception as e:
            print(f"  [跳过] {token['coingeckoId']} 地址查询失败: {e}")
            continue

        for cg_chain, address in platforms.items():
            chain = CHAIN_MAP.get(cg_chain)
            if not chain or not address:
                continue   # 不认识的链先跳过
            try:
                pairs = dex_pairs(chain, address)
            except Exception as e:
                print(f"  [跳过] {chain} 查询失败: {e}")
                continue

            for pair in pairs:
                liq = float((pair.get("liquidity") or {}).get("usd") or 0)
                if liq < CONFIG["minLiquidityUsd"]:
                    continue   # 流动性太低的池子价格不可信
                price = float(pair["priceUsd"])
                premium = (price - ref["price"]) / ref["price"] * 100
                txns = (pair.get("txns") or {}).get("h24") or {}
                rows.append({
                    "ts_utc": iso(NOW),
                    "ticker": ticker,
                    "token": pair["baseToken"]["symbol"],
                    "issuer": token["issuer"],
                    "chain": chain,
                    "dex": pair["dexId"],
                    "quote": pair["quoteToken"]["symbol"],
                    "pair_address": pair["pairAddress"],
                    "price_usd": price,
                    "ref_price": ref["price"],
                    "ref_time_utc": ref["time_utc"],
                    "market_state": ref["state"],
                    "premium_pct": round(premium, 4),
                    "liquidity_usd": round(liq, 2),
                    "volume_h24": float((pair.get("volume") or {}).get("h24") or 0),
                    "txns_h24": int(txns.get("buys", 0)) + int(txns.get("sells", 0)),
                    # 报价币不是主流币的池子容易被操纵；溢价离谱的池子视为异常。只打标记，不删除
                    "trusted_quote": int(pair["quoteToken"]["symbol"].upper() in trusted_quotes),
                    "anomaly": int(abs(premium) > CONFIG["maxPremiumPercent"]),
                })

if cache_changed:
    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")

if not rows:
    raise SystemExit("没有拿到任何数据")

out = REPO / "data" / NOW.strftime("%Y") / NOW.strftime("%m") / f"prices_{NOW:%Y-%m-%d}.csv"
out.parent.mkdir(parents=True, exist_ok=True)
is_new = not out.exists()
with out.open("a", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=COLUMNS)
    if is_new:
        writer.writeheader()
    writer.writerows(rows)
print(f"采集完成：{len(rows)} 行 -> {out.relative_to(REPO)}")
