#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate.py — 行业主升浪工作台每日构建入口（GitHub Actions 主入口）

职责：
  1. 调用 workbench_data.build() 计算当日完整 payload（行业评分+RPS+二八+概念+结论），
     并把 payload 写入 data/workbench.json（供 Pages 同时作为静态资产暴露）。
  2. 读取 template.html，把内联数据占位符 __DATA_JSON__ / __TAKEAWAY_JSON__
     替换为当日 payload / 当日生成的关键结论数组，输出自包含的 index.html
     （单文件、全部 CSS/JS/数据内联、浏览器直接打开即可、绝不依赖 /api/data）。

运行方式：
  python3 generate.py           # 复用 data/sw_industry.pkl 种子 + 网络增量
  python3 generate.py --force   # 强制全量重抓（耗时长、易被限流，仅用于回填）

退出码：0=成功；非0=失败，stderr 打印可定位的错误。
"""
import json
import os
import sys
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(BASE, "template.html")
OUT_HTML = os.path.join(BASE, "index.html")


def _safe_js_json(obj):
    """把 Python 对象序列化为可在 <script> 中内联的 JS 字面量。

    关键防护：
      - ensure_ascii=False 保留中文可读；
      - </script> 等会被 HTML 解析器提前关 <script> 的字面量替换为 <\/script，
        防止浏览器读到一半截断；
      - JSON 里 < 字符统一替换为 \\u003c（更稳）。
    """
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    s = s.replace("</", "<\\/")          # 防 </script> 截断
    s = s.replace("<", "\\u003c")        # 把所有 < 转为 \u003c（防 2026-09-07 之类 < 不出现）
    return s


def build_takeaway(payload):
    """基于当日 payload 动态生成关键结论数组（6 条 + 1 条 verdict）。
    与模板原 TAKEAWAY 风格保持一致：基于真实数据点，避免空话。
    """
    sc = payload.get("status_count", {}) or {}
    rows = payload.get("rows", []) or []
    n_main = sc.get("主升浪", 0)
    n_strong = sc.get("强趋势", 0)
    n_forming = sc.get("趋势形成", 0)
    n_turn = sc.get("底部反转", 0)

    # 头部
    tag = "【盘中快照·未收盘】" if payload.get("intraday") else ""
    if rows:
        top1 = rows[0]
        head = (f"{tag}今日TOP1：<b>{top1['name']}</b>"
                f"（{top1['status']}，评分{top1['score']:.1f}，超额60日{top1['excess60']:+.1f}%）。")
    else:
        head = f"{tag}今日暂无评分数据。"

    if n_main > 0:
        mains = [r["name"] for r in rows if r["status"] == "主升浪"][:5]
        line1 = f"今日 <b>{n_main}</b> 个行业进入<b>主升浪</b>（&gt;80）：{' / '.join(mains)}。"
    else:
        line1 = "今日无任何行业进入主升浪（&gt;80），最高仅强趋势。"

    if n_strong > 0:
        strongs = [f"{r['name']}({r['score']:.0f})" for r in rows if r["status"] == "强趋势"][:6]
        line2 = f"强趋势 <b>{n_strong}</b> 个：{' / '.join(strongs)}。"
    else:
        line2 = "无强趋势行业。"

    # 超额 60 日领先者
    sorted_excess = sorted(rows, key=lambda r: r.get("excess60", 0), reverse=True)
    if sorted_excess:
        lead = sorted_excess[0]
        line3 = (
            f"<b>{lead['name']}</b> 以超额60日 <b>+{lead['excess60']:.1f}%</b> 居首"
            f"（RPS60={lead.get('rps60') or '--'}），"
            f"是当下相对强度最高的板块。"
        )
    else:
        line3 = "无超额强度数据。"

    # 底部反转候选
    if n_turn > 0:
        turns = [r["name"] for r in rows if r["status"] == "底部反转"][:3]
        line4 = f"底部反转 <b>{n_turn}</b> 个：{' / '.join(turns)}——可关注右侧突破机会。"
    else:
        line4 = "暂无底部反转信号。"

    # 二八信号 —— 补足实质内容：
    # 原来只拼一句「二系X / 八系Y」，等于把模板宣读一遍，看不出依据。
    # 现在给出每组有多少个指数站在阈值之上、最强/最弱分别多少，
    # 以及两组谁占优（大盘 vs 小盘风格），并如实标出哪些指数没取到。
    erba = payload.get("erba", {}) or {}
    erows = erba.get("rows", []) or []
    sig2 = erba.get("signal2", "数据受限")
    sig8 = erba.get("signal8", "数据受限")
    try:
        thr = float(erba.get("threshold") or 0.0)
    except Exception:
        thr = 0.0

    def _grp(g):
        rs = [r for r in erows
              if r.get("group") == g and r.get("available")
              and isinstance(r.get("ret20"), (int, float))]
        if not rs:
            return None
        rs.sort(key=lambda x: x["ret20"], reverse=True)
        return {
            "n": len(rs),
            "pos": sum(1 for r in rs if r["ret20"] > thr),
            "strong": rs[0], "weak": rs[-1],
            "avg": sum(r["ret20"] for r in rs) / len(rs),
        }

    s2, s8 = _grp("二"), _grp("八")
    parts = []
    for tag, st, sig in (("二系", s2, sig2), ("八系", s8, sig8)):
        if not st:
            parts.append(f"{tag}数据受限")
            continue
        parts.append(
            f"{tag}{st['n']}个指数里 <b>{st['pos']}</b> 个20日涨幅过阈值，"
            f"最强 <b>{st['strong']['name']} {st['strong']['ret20']:+.1f}%</b> / "
            f"最弱 {st['weak']['name']} {st['weak']['ret20']:+.1f}%（均值{st['avg']:+.1f}%）"
            f"→「<b>{sig}</b>」"
        )

    line5 = f"二八择时（阈值{thr:g}%）：" + "；".join(parts) + "。"

    if s2 and s8:
        gap = s8["avg"] - s2["avg"]
        if gap >= 2:
            line5 += f" 小盘跑赢大盘 {gap:.1f} 个百分点，风格偏八。"
        elif gap <= -2:
            line5 += f" 大盘跑赢小盘 {abs(gap):.1f} 个百分点，风格偏二。"
        else:
            line5 += " 两系差距不大，风格均衡。"

    missing = [r.get("name") for r in erows if not r.get("available")]
    if missing:
        line5 += f"（未取到：{'/'.join(missing)}，其余指数照常参与判断）"

    # 概念头部
    cs = payload.get("concepts", []) or []
    if cs:
        c_main = [c["name"] for c in cs if c["status"] == "主升浪"][:3]
        c_strong = [c["name"] for c in cs if c["status"] == "强趋势"][:3]
        c_parts = []
        if c_main:
            c_parts.append(f"主升浪概念：{' / '.join(c_main)}")
        if c_strong:
            c_parts.append(f"强趋势概念：{' / '.join(c_strong)}")
        line6 = f"概念板块（{len(cs)} 个）：{'；'.join(c_parts) or '暂无强信号'}。"
    else:
        line6 = "概念板块暂无数据。"

    # 一句话总结（verdict）
    if n_main >= 3:
        verdict = f"市场情绪偏多——{n_main} 个行业进入主升浪，可关注头部主线的右侧机会。"
    elif n_main >= 1:
        verdict = f"结构性机会——少数行业主升浪，关注 {rows[0]['name'] if rows else '头部'} 等主线。"
    elif n_strong >= 5:
        verdict = "强趋势聚集，<b>暂无主升浪</b>但资金已开始聚焦少数方向，防御+精选为上。"
    elif n_strong >= 1:
        verdict = "市场无主升浪、最高仅强趋势，资金避高就低、聚焦防御与少数超额强者，风格整体偏防御。"
    else:
        verdict = "市场整体弱势，无明确主线信号，建议降低仓位观望。"

    return [head, line1, line2, line3, line4, line5, line6], verdict


def main():
    force = "--force" in sys.argv
    print(f"[generate] start (force={force})")
    sys.path.insert(0, BASE)
    import workbench_data  # noqa: E402

    # a. 计算 payload + 写 workbench.json
    payload = workbench_data.build(force=force)

    # b. 读模板
    if not os.path.exists(TEMPLATE):
        sys.stderr.write(f"[generate] template not found: {TEMPLATE}\n")
        return 1
    with open(TEMPLATE, "r", encoding="utf-8") as f:
        tpl = f.read()

    if "__DATA_JSON__" not in tpl or "__TAKEAWAY_JSON__" not in tpl:
        sys.stderr.write("[generate] template missing __DATA_JSON__ / __TAKEAWAY_JSON__\n")
        return 1

    # c. 注入数据 + TAKEAWAY
    data_js = _safe_js_json(payload)
    takeaway_list, _verdict = build_takeaway(payload)
    takeaway_js = _safe_js_json(takeaway_list)

    html = tpl.replace("__DATA_JSON__", data_js, 1)
    html = html.replace("__TAKEAWAY_JSON__", takeaway_js, 1)

    if "__DATA_JSON__" in html or "__TAKEAWAY_JSON__" in html:
        sys.stderr.write("[generate] placeholders not fully replaced\n")
        return 1

    # d. 输出
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    # e. summary
    sc = payload.get("status_count", {})
    size = os.path.getsize(OUT_HTML)
    print(
        f"[generate] OK · trade_date={payload.get('trade_date')} · "
        f"行业{payload.get('total')}/RPS{len(payload.get('rps', []))}/二八{len(payload.get('erba', {}).get('rows', []))}/概念{len(payload.get('concepts', []))} · "
        f"主升浪{sc.get('主升浪', 0)} 强趋势{sc.get('强趋势', 0)} 趋势形成{sc.get('趋势形成', 0)} "
        f"底部反转{sc.get('底部反转', 0)} 弱势{sc.get('弱势', 0)} · "
        f"index.html={size}B"
    )
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except SystemExit as e:
        raise
    except Exception:
        traceback.print_exc()
        sys.stderr.write("\n[generate] FAILED\n")
        sys.exit(1)
    sys.exit(rc)
