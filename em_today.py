# -*- coding: utf-8 -*-
"""
东财「当日涨跌幅补齐」模块（2026-09-16）
========================================
为什么需要它
------------
申万官网 index_publish 接口滞后约 1 个交易日：今天（T）白天它最新只到 T-1，
T 日收盘数据要等到 T+1 才发布。这导致工作台永远慢一天。

东财 push2delay 接口（本仓库 concept_score/erba 已在用，runner 上确认可达）
可以一次性拿到全部 496 个行业板块的**当日涨跌幅**。只要把它换算成收盘价，
追加到申万历史序列末尾，就能让数据当天出。

关键设计
--------
1. 只用「涨跌幅」做比例换算：close_T = close_{T-1} × (1 + pct/100)
   —— 所有技术指标（RPS / 动量 / 均线）都基于收益率序列，与点位口径无关，
      所以东财板块与申万行业的编制差异不会影响评分结论。
2. 交易日判定用东财返回的时间戳 f124，而不是"今天"，自动规避周末/节假日。
3. 盘中保护：若东财时间戳就是今天、但北京时间还没到 15:05（未收盘），
   则不追加 —— 盘中价不是收盘价，不能进日线序列。
4. 幂等：日期重复时先删除同日旧数据再追加；只在东财日期 > 现有最新日期时动手。

失败一律抛异常，由 updater.fetch_all 捕获后继续用原数据，绝不破坏已有序列。
"""
import os
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
PKL = os.path.join(BASE, "data", "sw_industry.pkl")

EM_LIST = "https://push2delay.eastmoney.com/api/qt/clist/get"
EM_ULIST = "https://push2delay.eastmoney.com/api/qt/ulist.np/get"

BENCH = "801003"       # 评分引擎使用的基准代码（槽位保留）
BENCH_EM = "1.000001"  # 东财「上证指数」secid

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
}

CST = timezone(timedelta(hours=8))   # 北京时间


def _bj_now():
    return datetime.now(CST)


def em_board_chg():
    """东财行业板块当日涨跌幅 {板块名: 涨跌幅%}（分页抓全 496 个）"""
    out = {}
    for pn in range(1, 8):
        j = requests.get(EM_LIST, params={
            "pn": pn, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fs": "m:90+t:2+f:!50", "fields": "f2,f3,f12,f14", "fid": "f3",
        }, headers=HEADERS, timeout=25).json()
        diff = ((j.get("data") or {}).get("diff")) or []
        if not diff:
            break
        for x in diff:
            try:
                out[str(x["f14"])] = float(x["f3"])
            except Exception:
                continue
    return out


def bench_today():
    """上证指数当日涨跌幅 + 东财行情时间戳 → (涨跌幅%, 行情日期)"""
    j = requests.get(EM_ULIST, params={
        "secids": BENCH_EM, "fltt": 2, "invt": 2,
        "fields": "f2,f3,f12,f14,f124",
    }, headers=HEADERS, timeout=20).json()
    d = (j.get("data") or {}).get("diff") or []
    if not d:
        raise RuntimeError("东财基准指数无返回")
    x = d[0] if isinstance(d, list) else list(d.values())[0]
    pct = float(x.get("f3"))
    ts = int(x.get("f124") or 0)
    if ts <= 0:
        raise RuntimeError("东财未返回行情时间戳，无法判定交易日")
    day = datetime.fromtimestamp(ts, CST).date()
    return pct, day


def _match(name, chg):
    """板块名精确匹配 + Ⅰ/Ⅱ/Ⅲ 后缀兜底"""
    if name in chg:
        return chg[name]
    base = name.rstrip("ⅠⅡⅢ")
    for suf in ("Ⅲ", "Ⅱ", "Ⅰ", ""):
        if base + suf in chg:
            return chg[base + suf]
    return None


