# -*- coding: utf-8 -*-
"""
东财数据源（2026-09-16 新增）
=============================
背景：申万官网 index_publish 接口数据发布严重滞后 —— 2026-09-16 14:00 实测其最新
仍为 09-14（落后 2 个交易日），导致工作台无法反映当日行情。东财行业板块指数则是
实时更新（当日盘中即有），且东财"行业板块"本身就是申万行业分类（493 个），
与现有 155 个申万指数名称匹配率 97.4%（其余 4 个仅"Ⅱ/Ⅲ"后缀差异，可自动兜底）。

设计要点：
  - pkl 结构保持 {names, data}，另加 source 字段标记数据源（eastmoney / sws）
  - 首次切换：全量重建 155 个板块历史（东财 2023 年至今约 899 条/板块，
    足够 250 日 RPS 与 120 日动量计算）
  - 之后每天：增量抓取（beg = 上次最新日期），秒级完成
  - 基准 801003（原申万A指）改用上证指数 1.000001 的 K 线填充 —— 评分引擎
    score_v2_final.py 里 BENCH="801003" 无需任何改动
  - 失败时由 updater.fetch_all 回退到申万源，再失败由 workbench_data 兜底
"""
import os
import time
import pandas as pd
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
PKL = os.path.join(BASE, "data", "sw_industry.pkl")

EM_LIST = "https://push2delay.eastmoney.com/api/qt/clist/get"
# K 线主机轮询：第一个能通就用哪个（不同网络环境可达性不同，多备几个提高成功率）
EM_KLINE_HOSTS = [
    "https://push2his.eastmoney.com",
    "https://1.push2his.eastmoney.com",
    "https://7.push2his.eastmoney.com",
    "https://push2delay.eastmoney.com",
]

BENCH = "801003"          # 评分引擎使用的基准代码（槽位保留）
BENCH_EM = "1.000001"     # 东财「上证指数」secid，代替申万A指作为基准
BEG_FULL = "20230101"     # 全量重建起点（约 650 个交易日，足够 250 日 RPS 与 120 日动量）

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
}

_HOST = None   # 探测出来的可用 K 线主机（模块级缓存）


def em_boards():
    """东财行业板块 {名称: BK代码}（493 个，分页抓全）"""
    em = {}
    for pn in range(1, 8):
        j = requests.get(EM_LIST, params={
            "pn": pn, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fs": "m:90+t:2+f:!50", "fields": "f12,f14", "fid": "f3",
        }, headers=HEADERS, timeout=25).json()
        d = j.get("data")
        if not d:
            break
        diff = d.get("diff") or []
        if not diff:
            break
        for x in diff:
            em[x["f14"]] = x["f12"]
    return em


def code_map(names):
    """申万 code -> 东财板块 BK 代码（名称精确匹配 + Ⅰ/Ⅱ/Ⅲ 后缀兜底）"""
    em = em_boards()
    m = {}
    for code, name in names.items():
        if code == BENCH:
            continue
        bk = em.get(name)
        if not bk:
            base = name.rstrip("ⅠⅡⅢ")
            for suf in ("Ⅲ", "Ⅱ", "Ⅰ", ""):
                if base + suf in em:
                    bk = em[base + suf]
                    break
        if bk:
            m[code] = bk
    return m


def _kline_host():
    """探测可用的 K 线主机（结果缓存在模块级变量，避免每个板块都重试一遍）"""
    global _HOST
    if _HOST:
        return _HOST
    testid = BENCH_EM
    for h in EM_KLINE_HOSTS:
        try:
            j = requests.get(h + "/api/qt/stock/kline/get", params={
                "secid": testid, "fields1": "f1,f2,f3",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
                "klt": "101", "fqt": "0", "beg": "0", "end": "20991231", "lmt": "60",
            }, headers=HEADERS, timeout=15).json()
            if (j.get("data") or {}).get("klines"):
                _HOST = h
                print(f"[em] K线主机可用：{h}")
                return h
        except Exception:
            continue
    return None   # 全部不可达


def fetch_kline(secid, beg=None):
    """抓东财日 K，返回 DataFrame[date, close]"""
    beg = beg or BEG_FULL
    last_err = None
    # 主机若是探测出来的（已确认可达），失败时不再轮询其余域名 —— 那通常是限流或
    # 大响应被拦，换域名也没用，只会白白拖慢整轮（实测 8×4 次请求耗时数分钟）
    hosts = [_kline_host()] if _HOST else list(EM_KLINE_HOSTS)
    for h in hosts:
        try:
            j = requests.get(h + "/api/qt/stock/kline/get", params={
                "secid": secid, "fields1": "f1,f2,f3",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
                "klt": "101", "fqt": "0", "beg": beg, "end": "20991231", "lmt": "3000",
            }, headers=HEADERS, timeout=25).json()
            k = (j.get("data") or {}).get("klines") or []
            if k:
                return _parse_klines(k)
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    return pd.DataFrame(columns=["date", "close"])


