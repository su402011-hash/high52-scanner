# -*- coding: utf-8 -*-
"""
52週高値スキャナー v4 (テクニカル指標付き)
==========================================
v3からの変更点:
- value_scan / growth_scan の出力に以下のテクニカル列を追加
    MA25乖離% : 25日移動平均からの乖離率 (+が上方乖離=過熱気味)
    MA75乖離% : 75日移動平均からの乖離率 (中期トレンドの位置)
    RSI14     : 14日RSI (70以上=買われすぎ, 30以下=売られすぎ)
    出来高倍率 : 直近5日平均出来高 ÷ 過去60日平均 (1超=資金流入)

コマンド:
  python high52_scanner.py scan         # 52週高値更新銘柄 (日米)
  python high52_scanner.py rank         # 高値更新回数ランキング
  python high52_scanner.py value_scan   # 中小型割安バリュー株
  python high52_scanner.py growth_scan  # テンバガー候補 (小型高成長)
"""

import io
import os
import sys
import time
from datetime import datetime, date

import pandas as pd
import requests
import yfinance as yf

OUTPUT_DIR = "outputs"
UA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0 Safari/537.36")
}
JPX_URL = ("https://www.jpx.co.jp/markets/statistics-equities/misc/"
           "tvdivq0000001vg2-att/data_j.xls")

# ---- スクリーニング条件 (自由に調整OK) ----
VALUE_MCAP_MIN = 100e8
VALUE_MCAP_MAX = 3000e8
VALUE_PBR_MAX = 1.0
VALUE_PER_MAX = 12.0
VALUE_YIELD_MIN = 0.03
VALUE_POS_MAX = 0.30

GROWTH_MCAP_MAX = 500e8
GROWTH_REV_MIN = 0.20
GROWTH_ROE_MIN = 0.08
GROWTH_POS_MIN = 0.60

MAX_FUND_CALLS = 350

# ---------------------------------------------------------------
# 銘柄ユニバース
# ---------------------------------------------------------------

JP_DEFAULT_TICKERS = {
    "7203.T": "トヨタ自動車", "6758.T": "ソニーG", "8306.T": "三菱UFJ",
    "9984.T": "ソフトバンクG", "6861.T": "キーエンス", "8035.T": "東京エレクトロン",
    "6501.T": "日立製作所", "7974.T": "任天堂", "9983.T": "ファーストリテイリング",
    "4063.T": "信越化学", "8058.T": "三菱商事", "8001.T": "伊藤忠商事",
    "8031.T": "三井物産", "7011.T": "三菱重工業", "8316.T": "三井住友FG",
    "8411.T": "みずほFG", "9432.T": "NTT", "9433.T": "KDDI",
    "6098.T": "リクルートHD", "6902.T": "デンソー", "4502.T": "武田薬品",
    "4568.T": "第一三共", "6367.T": "ダイキン工業", "6981.T": "村田製作所",
    "7741.T": "HOYA", "6273.T": "SMC", "9101.T": "日本郵船",
    "2914.T": "JT", "8766.T": "東京海上HD", "8591.T": "オリックス",
    "6503.T": "三菱電機", "6702.T": "富士通", "6752.T": "パナソニックHD",
    "7267.T": "ホンダ", "7751.T": "キヤノン", "4661.T": "OLC",
    "3382.T": "セブン&アイ", "9843.T": "ニトリHD", "4519.T": "中外製薬",
    "4523.T": "エーザイ", "6146.T": "ディスコ", "6857.T": "アドバンテスト",
    "6920.T": "レーザーテック", "7735.T": "SCREEN", "5401.T": "日本製鉄",
    "9022.T": "JR東海", "9020.T": "JR東日本",
}


def get_us_tickers() -> dict:
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    try:
        resp = requests.get(url, headers=UA_HEADERS, timeout=30)
        resp.raise_for_status()
        df = pd.read_html(io.StringIO(resp.text))[0]
        tickers = {str(s).replace(".", "-"): str(n)
                   for s, n in zip(df["Symbol"], df["Security"])}
        print(f"[INFO] S&P500リスト取得成功: {len(tickers)}銘柄")
        return tickers
    except Exception as e:
        print(f"[WARN] S&P500リスト取得に失敗: {e}")
        return {}


def get_jp_tickers() -> dict:
    path = "tickers_jp.txt"
    if os.path.exists(path):
        result = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(",")
                result[parts[0].strip()] = (parts[1].strip()
                                            if len(parts) > 1 else parts[0])
        print(f"[INFO] tickers_jp.txt から {len(result)}銘柄を読込")
        return result
    return JP_DEFAULT_TICKERS


