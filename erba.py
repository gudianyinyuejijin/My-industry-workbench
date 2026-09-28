# -*- coding: utf-8 -*-
"""二八指数 · 进出场状态（复刻作者模块）
逻辑：选"二"(大盘/价值) 与 "八"(小盘/成长) 指数对，设阈值（默认0），20日涨幅判断：
  至少1个 > 阈值  → 进场/持有/满仓
  两者 都 < 阈值  → 清仓/空仓
数据源（主）：东方财富 push2delay ulist.np（f110=20日涨跌幅）
数据源（备1）：东方财富 K 线（同一 secid，自己算 20 日涨幅）—— 最稳，接口路径不同
数据源（备2）：腾讯/新浪日K
2026-09-24 修正：
  - 中证2000 的东财 secid 是 2.932000（市场前缀 2，不是 1/0），之前填 None 导致
    白白缺失。现在能正常取数。
  - 微盘股(万得8841431) 是万得专有指数，东财/腾讯/新浪都没有，确实拿不到。
    与其留一条永远 null 的占位（会让 signal8 一直显示"部分数据缺失"），
    不如移除——中证2000 本身就是 A股最小市值 2000 只的代表，已覆盖微盘敞口。
2026-09-25 修正（v6.5）：盘中/个别时段东财 ulist.np 对部分指数只返回一条记录、
  但 f110（20日涨幅）字段是空的（None 或 '-'）。旧代码只在「整条记录都没有」
  时才走备源，于是这种"有记录、没数值"的情况直接判缺失。
  现在改为：只要 20 日涨幅拿不到数字，就依次走 备1(东财K线) → 备2(腾讯/新浪)。
  同时修掉 _fallback_kline 里新浪那条路永远走不到的老 bug，
  并补全中证2000 / 北证50 的腾讯、新浪代码（原来全是 None，备源形同虚设）。
"""
import requests, urllib3, json, re
from datetime import datetime, timezone, timedelta
urllib3.disable_warnings()
H = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'}

# 名称 -> (东财secid, 腾讯代码, 新浪代码, 二八类别)
INDICES = [
    ("上证50",   "1.000016",  "sh000016", "sh000016", "二"),
    ("沪深300",  "1.000300",  "sh000300", "sh000300", "二"),
    ("中证A100", "1.000903",  "sh000903", "sh000903", "二"),
    ("中证2000", "2.932000",  "sh932000", "sh932000", "八"),   # 中证指数，行情挂上交所 932000
    ("国证2000", "0.399303",  "sz399303", "sz399303", "八"),
    ("中证1000", "1.000852",  "sh000852", "sh000852", "八"),
    ("中证500",  "1.000905",  "sh000905", "sh000905", "八"),
    ("北证50",   "0.899050",  "bj899050", "bj899050", "八"),   # 北交所，新浪/腾讯用 bj 前缀
    ("创业板指", "0.399006",  "sz399006", "sz399006", "八"),
    ("科创50",   "1.000688",  "sh000688", "sh000688", "八"),
]
THRESHOLD = 0.0   # 阈值（可调为0.5%/1%等）

# 东财 K 线主机（push2his 在部分网络下不可达，push2delay 兜底）
EM_KLINE_HOSTS = ["https://push2his.eastmoney.com",
                  "https://1.push2his.eastmoney.com",
                  "https://7.push2his.eastmoney.com",
                  "https://push2delay.eastmoney.com"]


def _em_query(secids, timeout=12):
    """东财 ulist.np 查询（f3当日/f109五日/f160十日/f110二十日/f24六十日/f124时间戳）"""
    try:
        r = requests.get("https://push2delay.eastmoney.com/api/qt/ulist.np/get",
                         params={"fltt": 2, "secids": secids, "np": 1,
                                 "fields": "f12,f14,f3,f109,f160,f110,f24,f124"},
                         headers=H, timeout=timeout)
        diff = (r.json().get("data") or {}).get("diff") or []
        return {row["f12"]: row for row in diff if row.get("f12")}
    except Exception:
        return {}


def _eastmoney():
    """先批量拉一次；批量里漏掉的，再逐个补一次。

    为什么要补：多个市场前缀（1.上交所 / 0.深交所 / 2.中证）混在一次批量请求里，
    偶尔会漏掉其中某个（中证2000 的 2.932000 就属于这种情况）。单独请求是稳的，
    所以用它兜底，避免整块数据凭空缺失。"""
    secids = [x[1] for x in INDICES if x[1]]
    em = _em_query(",".join(secids))
    for s in secids:
        code = s.split(".")[-1]
        if code in em:
            continue
        one = _em_query(s, timeout=10)
        if one:
            em.update(one)
    return em


def _em_kline(secid, lmt=25, timeout=10):
    """东财日K（前复权）。返回 klines 列表或 None"""
    params = {"secid": secid, "fields1": "f1,f2,f3,f4,f5,f6",
              "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
              "klt": 101, "fqt": 1, "end": "20500101", "lmt": lmt}
    for host in EM_KLINE_HOSTS:
        try:
            r = requests.get(host + "/api/qt/stock/kline/get",
                             params=params, headers=H, timeout=timeout)
            k = ((r.json().get("data") or {}).get("klines") or [])
            if len(k) >= 21:
                return k
        except Exception:
            continue
    return None


