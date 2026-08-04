# -*- coding: utf-8 -*-
"""
52週高値スキャナー (日米対応 MVP)
=================================
使い方 (Replit の Shell で):
  pip install yfinance pandas lxml
  python high52_scanner.py scan   # 本日の52週高値更新銘柄を抽出しCSV保存
  python high52_scanner.py rank   # 蓄積した履歴から更新回数ランキングを表示

- 米国: S&P500 構成銘柄を Wikipedia から自動取得
- 日本: 主要大型株の内蔵リスト (tickers_jp.txt を置けば差し替え可能)
- 出力: outputs/YYYY-MM-DD_highs.csv (当日分) / outputs/history.csv (累積)
"""

import os
import sys
from datetime import date

import pandas as pd
import yfinance as yf

OUTPUT_DIR = "outputs"

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
    """S&P500構成銘柄をWikipediaから取得。失敗時は空dict。"""
    try:
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        df = tables[0]
        return {
            str(sym).replace(".", "-"): str(name)
            for sym, name in zip(df["Symbol"], df["Security"])
        }
    except Exception as e:
        print(f"[WARN] S&P500リスト取得に失敗: {e}")
        return {}


def get_jp_tickers() -> dict:
    """tickers_jp.txt があれば優先 (1行1銘柄: '7203.T,トヨタ自動車')。"""
    path = "tickers_jp.txt"
    if os.path.exists(path):
        result = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(",")
                code = parts[0].strip()
                name = parts[1].strip() if len(parts) > 1 else code
                result[code] = name
        return result
    return JP_DEFAULT_TICKERS


# ---------------------------------------------------------------
# スキャン処理
# ---------------------------------------------------------------

def scan_universe(tickers: dict, market: str) -> list:
    """52週高値を更新した銘柄を抽出して行リストで返す。"""
    if not tickers:
        return []
    symbols = list(tickers.keys())
    print(f"[INFO] {market}: {len(symbols)}銘柄をダウンロード中...")
    data = yf.download(
        symbols, period="1y", interval="1d",
        group_by="ticker", auto_adjust=False,
        threads=True, progress=False,
    )
    rows = []
    for sym in symbols:
        try:
            df = data[sym].dropna(subset=["High"]) if len(symbols) > 1 else data.dropna(subset=["High"])
            if len(df) < 30:  # データ不足はスキップ
                continue
            today = df.iloc[-1]
            past_max = df["High"].iloc[:-1].max()
            if today["High"] >= past_max:
                prev_close = df["Close"].iloc[-2]
                chg = (today["Close"] / prev_close - 1) * 100
                rows.append({
                    "date": df.index[-1].strftime("%Y-%m-%d"),
                    "market": market,
                    "ticker": sym,
                    "name": tickers[sym],
                    "high": round(float(today["High"]), 2),
                    "close": round(float(today["Close"]), 2),
                    "change_pct": round(float(chg), 2),
                })
        except Exception:
            continue
    return rows


def cmd_scan():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    rows = []
    rows += scan_universe(get_jp_tickers(), "JP")
    rows += scan_universe(get_us_tickers(), "US")

    if not rows:
        print("本日の52週高値更新銘柄はありませんでした(または取得失敗)。")
        return

    df = pd.DataFrame(rows).sort_values(["market", "change_pct"], ascending=[True, False])
    today_str = date.today().isoformat()
    daily_path = os.path.join(OUTPUT_DIR, f"{today_str}_highs.csv")
    df.to_csv(daily_path, index=False, encoding="utf-8-sig")

    # 履歴に追記 (同一日・同一銘柄の重複は除去)
    hist_path = os.path.join(OUTPUT_DIR, "history.csv")
    if os.path.exists(hist_path):
        hist = pd.read_csv(hist_path)
        df = pd.concat([hist, df]).drop_duplicates(subset=["date", "ticker"])
    df.to_csv(hist_path, index=False, encoding="utf-8-sig")

    print(f"[DONE] {len(rows)}銘柄が52週高値を更新。{daily_path} に保存しました。")
    print(pd.DataFrame(rows).to_string(index=False))


def cmd_rank():
    hist_path = os.path.join(OUTPUT_DIR, "history.csv")
    if not os.path.exists(hist_path):
        print("履歴がまだありません。まず `python high52_scanner.py scan` を実行してください。")
        return
    hist = pd.read_csv(hist_path)
    rank = (
        hist.groupby(["market", "ticker", "name"])
        .size().reset_index(name="更新回数")
        .sort_values(["market", "更新回数"], ascending=[True, False])
    )
    print("=== 52週高値 更新回数ランキング ===")
    print(rank.to_string(index=False))
    rank.to_csv(os.path.join(OUTPUT_DIR, "ranking.csv"),
                index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "rank":
        cmd_rank()
    else:
        cmd_scan()