def get_jpx_universe(markets) -> dict:
    try:
        resp = requests.get(JPX_URL, headers=UA_HEADERS, timeout=60)
        resp.raise_for_status()
        df = pd.read_excel(io.BytesIO(resp.content))
        seg = df["市場・商品区分"].astype(str)
        df = df[seg.str.contains("内国株式")]
        df = df[df["市場・商品区分"].astype(str).apply(
            lambda s: any(m in s for m in markets))]
        result = {}
        for code, name in zip(df["コード"], df["銘柄名"]):
            code = str(code).strip()
            if code.isdigit():
                result[f"{code.zfill(4)}.T"] = str(name)
        print(f"[INFO] JPXリスト取得: {'/'.join(markets)} {len(result)}銘柄")
        return result
    except Exception as e:
        print(f"[WARN] JPXリスト取得失敗: {e}")
        return {}


# ---------------------------------------------------------------
# 価格・テクニカル・財務データ取得
# ---------------------------------------------------------------

def calc_rsi(close: pd.Series, period: int = 14):
    """14日RSI (単純平均方式)。"""
    diff = close.diff()
    gain = diff.clip(lower=0).rolling(period).mean()
    loss = (-diff.clip(upper=0)).rolling(period).mean()
    last_gain = gain.iloc[-1]
    last_loss = loss.iloc[-1]
    if pd.isna(last_gain) or pd.isna(last_loss):
        return None
    if last_loss == 0:
        return 100.0
    rs = last_gain / last_loss
    return float(100 - 100 / (1 + rs))


def technicals(df: pd.DataFrame) -> dict:
    """終値・出来高系列からテクニカル指標を計算。"""
    close = df["Close"].dropna()
    vol = df["Volume"].dropna() if "Volume" in df else pd.Series(dtype=float)
    out = {"ma25_dev": None, "ma75_dev": None, "rsi14": None, "vol_ratio": None}
    c = float(close.iloc[-1])
    if len(close) >= 25:
        ma25 = float(close.rolling(25).mean().iloc[-1])
        out["ma25_dev"] = (c / ma25 - 1) * 100
    if len(close) >= 75:
        ma75 = float(close.rolling(75).mean().iloc[-1])
        out["ma75_dev"] = (c / ma75 - 1) * 100
    if len(close) >= 15:
        out["rsi14"] = calc_rsi(close)
    if len(vol) >= 60:
        v5 = float(vol.tail(5).mean())
        v60 = float(vol.tail(60).mean())
        if v60 > 0:
            out["vol_ratio"] = v5 / v60
    return out


def bulk_price_summary(symbols: list, chunk: int = 200) -> dict:
    """終値/52週高安値/テクニカル指標をチャンク分割で取得。"""
    out = {}
    for i in range(0, len(symbols), chunk):
        part = symbols[i:i + chunk]
        print(f"[INFO] 価格取得 {i + 1}-{i + len(part)}/{len(symbols)}")
        try:
            data = yf.download(part, period="1y", interval="1d",
                               group_by="ticker", auto_adjust=False,
                               threads=True, progress=False)
        except Exception as e:
            print(f"[WARN] チャンク取得失敗: {e}")
            continue
        if data is None or data.empty:
            continue
        for sym in part:
            try:
                df = (data[sym] if len(part) > 1 else data)
                df = df.dropna(subset=["Close"])
                if len(df) < 60:
                    continue
                rec = {
                    "close": float(df["Close"].iloc[-1]),
                    "high52": float(df["High"].max()),
                    "low52": float(df["Low"].min()),
                }
                rec.update(technicals(df))
                out[sym] = rec
            except Exception:
                continue
        time.sleep(1)
    print(f"[INFO] 価格データ取得完了: {len(out)}/{len(symbols)}銘柄")
    return out


def range_position(p: dict) -> float:
    span = p["high52"] - p["low52"]
    if span <= 0:
        return 0.5
    return (p["close"] - p["low52"]) / span


def fetch_fundamentals(sym: str) -> dict:
    try:
        info = yf.Ticker(sym).info
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        div_rate = info.get("trailingAnnualDividendRate")
        yld = None
        if div_rate and price:
            yld = float(div_rate) / float(price)
        return {
            "mcap": info.get("marketCap"),
            "pbr": info.get("priceToBook"),
            "per": info.get("trailingPE"),
            "yld": yld,
            "rev_g": info.get("revenueGrowth"),
            "roe": info.get("returnOnEquity"),
        }
    except Exception:
        return {}


