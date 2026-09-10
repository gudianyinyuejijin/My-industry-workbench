# -*- coding: utf-8 -*-
"""
概念板块评分与RPS排名（复刻作者 concept.html 风格）
====================================================
数据源：东方财富概念板块（push2delay 免费接口，504个概念板块）
字段：f3当日 / f109五日 / f160十日 / f110二十日 / f24六十日 / f25年初至今 涨跌幅

【概念评分 = 简化版五因子】（镜像行业模型结构：30+20+15+25-10）
说明：概念板块无公开K线源（东财K线接口受限），故以多周期涨幅镜像各因子思想：
  · 趋势结构(30)：多周期同向多头（5/10/20/60/年初至今全>0 ≈ 均线火车轨）
  · 价格强度(20)：20日涨幅分档
  · 趋势方向(15)：5日/10日/当日方向一致
  · 相对强度(25)：60日/20日涨幅在全概念中的百分位（RPS）
  · 过热扣分(10)：短期涨幅过高（5日/20日偏热）
阶段阈值同行业模型：主升浪>80 ｜ 强趋势65-80 ｜ 趋势形成50-65 ｜ 底部反转35-50 ｜ 弱势<35
"""
import json, os
import requests, urllib3

urllib3.disable_warnings()
H = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
BASE = os.path.dirname(os.path.abspath(__file__))
RAW = f"{BASE}/data/concept_raw.json"

