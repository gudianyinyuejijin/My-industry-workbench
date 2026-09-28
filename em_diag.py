# -*- coding: utf-8 -*-
"""东财连通性自检 —— 可独立运行，专为定位 GitHub Actions runner 上的取数失败

用法：
    python em_diag.py

为什么需要它
------------
GitHub Actions 的出口 IP 属于公共云数据中心段。东方财富会按 IP 做限流甚至直接断连，
表现为三种之一：ConnectionError / HTTP 403 / HTTP 200 但 data 为 null 或 diff 为空。
而 runner 的出口 IP **每次运行都可能不同**，所以现象是「时通时断」——
今天中午能推送、今晚就没数据，并不是代码抽风。

这个脚本把「当前这台机器 → 东财各域名」的连通情况一次性打清楚：
哪个域名通、哪个被拒、HTTP 多少、拿到几条数据、行情时间戳是几号几点。
有了它，一眼就能判断是「东财整体把这段 IP 封了」还是「个别域名被墙」，
不至于再靠猜。

在 GitHub Actions 里怎么跑：Actions → daily_workbench → Run workflow，
日志里 "diagnose EM connectivity" 那一段就是它。
"""
import time
import requests
import urllib3
from datetime import datetime, timezone, timedelta

urllib3.disable_warnings()

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
}
CST = timezone(timedelta(hours=8))

# 候选主机：不同子域名解析到不同集群，被封时往往只是其中一段
HOSTS = [
    "https://push2delay.eastmoney.com",   # 延时行情（本仓库主力在用）
    "https://push2.eastmoney.com",        # 实时行情主站
    "https://82.push2.eastmoney.com",     # 数字镜像节点
    "https://23.push2.eastmoney.com",
    "https://push2ex.eastmoney.com",      # 扩展节点
    "https://push2his.eastmoney.com",     # 历史 K 线
    "https://1.push2his.eastmoney.com",
    "https://7.push2his.eastmoney.com",
]

# (接口路径, 参数, 用途说明)
CHECKS = [
    ("/api/qt/ulist.np/get",
     {"secids": "1.000001,0.399001,2.932000,0.899050", "fltt": 2, "np": 1,
      "fields": "f12,f14,f3,f110,f124"},
     "指数实时行情(ulist.np)"),
    ("/api/qt/clist/get",
     {"pn": 1, "pz": 5, "po": 1, "np": 1, "fltt": 2, "invt": 2,
      "fs": "m:90+t:2+f:!50", "fields": "f2,f3,f12,f14", "fid": "f3"},
     "行业板块列表(clist)"),
    ("/api/qt/stock/kline/get",
     {"secid": "1.000001", "fields1": "f1,f2,f3,f4,f5,f6",
      "fields2": "f51,f52,f53,f54,f55,f56,f57", "klt": 101, "fqt": 1,
      "end": "20500101", "lmt": 25},
     "日K线(kline)"),
]


def _stamp(ts):
    try:
        return datetime.fromtimestamp(int(ts), CST).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "无"


def check_host(host, path, params, timeout=12):
    """返回 (是否可用, 描述)"""
    t0 = time.time()
    try:
        r = requests.get(host + path, params=params, headers=HEADERS, timeout=timeout)
        cost = time.time() - t0
        if r.status_code != 200:
            return False, f"HTTP {r.status_code} ({cost:.1f}s)"
        try:
            j = r.json()
        except Exception:
            return False, f"非JSON响应 {len(r.text)}字节 ({cost:.1f}s)"
        d = j.get("data") or {}
        if path.endswith("kline/get"):
            rows = d.get("klines") or []
            if not rows:
                return False, f"200但无K线 ({cost:.1f}s)"
            return True, f"{len(rows)}条K线 末日={rows[-1].split(',')[0]} ({cost:.1f}s)"
        diff = d.get("diff") or []
        if not diff:
            return False, f"200但diff为空 ({cost:.1f}s)"
        first = diff[0] if isinstance(diff, list) else list(diff.values())[0]
        return True, (f"{len(diff)}条 "
                      f"{first.get('f14')} 当日{first.get('f3')}% "
                      f"行情时间={_stamp(first.get('f124'))} ({cost:.1f}s)")
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:50]} ({time.time()-t0:.1f}s)"


def main():
    now = datetime.now(CST)
    print("=" * 78)
    print(f"东财连通性自检   北京时间 {now:%Y-%m-%d %H:%M:%S}")
    print("判定标准：HTTP 200 且返回有效数据才算可用；403/空diff/ConnectionError 一律视为被挡")
    print("=" * 78)

    usable_by_check = {}
    for path, params, label in CHECKS:
        print(f"\n▶ {label}")
        usable_by_check[label] = []
        for h in HOSTS:
            ok, desc = check_host(h, path, params)
            flag = "✅" if ok else "❌"
            print(f"   {flag} {h:38s} {desc}")
            if ok:
                usable_by_check[label].append(h)

    print("\n" + "=" * 78)
    print("小结")
    print("=" * 78)
    for label, hs in usable_by_check.items():
        if hs:
            print(f"  {label}：{len(hs)} 个域名可用 → {', '.join(x.split('//')[1] for x in hs[:3])}")
        else:
            print(f"  {label}：❌ 全部域名不可用 —— 本段出口 IP 被东财整体拒绝")

    all_bad = all(not v for v in usable_by_check.values())
    if all_bad:
        print("\n⚠️  结论：当前机器到东财完全不通。工作台会降级到申万源（数据慢一天 T-1）。")
        print("    这是数据源对数据中心 IP 的限流，不是代码错误；换个出口或稍后重试通常就恢复。")
    else:
        print("\n✅ 结论：至少有可用通道，主流程应当能取到当日数据。")


if __name__ == "__main__":
    main()