def tech_cols(p: dict) -> dict:
    """出力用テクニカル列 (Noneは空欄)。"""
    def r(v, n=1):
        return round(v, n) if v is not None else ""
    return {
        "MA25乖離%": r(p.get("ma25_dev")),
        "MA75乖離%": r(p.get("ma75_dev")),
        "RSI14": r(p.get("rsi14")),
        "出来高倍率": r(p.get("vol_ratio"), 2),
    }


# ---------------------------------------------------------------
# コマンド: scan / rank
# ---------------------------------------------------------------

def scan_universe(tickers: dict, market: str) -> list:
    if not tickers:
        print(f"[WARN] {market}: 銘柄リストが空のためスキップ")
        return []
    symbols = list(tickers.keys())
    print(f"[INFO] {market}: {len(symbols)}銘柄をダウンロード中...")
    data = None
    for attempt in range(1, 4):
        data = yf.download(symbols, period="1y", interval="1d",
                           group_by="ticker", auto_adjust=False,
                           threads=True, progress=False)
        if data is not None and not data.empty:
            break
        print(f"[WARN] 空振り (試行{attempt}/3)。20秒待機...")
        time.sleep(20)
    if data is None or data.empty:
        print(f"[ERROR] {market}: データ取得に失敗")
        return []

    rows, ok = [], 0
    for sym in symbols:
        try:
            df = (data[sym] if len(symbols) > 1 else data)
            df = df.dropna(subset=["High"])
            if len(df) < 30:
                continue
            ok += 1
            today = df.iloc[-1]
            past_max = df["High"].iloc[:-1].max()
            if today["High"] >= past_max:
                close_v = float(today["Close"]) if pd.notna(today["Close"]) \
                    else float(df["Close"].dropna().iloc[-1])
                prev = df["Close"].dropna()
                chg = ((close_v / float(prev.iloc[-2]) - 1) * 100
                       if len(prev) >= 2 else None)
                rows.append({
                    "date": df.index[-1].strftime("%Y-%m-%d"),
                    "market": market, "ticker": sym, "name": tickers[sym],
                    "high": round(float(today["High"]), 2),
                    "close": round(close_v, 2),
                    "change_pct": round(chg, 2) if chg is not None else "",
                })
        except Exception:
            continue
    print(f"[INFO] {market}: 有効 {ok}/{len(symbols)}, 高値更新 {len(rows)}銘柄")
    return rows


def write_note(filename: str, text: str):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(OUTPUT_DIR, filename), "w", encoding="utf-8") as f:
        f.write(f"last run (UTC): {datetime.utcnow().isoformat()}\n{text}\n")


def cmd_scan():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    jp = scan_universe(get_jp_tickers(), "JP")
    us = scan_universe(get_us_tickers(), "US")
    rows = jp + us
    write_note("last_run.txt", f"JP: {len(jp)}件 / US: {len(us)}件")
    if not rows:
        print("本日の52週高値更新銘柄はありませんでした(または取得失敗)。")
        return
    df = pd.DataFrame(rows).sort_values(["market", "ticker"])
    daily = os.path.join(OUTPUT_DIR, f"{date.today().isoformat()}_highs.csv")
    df.to_csv(daily, index=False, encoding="utf-8-sig")
    hist_path = os.path.join(OUTPUT_DIR, "history.csv")
    if os.path.exists(hist_path):
        hist = pd.read_csv(hist_path)
        df = pd.concat([hist, df]).drop_duplicates(subset=["date", "ticker"])
    df.to_csv(hist_path, index=False, encoding="utf-8-sig")
    print(f"[DONE] {daily} に保存 ({len(rows)}銘柄)")


def cmd_rank():
    hist_path = os.path.join(OUTPUT_DIR, "history.csv")
    if not os.path.exists(hist_path):
        print("履歴がありません。先に scan を実行してください。")
        return
    hist = pd.read_csv(hist_path)
    rank = (hist.groupby(["market", "ticker", "name"]).size()
            .reset_index(name="更新回数")
            .sort_values(["market", "更新回数"], ascending=[True, False]))
    rank.to_csv(os.path.join(OUTPUT_DIR, "ranking.csv"),
                index=False, encoding="utf-8-sig")
    print(rank.to_string(index=False))


# ---------------------------------------------------------------
# コマンド: value_scan
# ---------------------------------------------------------------