def _parse_klines(k):
    rows = []
    for line in k:
        p = line.split(",")
        try:
            rows.append((pd.to_datetime(p[0]).date(), float(p[2])))
        except Exception:
            continue
    df = pd.DataFrame(rows, columns=["date", "close"])
    return df.dropna().sort_values("date").reset_index(drop=True)


def _merge(old, new):
    """按 date 合并（新数据覆盖同日旧数据）"""
    if old is None or len(old) == 0:
        return new
    return (pd.concat([old, new])
            .drop_duplicates(subset=["date"], keep="last")
            .sort_values("date")
            .reset_index(drop=True))


def update_from_em(force=False):
    """用东财数据更新 pkl（主数据源）"""
    if not os.path.exists(PKL):
        raise RuntimeError("pkl 不存在，无法建立板块映射")
    blob = pd.read_pickle(PKL)
    names = blob.get("names") or {}
    data = blob.get("data") or {}
    if not names:
        raise RuntimeError("pkl 无 names，无法建立映射")

    # 先探测 K 线主机：不可达时立刻放弃，不要白白跑 153 次失败请求
    if not _kline_host():
        raise RuntimeError("东财 K 线接口(push2his 系列)不可达，跳过全量重建")

    cmap = code_map(names)
    print(f"[em] 板块映射成功 {len(cmap)}/{len(names) - 1} 个")
    if len(cmap) < 100:
        raise RuntimeError(f"映射数量过少({len(cmap)})，判定东财源不可用")

    missed = [c for c in names if c != BENCH and c not in cmap]
    if missed:
        print(f"[em] 未匹配 {len(missed)} 个板块（沿用旧数据）：{', '.join(names[c] for c in missed[:6])}")

    need_full = force or (blob.get("source") != "eastmoney") or (not data)
    if need_full:
        # 先清空待重建槽位：一旦抓取失败，不会留下「半东财半申万」的混合数据，
        # 而是直接抛错 → updater 回退申万源（重新读 pkl，拿到完整原始数据）
        for c in list(cmap) + [BENCH]:
            data.pop(c, None)
        beg = BEG_FULL
        print(f"[em] 首次切换到东财源，全量重建历史（起点 {beg}，约 3-6 分钟）…")
    else:
        last = max(d["date"].iloc[-1] for d in data.values() if isinstance(d, pd.DataFrame) and len(d))
        beg = pd.Timestamp(last).strftime("%Y%m%d")
        print(f"[em] 增量更新，当前最新 {last}")

    fail = 0
    for i, (code, bk) in enumerate(cmap.items(), 1):
        try:
            df = fetch_kline(f"90.{bk}", beg=beg)
            if len(df):
                data[code] = df if need_full else _merge(data.get(code), df)
                fail = 0
            else:
                fail += 1
        except Exception as e:
            fail += 1
            print(f"[em] {code}({bk}) 抓取失败: {str(e)[:70]}")
        if fail >= 4:
            raise RuntimeError(f"连续 {fail} 个板块抓取失败，判定东财源不可用")
        time.sleep(0.2)
        if i % 30 == 0:
            print(f"[em] {i}/{len(cmap)}")

    # 基准：上证指数填充到 801003 槽位（评分引擎无需改动）
    try:
        bdf = fetch_kline(BENCH_EM, beg=beg)
        if len(bdf):
            data[BENCH] = bdf if need_full else _merge(data.get(BENCH), bdf)
    except Exception as e:
        print(f"[em] 基准抓取失败: {str(e)[:70]}")

    # 注意：data 的值可能是 DataFrame，不能用 `or []` 判空（会触发 DataFrame 真值歧义）
    bdf = data.get(BENCH)
    if bdf is None or not isinstance(bdf, pd.DataFrame) or len(bdf) == 0:
        raise RuntimeError("基准数据缺失，东财源不可用")

    valid = [d["date"].iloc[-1] for d in data.values()
             if isinstance(d, pd.DataFrame) and len(d)]
    if not valid:
        raise RuntimeError("东财源返回数据为空")
    newest = max(valid)
    print(f"[em] 完成：{len(data)} 个指数，最新 {newest}")

    pd.to_pickle({"names": names, "data": data, "source": "eastmoney"}, PKL)
    return names, data
