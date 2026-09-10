# -*- coding: utf-8 -*-
"""
行业主升浪趋势识别系统 · 版本 2.0（依据权威截图重新推导）
=========================================================
版本2.0 评分 = 趋势结构(30) + 价格强度(20) + 趋势方向(15) + 相对强度(25) − 过热扣分(10)
阶段阈值：主升浪 >80 ｜ 强趋势 65–80 ｜ 趋势形成 50–65 ｜ 底部反转 35–50 ｜ 弱势 <35

【标定依据】以甲供 2026-09-08 17:00 权威截图为正：
  · 当时主升浪=0 个，强趋势=仅「医疗服务 77.0」「国有大型银行Ⅱ 70.0」，
    其余头部（农产品加工、城商行、渔业、炼化、养殖、白电、贵金属、航运、教育、
    种植、装修装饰、一般零售、航海装备、焦炭、煤炭开采、房地产服务、饲料、调味发酵品）
    均为「趋势形成 50–65」。
  · 医疗服务 60日相对基准超额 ≈ +38.8%（全行业第一）、120日超额 +29.4%，
    但 20日回调、短期均线破位 —— 说明「相对强度以超额收益为主导」且「近期回调不过热不扣分」。
  · 农业链（养殖、渔业、种植、农产品）短期均线多头但 BIAS20 高达 7–14% ——
    「过热扣分力度大于初版」，将其压回趋势形成。
  · 国有大行 短期均线多头 + 超额不错 + 不过热 → 70；白色家电 短期破位 + 超额平淡 → 50–65。
"""
import os
import pandas as pd
import numpy as np

BENCH = "801003"
DEFAULT_PKL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sw_industry.pkl")

def calc_indicators(df):
    out = df.copy(); c = out["close"]
    for n in [5, 10, 20, 60, 120]:
        out[f"ma{n}"] = c.rolling(n).mean()
    std = c.rolling(20).std()
    out["boll_up"] = out["ma20"] + 2 * std
    out["boll_low"] = out["ma20"] - 2 * std
    rng = out["boll_up"] - out["boll_low"]
    out["pctb"] = np.where(rng > 0, (c - out["boll_low"]) / rng, 0.5)
    out["bias20"] = c / out["ma20"] - 1
    out["bias60"] = c / out["ma60"] - 1
    for n in [20, 60, 120]:
        out[f"ret{n}"] = c / c.shift(n) - 1
    return out


def score(hist, bench_row):
    row = hist.iloc[-1]; h = hist; close = row["close"]
    ma20, ma60, ma120 = row["ma20"], row["ma60"], row["ma120"]
    pb = row["pctb"]; bias = row["bias20"]

    # 1) 趋势结构 30 —— 中长期均线火车轨（MA20>MA60>MA120 + 均线向上）为骨架
    s1 = 0
    if ma20 > ma60:  s1 += 8
    if ma60 > ma120: s1 += 8
    if row["ma60"]  > h["ma60"].iloc[-6]:  s1 += 7
    if row["ma120"] > h["ma120"].iloc[-6]: s1 += 7
    s1 = min(30, s1)

    # 2) 价格强度 20 —— 价格相对中长期均线比值 + 布林健康度
    s2 = 0
    p60 = close / ma60 - 1; p120 = close / ma120 - 1
    if p60 >= 0.06:  s2 += 5
    elif p60 >= 0.03: s2 += 4
    elif p60 >= 0.00: s2 += 2
    elif p60 >= -0.03: s2 += 1
    if p120 >= 0.04: s2 += 5
    elif p120 >= 0.01: s2 += 3
    elif p120 >= 0.00: s2 += 2
    elif p120 >= -0.04: s2 += 1
    if 0.45 <= pb <= 0.78: s2 += 6
    elif 0.35 <= pb < 0.45 or 0.78 < pb <= 0.88: s2 += 4
    elif 0.25 <= pb < 0.35 or 0.88 < pb <= 1.00: s2 += 1
    s2 = min(20, s2)

    # 3) 趋势方向 15 —— 价格站上中长期均线 + 均线向上
    s3 = 0
    if close > ma20:  s3 += 2
    if close > ma60:  s3 += 2
    if close > ma120: s3 += 2
    if row["ma20"]  > h["ma20"].iloc[-11]:  s3 += 3
    if row["ma60"]  > h["ma60"].iloc[-11]:  s3 += 3
    if row["ma120"] > h["ma120"].iloc[-11]: s3 += 3
    s3 = min(15, s3)

    # 4) 相对强度 25 —— 相对基准的超额收益（60日 + 120日）为主导
    ex60  = (row["ret60"]  - bench_row["ret60"])  * 100
    ex120 = (row["ret120"] - bench_row["ret120"]) * 100
    s4 = 0
    if ex60 >= 30:  s4 += 16
    elif ex60 >= 15: s4 += 13
    elif ex60 >= 10: s4 += 10
    elif ex60 >= 5:  s4 += 7
    elif ex60 >= 1:  s4 += 4
    else: s4 += 1
    if ex120 >= 15: s4 += 9
    elif ex120 >= 5: s4 += 7
    elif ex120 >= 0: s4 += 4
    elif ex120 >= -6: s4 += 2
    else: s4 += 0
    s4 = min(25, s4)

    # 5) 过热扣分 10 —— 短期偏离过大（BIAS20 过高 / 贴上轨）
    s5 = 0
    if bias >= 0.12 or (bias >= 0.08 and pb >= 1.0): s5 = 10
    elif bias >= 0.08 or pb >= 1.10:                  s5 = 8
    elif bias >= 0.06:                                s5 = 6
    elif bias >= 0.04:                                s5 = 3
    else: s5 = 0

    total = max(0, round(s1 + s2 + s3 + s4 - s5, 1))
    return s1, s2, s3, s4, -s5, total


def status_of(s):
    if s > 80:  return "主升浪"
    if s >= 65: return "强趋势"
    if s >= 50: return "趋势形成"
    if s >= 35: return "底部反转"
    return "弱势"


def run_v2(pkl=DEFAULT_PKL):
    blob = pd.read_pickle(pkl); names, data = blob["names"], blob["data"]
    ind = {c: calc_indicators(d) for c, d in data.items() if len(d) >= 260 and c != BENCH}
    bench = calc_indicators(data[BENCH]).iloc[-1]
    rows = []
    for code, h in ind.items():
        s1, s2, s3, s4, s5, total = score(h, bench)
        row = h.iloc[-1]
        rows.append({
            "代码": code, "行业": names.get(code, code),
            "趋势结构": s1, "价格强度": s2, "趋势方向": s3, "相对强度": s4, "过热扣分": s5,
            "总分": total, "状态": status_of(total),
            "20日涨幅%": round(row["ret20"]*100, 1), "60日涨幅%": round(row["ret60"]*100, 1),
            "120日涨幅%": round(row["ret120"]*100, 1),
            "超额60日%": round((row["ret60"]-bench["ret60"])*100, 1),
            "超额120日%": round((row["ret120"]-bench["ret120"])*100, 1),
            "BIAS20%": round(row["bias20"]*100, 1), "%B": round(row["pctb"], 2),
        })
    df = pd.DataFrame(rows).sort_values("总分", ascending=False).reset_index(drop=True)
    return df, names


if __name__ == "__main__":
    df, names = run_v2()
    print("强趋势及以上（≥65）：")
    print(df[df["总分"] >= 65][["行业", "趋势结构", "价格强度", "趋势方向", "相对强度", "过热扣分", "总分", "状态"]].to_string(index=False))
    print("\n状态分布:", df["状态"].value_counts().to_dict())
