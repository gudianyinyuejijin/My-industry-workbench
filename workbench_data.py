# -*- coding: utf-8 -*-
"""
工作台完整数据引擎：计算 行业评分 + 多活跃ETF + 行业RPS + 二八指数 + 关键结论
输出 data/workbench.json，供 app.py 的 /api/data 实时返回（浏览器动态渲染）。
"""
import json, os, sys
import pandas as pd
import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from score_v2_final import run_v2
from etf_map import get_etfs, get_category
from erba import run as run_erba, signal as erba_signal
from concept_score import run as run_concepts

BENCH = "801003"


def _rps():
    blob = pd.read_pickle(f"{BASE}/data/sw_industry.pkl")
    data = blob["data"]; names = blob["names"]
    ind = {c: d for c, d in data.items() if len(d) >= 260 and c != BENCH}
    rows = []
    for c, d in ind.items():
        close = d["close"].iloc[-1]
        r = {"code": c, "name": names.get(c, c)}
        for n in [20, 50, 60, 90, 120, 250]:
            r[f"ret{n}"] = (close / d["close"].iloc[-n-1] - 1) * 100 if len(d) > n else np.nan
        rows.append(r)
    rdf = pd.DataFrame(rows)
    for n in [20, 50, 60, 90, 120, 250]:
        rdf[f"RPS{n}"] = (rdf[f"ret{n}"].rank(pct=True) * 100).round(0)
    rdf_out = rdf.sort_values("RPS60", ascending=False)
    out = []
    for rank, (_, r) in enumerate(rdf_out.iterrows(), 1):
        out.append({
            "rank": rank, "code": r["code"], "name": r["name"],
            "rps20": None if pd.isna(r["RPS20"]) else float(r["RPS20"]),
            "rps50": None if pd.isna(r["RPS50"]) else float(r["RPS50"]),
            "rps60": None if pd.isna(r["RPS60"]) else float(r["RPS60"]),
            "rps90": None if pd.isna(r["RPS90"]) else float(r["RPS90"]),
            "rps120": None if pd.isna(r["RPS120"]) else float(r["RPS120"]),
            "rps250": None if pd.isna(r["RPS250"]) else float(r["RPS250"]),
            "ret20": None if pd.isna(r["ret20"]) else round(float(r["ret20"]), 1),
            "ret60": None if pd.isna(r["ret60"]) else round(float(r["ret60"]), 1),
            "ret120": None if pd.isna(r["ret120"]) else round(float(r["ret120"]), 1),
        })
    return out