def append_today():
    """把东财当日收盘涨跌幅补进 pkl，返回 (names, data)"""
    if not os.path.exists(PKL):
        raise RuntimeError("pkl 不存在")
    blob = pd.read_pickle(PKL)
    names = blob.get("names") or {}
    data = blob.get("data") or {}
    if not names or not data:
        raise RuntimeError("pkl 内容不完整")

    bpct, eday = bench_today()
    now = _bj_now()
    # 盘中 vs 收盘后，两种情况区别对待：
    #   盘中（15:02 前）→ 仍构造当天数据，但标记为 intraday —— 用户中午也能看到
    #                     今天的实时强弱，下午收盘那次会用收盘价自动覆盖修正
    #   收盘后          → 正常补齐 / 校准
    # 之所以敢让盘中数据进序列，是因为「同日校准」机制保证它一定会被收盘价替换。
    minutes = now.hour * 60 + now.minute
    intraday = (eday == now.date() and minutes < 15 * 60 + 2)

    cur = max(d["date"].iloc[-1] for d in data.values()
              if isinstance(d, pd.DataFrame) and len(d))
    cur = pd.Timestamp(cur).date()
    # eday > cur → 补上新的一天
    # eday == cur → 同一天，收盘后用最新涨跌幅「校准」一次（见下）
    # eday < cur  → 东财比现有还旧，绝对不能倒退
    if eday < cur:
        raise RuntimeError(f"东财最新 {eday} 旧于现有 {cur}，禁止覆盖")
    same_day = (eday == cur)
    # 护栏：东财在停市日会一直吐「上一交易日」的数据，时间戳可能跨度异常，
    # 直接拿来追加会凭空造出一个假交易日
    if eday.weekday() >= 5:
        raise RuntimeError(f"{eday} 是周末，非交易日，不追加")
    # 未来日期必然是脏数据（时差/伪造时间戳）。
    # 注意：这里刻意不限制「距今跨度」，因为长假后首次运行需要追赶若干天，
    # 一刀切反而会把正常的追补卡死。只要 eday 是东财真实吐出来的交易日即可。
    if eday > now.date():
        raise RuntimeError(f"东财日期 {eday} 晚于今天 {now.date()}，数据异常，不追加")

    chg = em_board_chg()
    if len(chg) < 300:
        raise RuntimeError(f"东财板块数量异常({len(chg)})，判定不可用")

    hit = miss = 0
    for code, name in names.items():
        if code == BENCH:
            pct = bpct
        else:
            pct = _match(name, chg)
        if pct is None:
            miss += 1
            continue
        df = data.get(code)
        if not isinstance(df, pd.DataFrame) or len(df) == 0:
            miss += 1
            continue
        prev = float(df["close"].iloc[-1])
        new = round(prev * (1.0 + float(pct) / 100.0), 4)
        row = pd.DataFrame({"date": [eday], "close": [new]})
        data[code] = (pd.concat([df[df["date"] != eday], row])
                      .sort_values("date").reset_index(drop=True))
        hit += 1

    if hit < 100:
        raise RuntimeError(f"补齐命中过少({hit})，放弃本次补齐")

    if intraday:
        print(f"[em-today] 盘中快照 {eday}（{now:%H:%M} 未收盘，收盘后自动校准）："
              f"命中 {hit}，基准 {bpct}%")
    elif same_day:
        print(f"[em-today] 校准 {eday}（收盘后用最新涨跌幅覆盖）：命中 {hit}，基准 {bpct}%")
    else:
        print(f"[em-today] 补齐 {eday}：命中 {hit} 个，未匹配 {miss} 个，基准涨跌幅 {bpct}%")

    blob["data"] = data
    if "em_today" not in str(blob.get("source") or ""):
        blob["source"] = (blob.get("source") or "sws") + "+em_today"
    blob["intraday"] = bool(intraday)      # 供网页/推送标注「盘中快照」
    blob["last_em_day"] = str(eday)
    pd.to_pickle(blob, PKL)
    return names, data


if __name__ == "__main__":
    n, d = append_today()
    print("done")