# 概念 → 活跃ETF 映射（代码均经东财全量基金库验证存在）
CONCEPT_ETF = {
    "创新药": [("创新药ETF", "159992"), ("创新药ETF易方达", "516080")],
    "CRO": [("医疗ETF", "512170"), ("生物医药ETF", "512290")],
    "创新医疗服务": [("医疗ETF", "512170"), ("医疗ETF国泰", "159828")],
    "精准医疗": [("生物医药ETF", "512290"), ("医疗ETF", "512170")],
    "基因测序": [("生物医药ETF", "512290")],
    "合成生物": [("生物医药ETF", "512290")],
    "生物疫苗": [("生物医药ETF", "512290"), ("创新药ETF", "159992")],
    "中药概念": [("中药ETF", "159647")],
    "AI制药（医疗）": [("创新药ETF", "159992"), ("生物医药ETF", "512290")],
    "减肥药": [("创新药ETF", "159992")],
    "肝炎概念": [("医药ETF", "512010")],
    "猪肉概念": [("养殖ETF", "159865"), ("农业ETF", "159825")],
    "鸡肉概念": [("养殖ETF", "159865")],
    "水产概念": [("农业ETF", "159825")],
    "粮食概念": [("农业ETF", "159825"), ("农业ETF天弘", "512620")],
    "转基因": [("农业ETF", "159825")],
    "农业种植": [("农业ETF", "159825"), ("农业ETF天弘", "512620")],
    "生态农业": [("农业ETF", "159825")],
    "预制菜概念": [("食品饮料ETF", "515170"), ("消费ETF", "159928")],
    "乳业": [("食品饮料ETF", "515170")],
    "调味品概念": [("食品饮料ETF", "515170"), ("消费ETF", "159928")],
    "维生素": [("化工ETF", "159870"), ("医药ETF", "512010")],
    "新消费": [("消费ETF", "159928"), ("消费龙头ETF", "516130")],
    "新零售": [("消费ETF", "159928")],
    "退税商店": [("消费ETF", "159928")],
    "旅游酒店": [("旅游ETF", "159766")],
    "人工智能": [("人工智能ETF易方达", "159819"), ("AI人工智能ETF", "512930")],
    "机器人概念": [("机器人ETF", "562500"), ("机器人ETF天弘", "159770")],
    "机器人执行器": [("机器人ETF", "562500")],
    "光刻胶": [("半导体ETF", "512480"), ("芯片ETF", "159995")],
    "半导体概念": [("半导体ETF", "512480"), ("芯片ETF", "159995")],
    "第三代半导体": [("半导体ETF", "512480")],
    "芯片概念": [("芯片ETF", "159995"), ("半导体ETF", "512480")],
    "存储芯片": [("芯片ETF", "159995"), ("半导体ETF", "512480")],
    "半导体设备": [("半导体设备ETF", "159516")],
    "AI语料": [("大数据ETF", "515400"), ("云计算ETF", "516510")],
    "大数据概念": [("大数据ETF", "515400")],
    "云计算": [("云计算ETF", "516510")],
    "信创": [("信创ETF", "159537"), ("软件ETF", "515230")],
    "国产软件": [("软件ETF", "515230"), ("软件ETF嘉实", "159852")],
    "网络安全": [("信创ETF", "159537")],
    "数字经济": [("大数据ETF", "515400")],
    "5G": [("通信ETF", "515880"), ("5G通信ETF", "515050")],
    "6G概念": [("通信ETF", "515880")],
    "物联网": [("物联网ETF", "516380")],
    "智能汽车": [("智能汽车ETF", "159795"), ("汽车ETF", "516110")],
    "无人驾驶": [("智能汽车ETF", "159795")],
    "新能源车": [("新能源车ETF", "515030"), ("电池ETF", "159755")],
    "固态电池": [("电池ETF", "159755"), ("储能电池ETF", "159566")],
    "储能": [("储能电池ETF", "159566"), ("电池ETF", "159755")],
    "光伏概念": [("光伏ETF", "515790")],
    "氢能源": [("新能源车ETF", "515030")],
    "核电": [("电力ETF", "159611")],
    "智能电网": [("电网设备ETF", "159326")],
    "特高压": [("电网设备ETF", "159326")],
    "军工": [("军工ETF", "512660"), ("军工龙头ETF", "512710")],
    "大飞机": [("军工ETF", "512660")],
    "航天概念": [("军工ETF", "512660"), ("军工龙头ETF", "512710")],
    "黄金概念": [("黄金股ETF", "517400"), ("黄金ETF", "518880")],
    "有色金属": [("有色金属ETF", "512400"), ("有色ETF", "159980")],
    "稀土永磁": [("稀土ETF", "159713")],
    "小金属概念": [("有色金属ETF", "512400")],
    "煤炭概念": [("煤炭ETF", "515220")],
    "电力概念": [("电力ETF", "159611")],
    "白酒": [("酒ETF", "512690")],
    "券商概念": [("券商ETF", "512000"), ("证券ETF", "512880")],
    "银行": [("银行ETF", "512800"), ("银行ETF华夏", "515020")],
    "保险": [("证券保险ETF", "515630")],
    "互联网金融": [("金融科技ETF", "159851")],
    "影视概念": [("影视ETF", "516620"), ("传媒ETF", "512980")],
    "游戏": [("游戏ETF", "516010"), ("传媒ETF", "512980")],
    "元宇宙概念": [("传媒ETF", "512980")],
    "跨境电商": [("物流ETF", "516530")],
    "宠物经济": [("消费ETF", "159928")],
    "医美概念": [("消费ETF", "159928")],
    "养老概念": [("消费ETF", "159928")],
    "白酒概念": [("酒ETF", "512690")],
    "超超临界发电": [("电力ETF", "159611")],
    "虚拟电厂": [("电力ETF", "159611")],
    "汽车零部件": [("汽车零部件ETF", "159565")],
    "工业母机": [("工业母机ETF", "159667"), ("机械ETF", "159886")],
    "高端装备": [("高端装备ETF", "516320")],
    "工业互联网": [("工业母机ETF", "159667")],
    "3D打印": [("高端装备ETF", "516320")],
    "卫星导航": [("军工ETF", "512660")],
    "北斗导航": [("军工ETF", "512660")],
    "数据要素": [("大数据ETF", "515400")],
    "东数西算": [("云计算ETF", "516510")],
    "算力概念": [("云计算ETF", "516510"), ("大数据ETF", "515400")],
    "CPO概念": [("通信ETF", "515880")],
    "铜缆高速连接": [("通信ETF", "515880")],
    "低空经济": [("高端装备ETF", "516320")],
    "智能家居": [("家电ETF", "159996")],
    "汽车拆解": [("环保ETF", "512580")],
    "海洋经济": [("环保ETF", "512580")],
    "土壤修复": [("环保ETF", "512580")],
    "PM2.5": [("环保ETF", "512580")],
    "共享经济": [("消费ETF", "159928")],
    "超级品牌": [("消费ETF", "159928"), ("消费龙头ETF", "516130")],
    "生物柴油": [("化工ETF", "159870")],
    "化工原料": [("化工ETF", "159870"), ("能源化工ETF", "159981")],
    "新材料概念": [("化工ETF", "159870")],
    "碳纤维": [("化工ETF", "159870")],
    "医疗美容": [("消费ETF", "159928")],
}