def cmd_value_scan():
    universe = get_jpx_universe(("プライム", "スタンダード"))
    if not universe:
        write_note("value_last_run.txt", "JPXリスト取得失敗")
        return
    prices = bulk_price_summary(list(universe.keys()))
    cands = [(s, range_position(p)) for s, p in prices.items()
             if range_position(p) <= VALUE_POS_MAX]
    cands.sort(key=lambda x: x[1])
    cands = cands[:MAX_FUND_CALLS]
    print(f"[INFO] 安値圏候補 {len(cands)}銘柄の財務指標を取得中...")

    rows = []
    for i, (sym, pos) in enumerate(cands, 1):
        if i % 50 == 0:
            print(f"[INFO] 財務取得 {i}/{len(cands)}")
        f = fetch_fundamentals(sym)
        mc, pbr, per, yld = f.get("mcap"), f.get("pbr"), f.get("per"), f.get("yld")
        if not mc or not (VALUE_MCAP_MIN <= mc <= VALUE_MCAP_MAX):
            continue
        if not pbr or pbr >= VALUE_PBR_MAX:
            continue
        if not per or per <= 0 or per > VALUE_PER_MAX:
            continue
        if not yld or yld < VALUE_YIELD_MIN:
            continue
        row = {
            "ticker": sym, "name": universe[sym],
            "時価総額億円": round(mc / 1e8),
            "PBR": round(pbr, 2), "PER": round(per, 1),
            "配当利回り%": round(yld * 100, 2),
            "52週位置%": round(pos * 100, 1),
            "close": prices[sym]["close"],
        }
        row.update(tech_cols(prices[sym]))
        rows.append(row)
        time.sleep(0.3)

    write_note("value_last_run.txt",
               f"候補{len(cands)}件を精査 → 条件合致 {len(rows)}銘柄")
    if not rows:
        print("条件に合致する銘柄なし。")
        return
    df = pd.DataFrame(rows).sort_values("PBR")
    path = os.path.join(OUTPUT_DIR, f"value_{date.today().isoformat()}.csv")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[DONE] {path} に保存 ({len(rows)}銘柄)")
    print(df.to_string(index=False))


# ---------------------------------------------------------------
# コマンド: growth_scan
# ---------------------------------------------------------------

def cmd_growth_scan():
    universe = get_jpx_universe(("グロース", "スタンダード"))
    if not universe:
        write_note("growth_last_run.txt", "JPXリスト取得失敗")
        return
    prices = bulk_price_summary(list(universe.keys()))
    cands = [(s, range_position(p)) for s, p in prices.items()
             if range_position(p) >= GROWTH_POS_MIN]
    cands.sort(key=lambda x: -x[1])
    cands = cands[:MAX_FUND_CALLS]
    print(f"[INFO] 上昇圏候補 {len(cands)}銘柄の財務指標を取得中...")

    rows = []
    for i, (sym, pos) in enumerate(cands, 1):
        if i % 50 == 0:
            print(f"[INFO] 財務取得 {i}/{len(cands)}")
        f = fetch_fundamentals(sym)
        mc, rev_g, roe = f.get("mcap"), f.get("rev_g"), f.get("roe")
        if not mc or mc > GROWTH_MCAP_MAX:
            continue
        if rev_g is None or rev_g < GROWTH_REV_MIN:
            continue
        if roe is None or roe < GROWTH_ROE_MIN:
            continue
        row = {
            "ticker": sym, "name": universe[sym],
            "時価総額億円": round(mc / 1e8),
            "売上成長%": round(rev_g * 100, 1),
            "ROE%": round(roe * 100, 1),
            "52週位置%": round(pos * 100, 1),
            "close": prices[sym]["close"],
        }
        row.update(tech_cols(prices[sym]))
        rows.append(row)
        time.sleep(0.3)

    write_note("growth_last_run.txt",
               f"候補{len(cands)}件を精査 → 条件合致 {len(rows)}銘柄")
    if not rows:
        print("条件に合致する銘柄なし。")
        return
    df = pd.DataFrame(rows).sort_values("売上成長%", ascending=False)
    path = os.path.join(OUTPUT_DIR, f"growth_{date.today().isoformat()}.csv")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[DONE] {path} に保存 ({len(rows)}銘柄)")
    print(df.to_string(index=False))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    {"rank": cmd_rank,
     "value_scan": cmd_value_scan,
     "growth_scan": cmd_growth_scan}.get(cmd, cmd_scan)()