def _em_kline_ret20(secid):
    """备源1：东财日K 自己算 20 日涨幅。返回 (ret20, date)"""
    k = _em_kline(secid)
    if not k:
        return None, None
    try:
        c = [float(x.split(",")[2]) for x in k]      # f53 = 收盘
        date = k[-1].split(",")[0]
        if c[-20] == 0:
            return None, None
        return round((c[-1] / c[-20] - 1) * 100, 1), date
    except Exception:
        return None, None


def _tx_kline(code, cnt=30):
    try:
        r = requests.get(f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{cnt},qfq',
                         headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        d = r.json().get('data', {}).get(code, {})
        return d.get('qfqday') or d.get('day')
    except Exception:
        return None


def _sina_kline(symbol, scale=240, datalen=30):
    try:
        url = 'https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData'
        r = requests.get(url, params={'symbol': symbol, 'scale': scale, 'ma': 'no', 'datalen': datalen},
                         headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        t = re.sub(r'^[^=]*=|;$', '', r.text.strip()).strip('()')
        return json.loads(t)
    except Exception:
        return None


def _is_num(x):
    """东财缺字段时给的是 None 或字符串 '-'，都算没数"""
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _fallback_kline(tx_c, sina_c):
    """备源2：腾讯/新浪日K，自己算 20 日涨幅。返回 (ret20, date)

    注意：两轮必须分别走腾讯和新浪。旧写法里第二轮会因为代码值相同而重复走腾讯，
    新浪那条路永远走不到，这里改成显式按 kind 区分。"""
    for kind, cand in (("tx", tx_c), ("sina", sina_c)):
        if not cand:
            continue
        try:
            k = _tx_kline(cand) if kind == "tx" else _sina_kline(cand)
        except Exception:
            k = None
        if not k or len(k) < 21:
            continue
        try:
            if isinstance(k[0], list):
                c = [float(x[2]) for x in k]
                date = k[-1][0]
            else:
                c = [float(x["close"]) for x in k]
                date = k[-1].get("day")
            if c[-20] == 0:
                continue
            return round((c[-1] / c[-20] - 1) * 100, 1), date
        except Exception:
            continue
    return None, None


def run():
    em = _eastmoney()
    rows = []
    for name, secid, tx_c, sina_c, grp in INDICES:
        v20 = v1 = v5 = v60 = None
        date = None
        src = None
        code = (secid or "").split(".")[-1] if secid else ""

        # ── 主源：东财 ulist.np ──
        row = em.get(code) if code else None
        if row:
            v1 = row.get("f3"); v5 = row.get("f109"); v20 = row.get("f110"); v60 = row.get("f24")
            ts = row.get("f124")
            if ts:
                try:
                    date = datetime.fromtimestamp(
                        int(ts), timezone(timedelta(hours=8))).date().isoformat()
                except Exception:
                    date = None
            if _is_num(v20):
                src = "em"

        # ── 备源1：东财日K 自算（有记录但 f110 为空时，这是最可靠的一层）──
        if not _is_num(v20) and secid:
            a, b = _em_kline_ret20(secid)
            if a is not None:
                v20 = a
                date = date or b
                src = "em_kline"

        # ── 备源2：腾讯 / 新浪日K ──
        if not _is_num(v20) and (tx_c or sina_c):
            a, b = _fallback_kline(tx_c, sina_c)
            if a is not None:
                v20 = a
                date = date or b
                src = "tx/sina"

        ok = _is_num(v20)
        if not ok:
            print(f"[erba] 缺数 {name}({code})：主源字段={row.get('f110') if row else '无记录'}，"
                  f"K线备源={'失败' if secid else '无secid'}，三方备源={tx_c or sina_c or '无'}")

        rows.append({
            "name": name, "code": code or (tx_c or sina_c or ""), "group": grp,
            "chg1": None if v1 in (None, "-") else v1,
            "chg5": None if v5 in (None, "-") else v5,
            "ret20": None if v20 in (None, "-") else (round(v20, 1) if isinstance(v20, (int, float)) else v20),
            "chg60": None if v60 in (None, "-") else v60,
            "date": date, "available": ok, "src": src,
        })
    return rows


def signal(rows, group, threshold=THRESHOLD):
    grp_rows = [r for r in rows if r['group'] == group]
    vals = [r['ret20'] for r in grp_rows if r['available'] and isinstance(r['ret20'], (int, float))]
    missing = any(not r['available'] for r in grp_rows)
    if not vals:
        return "数据受限"
    any_pos = any(v > threshold for v in vals)
    all_neg = all(v < threshold for v in vals)
    if any_pos:
        base = "进场/持有/满仓"
    elif all_neg:
        base = "清仓/空仓"
    else:
        base = "分化/中性"
    if missing:
        return "部分数据缺失·" + base
    return base


if __name__ == "__main__":
    rows = run()
    print(f"{'名称':10s} {'类别':4s} {'当日':>7s} {'5日':>7s} {'20日':>7s} {'60日':>7s}  来源")
    for r in rows:
        def f(x): return f"{x:+.1f}%" if isinstance(x, (int, float)) else "受限"
        print(f"{r['name']:10s} {r['group']:4s} {f(r['chg1']):>7s} {f(r['chg5']):>7s} "
              f"{f(r['ret20']):>7s} {f(r['chg60']):>7s}  {r.get('src') or '-'}")
    print("\n—— 二八进出场信号（阈值0）——")
    for grp in ["二", "八"]:
        print(f"【{grp}系】{signal(rows, grp)}")