def fetch_concepts():
    """抓取全部概念板块（东财delay接口）"""
    all_rows = []
    for pn in range(1, 8):
        try:
            r = requests.get("https://push2delay.eastmoney.com/api/qt/clist/get",
                             params={"pn": pn, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                                     "fid": "f24", "fs": "m:90+t:3",
                                     "fields": "f12,f14,f3,f5,f6,f8,f24,f25,f109,f110,f160,f62,f104,f105,f106,f128,f140,f136"},
                             headers=H, timeout=15)
            diff = (r.json().get("data") or {}).get("diff") or []
            if not diff:
                break
            all_rows.extend(diff)
        except Exception:
            break
    return all_rows


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _score(row, rps60, rps20):
    """简化五因子评分（镜像行业模型结构）"""
    d1, d5, d10, d20, d60, dytd = row
    # 1) 趋势结构 30 —— 多周期多头（火车轨思想）
    s1 = 0
    if d5 and d5 > 0: s1 += 5
    if d10 and d10 > 0: s1 += 6
    if d20 and d20 > 0: s1 += 8
    if d60 and d60 > 0: s1 += 8
    if dytd and dytd > 0: s1 += 3
    s1 = min(30, s1)
    # 2) 价格强度 20 —— 20日涨幅分档
    s2 = 0
    if d20 is not None:
        if d20 >= 30: s2 = 20
        elif d20 >= 20: s2 = 17
        elif d20 >= 10: s2 = 13
        elif d20 >= 5: s2 = 9
        elif d20 >= 0: s2 = 5
        elif d20 >= -5: s2 = 2
    # 3) 趋势方向 15 —— 短周期同向
    s3 = 0
    if d5 and d5 > 0 and d10 and d10 > 0: s3 += 9
    elif d5 and d5 > 0: s3 += 5
    elif d10 and d10 > 0: s3 += 3
    if d1 and d1 > 0: s3 += 4
    elif d1 and d1 > -2: s3 += 2
    s3 = min(15, s3)
    # 4) 相对强度 25 —— RPS百分位（超额思想）
    s4 = 0
    if rps60 is not None:
        if rps60 >= 95: s4 += 20
        elif rps60 >= 90: s4 += 17
        elif rps60 >= 80: s4 += 13
        elif rps60 >= 70: s4 += 10
        elif rps60 >= 60: s4 += 7
        elif rps60 >= 50: s4 += 4
        else: s4 += 1
    if rps20 is not None:
        if rps20 >= 90: s4 += 5
        elif rps20 >= 75: s4 += 3
        elif rps20 >= 50: s4 += 1
    s4 = min(25, s4)
    # 5) 过热扣分 10 —— 短期涨幅过热
    s5 = 0
    if (d5 is not None and d5 >= 20) or (d20 is not None and d20 >= 60): s5 = 10
    elif (d5 is not None and d5 >= 15) or (d20 is not None and d20 >= 40): s5 = 8
    elif (d5 is not None and d5 >= 10) or (d20 is not None and d20 >= 30): s5 = 5
    elif d5 is not None and d5 >= 8: s5 = 3
    return s1, s2, s3, s4, s5, max(0, round(s1 + s2 + s3 + s4 - s5, 1))


