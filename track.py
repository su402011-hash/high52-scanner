# -*- coding: utf-8 -*-
"""
予想トラッカー track.py
=======================
picks.csv に記録した予想(エントリー/目標/撤退)を、実際の株価で自動判定する。

使い方:
  python track.py        # outputs/track_YYYY-MM-DD.csv と outputs/track_summary.txt を生成

picks.csv の列:
  date        : 予想日 (YYYY-MM-DD)
  ticker      : yfinance形式 (例 4441.T / PLMR)
  name        : 銘柄名
  type        : 短期 / 長期
  entry_type  : 即 (予想日にentry価格で買った扱い) / ブレイク (高値がentryに達した日に買った扱い)
  entry       : エントリー価格
  target1     : 第一目標
  target2     : 第二目標
  stop        : 撤退価格 (空欄可 = 撤退判定なし)
  note        : メモ (任意)

判定ルール:
  - ブレイク型は、予想日以降で高値 >= entry となった最初の日を「発火日」とする。未発火なら状態=待機
  - 発火後(即型は予想日翌営業日から)、日々の 安値 <= stop なら「撤退」、高値 >= target2 なら「目標②」、
    高値 >= target1 なら「目標①」。同日に撤退と目標が重なった場合は撤退を優先(保守的判定)
  - どれにも該当しなければ「進行中」で現値ベースの損益を表示
"""

import os
from datetime import date, datetime

import pandas as pd
import yfinance as yf

PICKS = "picks.csv"
OUTPUT_DIR = "outputs"


def load_picks() -> pd.DataFrame:
    df = pd.read_csv(PICKS, dtype=str).fillna("")
    for c in ("entry", "target1", "target2", "stop"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def fetch_history(ticker: str, start: date) -> pd.DataFrame:
    try:
        df = yf.download(ticker, start=start.isoformat(), interval="1d",
                         auto_adjust=False, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df.dropna(subset=["Close"])
    except Exception as e:
        print(f"[WARN] {ticker} 取得失敗: {e}")
        return pd.DataFrame()


def evaluate(row: pd.Series, hist: pd.DataFrame) -> dict:
    out = {"発火日": "", "状態": "データなし", "現値": "", "損益%": "",
           "経過日数": "", "最高値%": ""}
    if hist.empty:
        return out
    hist = hist[hist.index.date >= row["date"]]
    if hist.empty:
        return out
    current = float(hist["Close"].iloc[-1])
    out["現値"] = round(current, 2)

    entry = row["entry"]
    # --- エントリー判定 ---
    if row["entry_type"] == "ブレイク":
        hit = hist[hist["High"] >= entry]
        if hit.empty:
            out["状態"] = "待機"
            out["損益%"] = round((current / entry - 1) * 100, 1)  # entryまでの距離
            return out
        fire_date = hit.index[0]
        out["発火日"] = fire_date.date().isoformat()
        after = hist[hist.index > fire_date]
    else:
        out["発火日"] = row["date"].isoformat()
        after = hist[hist.index.date > row["date"]]

    out["経過日数"] = len(after)
    if after.empty:
        out["状態"] = "進行中"
        out["損益%"] = round((current / entry - 1) * 100, 1)
        return out

    peak = float(after["High"].max())
    out["最高値%"] = round((peak / entry - 1) * 100, 1)

    stop, t1, t2 = row["stop"], row["target1"], row["target2"]
    reached_t1 = False
    for ts, d in after.iterrows():
        lo, hi = float(d["Low"]), float(d["High"])
        if pd.notna(stop) and lo <= stop:
            out["状態"] = "撤退"
            out["損益%"] = round((stop / entry - 1) * 100, 1)
            out["経過日数"] = len(after.loc[:ts])
            return out
        if pd.notna(t2) and hi >= t2:
            out["状態"] = "目標②達成"
            out["損益%"] = round((t2 / entry - 1) * 100, 1)
            out["経過日数"] = len(after.loc[:ts])
            return out
        if pd.notna(t1) and hi >= t1:
            reached_t1 = True
    out["状態"] = "目標①達成(継続中)" if reached_t1 else "進行中"
    out["損益%"] = round((current / entry - 1) * 100, 1)
    return out


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    picks = load_picks()
    rows = []
    for _, r in picks.iterrows():
        hist = fetch_history(r["ticker"], r["date"])
        res = evaluate(r, hist)
        rows.append({**r.to_dict(), **res})
        print(f"[INFO] {r['ticker']} {r['name']} → {res['状態']} {res['損益%']}%")

    df = pd.DataFrame(rows)
    df["date"] = df["date"].astype(str)
    path = os.path.join(OUTPUT_DIR, f"track_{date.today().isoformat()}.csv")
    df.to_csv(path, index=False, encoding="utf-8-sig")

    # --- サマリー ---
    lines = [f"予想トラッカー集計 (UTC {datetime.utcnow().isoformat()})", ""]
    for t in ("短期", "長期"):
        sub = df[df["type"] == t]
        if sub.empty:
            continue
        fired = sub[sub["状態"] != "待機"]
        done = sub[sub["状態"].isin(["撤退", "目標②達成"])]
        wins = sub[sub["状態"].isin(["目標①達成(継続中)", "目標②達成"])]
        pl = pd.to_numeric(fired["損益%"], errors="coerce")
        lines.append(f"[{t}] 予想{len(sub)}件 / 発火{len(fired)}件 / 決着{len(done)}件")
        lines.append(f"  目標到達{len(wins)}件 / 撤退{len(sub[sub['状態']=='撤退'])}件")
        if len(fired):
            lines.append(f"  発火分の平均損益 {pl.mean():.1f}% / "
                         f"合計 {pl.sum():.1f}% / 最大 {pl.max():.1f}% / 最小 {pl.min():.1f}%")
        lines.append("")
    summary = "\n".join(lines)
    with open(os.path.join(OUTPUT_DIR, "track_summary.txt"), "w",
              encoding="utf-8") as f:
        f.write(summary + "\n")
    print(summary)
    print(df[["date", "ticker", "name", "type", "entry_type", "entry",
              "発火日", "状態", "現値", "損益%", "最高値%"]].to_string(index=False))


if __name__ == "__main__":
    main()
