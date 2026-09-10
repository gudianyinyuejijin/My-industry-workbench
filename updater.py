# -*- coding: utf-8 -*-
"""
工作台每日更新器：抓取申万行业指数 → 五因子评分 → 合并ETF映射 → 输出 scores.json
"""
import json
import time
import os
import sys
import requests
import urllib3
import pandas as pd
import numpy as np

urllib3.disable_warnings()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from score_v2_final import run_v2
from etf_map import get_etfs, get_category

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE_DIR, "data", "scores.json")
PKL = os.path.join(BASE_DIR, "data", "sw_industry.pkl")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Referer": "https://www.swsresearch.com/",
    "Origin": "https://www.swsresearch.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
URL_LIST = "https://www.swsresearch.com/institute-sw/api/index_publish/current/"
URL_HIST = "https://www.swsresearch.com/institute-sw/api/index_publish/trend/"


def fetch_all(force=False):
    """抓取全部行业指数数据（增量：数据已是今天则跳过）"""
    pkl = PKL
    blob = None
    if os.path.exists(pkl) and not force:
        blob = pd.read_pickle(pkl)
        names, data = blob["names"], blob["data"]
        latest = max(d.iloc[-1]["date"] for d in data.values())
        # 判断缓存是否已是最新（避免 GitHub 全新环境下 pkl 陈旧导致页面停在旧日期）：
        # - 只有缓存最新日期就是「最近一个工作日」时才用缓存；否则强制重抓。
        # 周末/节假日：缓存停在最后一个交易日即视为最新；但若今天是交易日且缓存落后，
        # 必须重抓（否则页面会永远停留在 push 时的旧日期）。
        today = pd.Timestamp.now().date()
        latest_dt = pd.Timestamp(latest).date()
        # 最近工作日判断：倒推今天之前（含今天）最近一个非周末的日期
        ref = today
        while ref.weekday() >= 5:  # 5=周六, 6=周日
            ref -= pd.Timedelta(days=1)
        days_gap = (ref - latest_dt).days
        if days_gap <= 0:
            print(f"[update] 使用缓存数据，最新日期 {latest}（已是最近交易日）")
            return names, data
        print(f"[update] 缓存落后（缓存 {latest}，最近交易日 {ref}），重新抓取最新数据…")
    # 重新抓取
    names = {}
    for level in ["一级行业", "二级行业"]:
        r = requests.get(URL_LIST, params={"page": 1, "page_size": 1000, "indextype": level},
                         headers=HEADERS, verify=False, timeout=30)
        for row in r.json()["data"]["results"]:
            names[row["swindexcode"]] = row["swindexname"]
    names["801003"] = "申万A指(基准)"
    data = {}
    codes = list(names.keys())
    for i, code in enumerate(codes):
        for attempt in range(3):
            try:
                r = requests.get(URL_HIST, params={"swindexcode": code, "period": "DAY"},
                                 headers=HEADERS, verify=False, timeout=60)
                d = r.json()["data"]
                if d:
                    df = pd.DataFrame(d)[["bargaindate", "closeindex"]]
                    df.columns = ["date", "close"]
                    df["date"] = pd.to_datetime(df["date"]).dt.date
                    df["close"] = pd.to_numeric(df["close"], errors="coerce")
                    data[code] = df.dropna().sort_values("date").reset_index(drop=True)
                break
            except Exception as e:
                if attempt == 2:
                    print(f"[update] {code} 抓取失败: {e}")
                time.sleep(1.5)
        time.sleep(0.2)
        if (i + 1) % 40 == 0:
            print(f"[update] {i+1}/{len(codes)}")
    pd.to_pickle({"names": names, "data": data}, pkl)
    print(f"[update] 重新抓取完成，共 {len(data)} 个指数")
    return names, data


def compute_scores():
    """评分并输出 scores.json（版本2.0）"""
    from score_v2_final import run_v2
    result, names = run_v2()

    blob = pd.read_pickle(PKL)
    trade_date = max(d.iloc[-1]["date"] for d in blob["data"].values())

    rows = []
    for rank, (_, r) in enumerate(result.iterrows(), 1):
        etfs = get_etfs(r["代码"])
        rows.append({
            "rank": rank,
            "code": r["代码"],
            "name": r["行业"],
            "category": get_category(r["代码"]),
            "score": r["总分"],
            "status": r["状态"],
            "etfs": [{"name": n, "code": c} for n, c in etfs],
            "factors": {
                "结构": r["趋势结构"], "价格": r["价格强度"], "方向": r["趋势方向"],
                "相对": r["相对强度"], "过热": r["过热扣分"],
            },
            "chg20": r["20日涨幅%"], "chg60": r["60日涨幅%"],
            "excess60": r["超额60日%"], "excess120": r["超额120日%"],
        })

    status_count = result["状态"].value_counts().to_dict()
    payload = {
        "updated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": str(trade_date),
        "total": len(rows),
        "version": "2.0",
        "status_count": {k: int(status_count.get(k, 0)) for k in ["主升浪", "强趋势", "趋势形成", "底部反转", "弱势"]},
        "rows": rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"[update] 评分完成(版本2.0) → {OUT}，数据截至 {trade_date}")
    return payload


def update(force=False):
    fetch_all(force=force)
    return compute_scores()


if __name__ == "__main__":
    update(force=("--force" in sys.argv))