def status_of(s):
    if s > 80: return "主升浪"
    if s >= 65: return "强趋势"
    if s >= 50: return "趋势形成"
    if s >= 35: return "底部反转"
    return "弱势"


def run(use_cache=True):
    """返回概念板块列表（评分+RPS+ETF）"""
    rows = None
    if use_cache and os.path.exists(RAW):
        try:
            import pandas as pd
            ts = os.path.getmtime(RAW)
            if pd.Timestamp.now().timestamp() - ts < 12 * 3600:  # 12小时缓存
                rows = json.load(open(RAW, encoding="utf-8"))
        except Exception:
            rows = None
    if not rows:
        rows = fetch_concepts()
        if rows:
            json.dump(rows, open(RAW, "w", encoding="utf-8"), ensure_ascii=False)
    if not rows:
        return []

    # RPS百分位（全概念内）
    import pandas as pd
    df = pd.DataFrame([{
        "code": r["f12"], "name": r["f14"],
        "d1": _f(r.get("f3")), "d5": _f(r.get("f109")), "d10": _f(r.get("f160")),
        "d20": _f(r.get("f110")), "d60": _f(r.get("f24")), "dytd": _f(r.get("f25")),
        "amount": _f(r.get("f6")), "lead": r.get("f128"), "leadcode": r.get("f140"),
        "leadchg": _f(r.get("f136")), "up": r.get("f104"), "down": r.get("f106"),
    } for r in rows])
    for col, out in [("d1", "rps1"), ("d5", "rps5"), ("d10", "rps10"),
                     ("d20", "rps20"), ("d60", "rps60"), ("dytd", "rpsytd")]:
        df[out] = (df[col].rank(pct=True) * 100).round(0)

    out_rows = []
    for _, r in df.iterrows():
        s1, s2, s3, s4, s5, total = _score(
            (r["d1"], r["d5"], r["d10"], r["d20"], r["d60"], r["dytd"]),
            r["rps60"], r["rps20"])
        etfs = CONCEPT_ETF.get(r["name"], [])
        out_rows.append({
            "code": r["code"], "name": r["name"], "score": total,
            "status": status_of(total),
            "factors": {"结构": s1, "价格": s2, "方向": s3, "相对": s4, "过热": -s5},
            "chg1": r["d1"], "chg5": r["d5"], "chg10": r["d10"], "chg20": r["d20"],
            "chg60": r["d60"], "chgytd": r["dytd"],
            "rps1": r["rps1"], "rps5": r["rps5"], "rps10": r["rps10"],
            "rps20": r["rps20"], "rps60": r["rps60"], "rpsytd": r["rpsytd"],
            "amount": r["amount"], "lead": r["lead"], "leadcode": r["leadcode"],
            "leadchg": r["leadchg"], "up": r["up"], "down": r["down"],
            "etfs": [{"name": n, "code": c} for n, c in etfs],
        })
    out_rows.sort(key=lambda x: -x["score"])
    for i, r in enumerate(out_rows, 1):
        r["rank"] = i
    return out_rows


if __name__ == "__main__":
    rows = run()
    print(f"概念板块 {len(rows)} 个")
    sc = {}
    for r in rows:
        sc[r["status"]] = sc.get(r["status"], 0) + 1
    print("状态分布:", sc)
    print("\nTOP15:")
    for r in rows[:15]:
        print(f"{r['rank']:3d} {r['name']:14s} {r['score']:5.1f} {r['status']} "
              f"5日{r['chg5'] or 0:+.1f}% 20日{r['chg20'] or 0:+.1f}% 60日{r['chg60'] or 0:+.1f}% "
              f"RPS60={r['rps60']:.0f} ETF:{len(r['etfs'])}")