def build(force=False):
    """计算并写入 data/workbench.json，返回 payload"""
    # 核心评分
    from updater import update
    try:
        update(force=force)                       # 保证 sw_industry.pkl 与 scores.json 最新
    except Exception as e:
        # 抓取失败（数据源超时/限流等）时兜底：继续用现有 pkl，不中断整条流水线，
        # 页面仍可显示（可能为最近一次成功数据），避免 GitHub Actions 因此整失败。
        print(f"[workbench_data] 抓取失败，回退使用本地缓存: {str(e)[:160]}")
        if not os.path.exists(f"{BASE}/data/sw_industry.pkl"):
            raise  # 连缓存都没有才真的报错
    result, names = run_v2()

    blob = pd.read_pickle(f"{BASE}/data/sw_industry.pkl")
    trade_date = max(d.iloc[-1]["date"] for d in blob["data"].values())

    # 评分 + 多ETF + RPS合并
    rps_list = _rps()
    rdf = pd.DataFrame({r["code"]: r for r in rps_list}).T
    rows = []
    for i, (_, r) in enumerate(result.iterrows(), 1):
        etfs = get_etfs(r["代码"])            # [(名,码)] 多个
        rows.append({
            "rank": i, "code": r["代码"], "name": r["行业"],
            "category": get_category(r["代码"]),
            "score": float(r["总分"]), "status": r["状态"],
            "etfs": [{"name": n, "code": c} for n, c in etfs],
            "factors": {"结构": float(r["趋势结构"]), "价格": float(r["价格强度"]),
                        "方向": float(r["趋势方向"]), "相对": float(r["相对强度"]),
                        "过热": float(r["过热扣分"])},
            "chg20": float(r["20日涨幅%"]), "chg60": float(r["60日涨幅%"]),
            "chg120": float(r["120日涨幅%"]),
            "excess60": float(r["超额60日%"]), "excess120": float(r["超额120日%"]),
            "bias20": float(r["BIAS20%"]), "pctb": float(r["%B"]),
            "rps60": float(rps_list and next((x["rps60"] for x in rps_list if x["code"] == r["代码"]), None) or 0),
        })

    status_count = result["状态"].value_counts().to_dict()
    erba_rows = run_erba()
    erba_date = max((r["date"] for r in erba_rows if r["date"]), default=None) or str(trade_date)

    # 概念板块（简化五因子+RPS+ETF）
    concepts = run_concepts()
    csc = {}
    for r in concepts:
        csc[r["status"]] = csc.get(r["status"], 0) + 1

    payload = {
        "updated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": str(trade_date),
        "version": "2.0",
        "total": len(rows),
        "status_count": {k: int(status_count.get(k, 0)) for k in ["主升浪", "强趋势", "趋势形成", "底部反转", "弱势"]},
        "rows": rows,
        "rps": rps_list,
        "erba": {"date": erba_date, "threshold": 0.0, "rows": erba_rows,
                 "signal2": erba_signal(erba_rows, "二"), "signal8": erba_signal(erba_rows, "八")},
        "concepts": concepts,
        "concepts_count": {k: int(csc.get(k, 0)) for k in ["主升浪", "强趋势", "趋势形成", "底部反转", "弱势"]},
    }

    # ---- 倒退保护（2026-09-12 加）----
    # 场景：数据源（申万/东财）临时异常/被限流时，update() 会回退用仓库里的旧 pkl
    # 重算——而 pkl 从不回传仓库、可能停在上传时的旧日期（如 9/8），算出的结果会比
    # 线上现有 workbench.json（如 9/11）更旧。绝不能用旧数据覆盖新数据：
    # 此时保留现有 payload（网页/推送/commit 维持"最近一次成功"状态），直到数据源恢复。
    wb_path = f"{BASE}/data/workbench.json"
    if os.path.exists(wb_path):
        try:
            with open(wb_path, encoding="utf-8") as f:
                old = json.load(f)
            old_date = str(old.get("trade_date") or "")
            new_date = str(payload.get("trade_date") or "")
            if old_date and new_date and new_date < old_date:
                print(f"[protect] 数据源异常：本次只能算到 {new_date}，旧于现有 {old_date} —— "
                      f"保留现有数据不倒退（网页/推送维持最近一次成功结果）")
                old = dict(old)
                old["note"] = f"数据源异常，沿用最近一次成功数据（{old_date}），本次未更新"
                # 带着提示写回磁盘（数据本身保持旧的好数据），让推送/网页都能看到异常状态；
                # 下次数据源恢复后会走正常路径整体覆盖，note 自动消失。
                try:
                    with open(wb_path, "w", encoding="utf-8") as f:
                        json.dump(old, f, ensure_ascii=False)
                except Exception as e:
                    print(f"[protect] note 写回失败（忽略）: {e}")
                return old
        except Exception as e:
            print(f"[protect] 保护检查失败（忽略，正常写入）: {e}")

    os.makedirs(f"{BASE}/data", exist_ok=True)
    with open(f"{BASE}/data/workbench.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return payload


if __name__ == "__main__":
    p = build(force="--force" in sys.argv)
    print(f"workbench.json 生成完成：数据 {p['trade_date']}，评分{p['total']}行 / RPS{len(p['rps'])} / 二八{len(p['erba']['rows'])}")
