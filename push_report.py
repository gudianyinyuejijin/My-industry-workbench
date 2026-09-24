# -*- coding: utf-8 -*-
"""
每日战报推送（在 GitHub Actions 里运行，零积分、免费）
==================================================
功能：读取 data/workbench.json（当日最新评分），生成「排名 / 行业 / 评分 / 活跃ETF」
      四个板块（主升浪 / 强趋势 / TOP20 / 趋势形成）+ 今日关键结论 Take-Away，
      以清晰的 Markdown 排版（标题加粗、条目分行、板块分隔线）推送到你指定的渠道。

排版说明（Server酱支持 Markdown）：
  - 每个板块用 ## 标题 + --- 分隔线隔开
  - 每个行业独立两行：第一行「排名. 行业 · 评分」加粗，第二行 ETF
  - 条目之间空一行，避免拥挤
  - 关键结论（Take-Away）放最前面，打开即读

推送渠道（在 GitHub 仓库 Secrets 里配置任意一个即可，脚本会自动识别）：
  1) Server酱（微信推送，推荐）  → Secrets 名：SERVERCHAN_KEY  值：你的 SendKey
  2) PushPlus（微信推送）        → Secrets 名：PUSHPLUS_TOKEN  值：你的 token
  3) Bark（iPhone 推送）         → Secrets 名：BARK_KEY        值：你的设备 key
  4) 邮件（SMTP）                → Secrets：EMAIL_HOST/EMAIL_PORT/EMAIL_USER/EMAIL_PASS/EMAIL_TO

不配置任何 Secrets 时，脚本只打印战报不推送（用于本地预览，安全无害）。
"""
import os
import sys
import json
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data", "workbench.json")

SEP = "\n\n---\n\n"          # 板块分隔线（前后必须空行，否则 Markdown 解析异常）


def load():
    with open(DATA, encoding="utf-8") as f:
        return json.load(f)


def fingerprint(obj):
    """数据指纹 = 交易日 + TOP20 的「行业名:整数分」

    为什么不能只比 trade_date：中午那次经常「日期没变、但数据已经被修正」——
    例如昨天下午是用『申万 T-2 + 东财推算 T-1』算的，今天中午申万把 T-1 的
    真实数据发出来了，重算后分数会变，但 trade_date 仍然是 T-1，
    只比日期就会误判成「没变化」而漏推。所以把名次和分数一起纳进来比。
    """
    if not obj:
        return ""
    td = str(obj.get("trade_date") or "")
    # 盘中快照(I) 与 收盘定稿(C) 算不同指纹：即使分数碰巧一样，
    # 收盘后也要再推一次定稿版（否则用户拿到的始终是中午那份未收盘的数据）
    intra = "I" if obj.get("intraday") else "C"
    parts = []
    for r in (obj.get("rows") or [])[:20]:
        try:
            sc = round(float(r.get("score") or 0))
        except Exception:
            sc = 0
        parts.append(f"{r.get('name')}:{sc}")
    return f"{td}|{intra}|" + "|".join(parts)


def last_fingerprint():
    """上一次已推送的指纹：取 git HEAD 里那份 workbench.json
    （每次跑完都会 commit 回去，所以 HEAD 就是上次成功的结果）"""
    try:
        import subprocess
        txt = subprocess.check_output(
            ["git", "show", "HEAD:data/workbench.json"],
            cwd=BASE, stderr=subprocess.DEVNULL)
        return fingerprint(json.loads(txt.decode("utf-8")))
    except Exception:
        return ""      # 取不到（首次运行）→ 当作有变化，正常推


def md(s):
    """HTML 标签转 Markdown（build_takeaway 输出带 <b>，Server酱用 ** 加粗）"""
    return (s.replace("<b>", "**").replace("</b>", "**")
             .replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&"))


def bj_time(utc_str):
    """runner 记录的 updated_at 是 UTC，+8 转成北京时间（避免误以为脚本没跑）"""
    try:
        t = datetime.strptime(str(utc_str), "%Y-%m-%d %H:%M:%S") + timedelta(hours=8)
        return t.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(utc_str) if utc_str else ""


def fmt_etfs(etfs, limit=3):
    """活跃ETF：最多显示 limit 只，用 / 分隔"""
    if not etfs:
        return "—"
    names = [e.get("name", "") for e in etfs if e.get("name")]
    return " / ".join(names[:limit]) if names else "—"


def block(title, rows, empty_note="今日无"):
    """生成一个板块：## 标题 + 每条两行（行业行 + ETF行），条间空行"""
    n = len(rows)
    out = [f"## {title}（{n}个）"]
    if not rows:
        out.append(f"> {empty_note}")
        return "\n\n".join(out)
    for r in rows:
        rank = r.get("rank", "-")
        name = r.get("name", "-")
        score = r.get("score", 0)
        etf = fmt_etfs(r.get("etfs"))
        out.append(f"**{rank}. {name}** · {score:.1f}分\n\nETF：{etf}")
    return "\n\n".join(out)


def build_takeaway_md(d):
    """复用网页版 generate.build_takeaway()，转 Markdown 放在战报最前"""
    try:
        sys.path.insert(0, BASE)
        from generate import build_takeaway
        take_list, verdict = build_takeaway(d)
        lines = [f"{i}. {md(t)}" for i, t in enumerate(take_list, 1)]
        lines.append(f"💡 **一句话**：{md(verdict)}")
        return "## 💡 今日关键结论\n\n" + "\n\n".join(lines)
    except Exception as e:
        return f"## 💡 今日关键结论\n\n> 生成失败：{e}"


def build_report(d):
    """生成完整战报文本（Markdown 排版）"""
    rows = d.get("rows") or []
    tdate = d.get("trade_date", "-")
    sc = d.get("status_count") or {}

    main = [r for r in rows if r.get("status") == "主升浪"]
    strong = [r for r in rows if r.get("status") == "强趋势"]
    forming = [r for r in rows if r.get("status") == "趋势形成"]
    top20 = rows[:20]

    erba = d.get("erba") or {}
    sig2 = erba.get("signal2", "-")
    sig8 = erba.get("signal8", "-")

    # ---- 头部 ----
    head = [
        "## 📊 行业主升浪 · 每日战报",
        f"**📅 数据日期：{tdate}**",
    ]
    # 盘中快照醒目标注：当日未收盘，数值还会变，收盘后那次会覆盖为定稿版
    if d.get("intraday"):
        head.append("⏱️ **盘中快照（当日未收盘，数值还会变）**")
        head.append("收盘后将自动刷新为收盘定稿版，届时会再推一条。")
    # 数据源异常提示（倒退保护触发时 workbench.json 带 note 字段）
    if d.get("note"):
        head.append(f"⚠️ **{d['note']}**")
    # 本次更新时间（北京时间）——让人一眼分清"脚本没跑"还是"数据源没给新数据"
    ua_bj = bj_time(d.get("updated_at", ""))
    if ua_bj:
        head.append(f"🕒 更新于：{ua_bj}（北京时间）")
    run_day = ua_bj[:10] if ua_bj else ""
    if run_day and tdate and str(tdate) < run_day:
        head.append(f"⏳ 申万最新发布只到 **{tdate}**（数据源滞后，非故障）")
    head += [
        f"覆盖 **{d.get('total', 0)}** 个行业",
        (f"主升浪 **{sc.get('主升浪', 0)}** ｜ 强趋势 **{sc.get('强趋势', 0)}** "
         f"｜ 趋势形成 **{sc.get('趋势形成', 0)}**"),
        (f"底部反转 **{sc.get('底部反转', 0)}** ｜ 弱势 **{sc.get('弱势', 0)}**"),
        f"二八信号：二系「**{sig2}**」 ／ 八系「**{sig8}**」",
    ]

    # ---- 板块 ----
    parts = [
        "\n\n".join(head),
        build_takeaway_md(d),
        block("🔥 主升浪", main, "今日无行业进入主升浪（>80分）"),
        block("⚡ 强趋势", strong),
        block("🏆 TOP 20", top20),
        block("🌱 趋势形成", forming),
    ]

    # ---- 页脚 ----
    foot = [
        "## ℹ️ 说明",
        "每日 **11:35 / 17:00** 自动生成",
        "数据来源：申万宏源 / 东方财富",
        "仅供研究参考，不构成投资建议",
    ]

    return SEP.join(parts) + SEP + "\n\n".join(foot)


# ---------------- 各推送渠道 ----------------
def _post(url, data, headers=None):
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(data).encode(),
        headers=headers or {"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "ignore")[:200]


def send(title, content):
    """按环境变量配置的渠道推送；未配置则只打印"""
    # 1) Server酱
    key = os.environ.get("SERVERCHAN_KEY", "").strip()
    if key:
        try:
            res = _post(f"https://sctapi.ftqq.com/{key}.send",
                        {"title": title, "desp": content})
            print(f"[push] Server酱 推送成功: {res[:80]}")
            return "serverchan"
        except Exception as e:
            print(f"[push] Server酱 失败: {str(e)[:120]}")

    # 2) PushPlus
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if token:
        try:
            res = _post("https://www.pushplus.plus/send",
                        {"token": token, "title": title, "content": content, "template": "txt"})
            print(f"[push] PushPlus 推送成功: {res[:80]}")
            return "pushplus"
        except Exception as e:
            print(f"[push] PushPlus 失败: {str(e)[:120]}")

    # 3) Bark (iPhone)
    bark = os.environ.get("BARK_KEY", "").strip()
    if bark:
        try:
            res = _post(f"https://api.day.app/{bark}",
                        {"title": title, "body": content})
            print(f"[push] Bark 推送成功: {res[:80]}")
            return "bark"
        except Exception as e:
            print(f"[push] Bark 失败: {str(e)[:120]}")

    # 4) 邮件
    host = os.environ.get("EMAIL_HOST", "").strip()
    if host:
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.header import Header
            port = int(os.environ.get("EMAIL_PORT", "465"))
            user = os.environ["EMAIL_USER"]
            pwd = os.environ["EMAIL_PASS"]
            to = os.environ["EMAIL_TO"]
            msg = MIMEText(content, "plain", "utf-8")
            msg["Subject"] = Header(title, "utf-8")
            msg["From"] = user
            msg["To"] = to
            if port == 465:
                s = smtplib.SMTP_SSL(host, port)
            else:
                s = smtplib.SMTP(host, port)
                s.starttls()
            s.login(user, pwd)
            s.sendmail(user, [to], msg.as_string())
            s.quit()
            print("[push] 邮件 推送成功")
            return "email"
        except Exception as e:
            print(f"[push] 邮件 失败: {str(e)[:120]}")

    print("[push] 未配置任何推送渠道（Secrets），仅本地预览，不推送。")
    return "none"


def main():
    d = load()

    # —— 是否值得推送：数据有实质变化才推，避免一天收到好几条一模一样的 ——
    cur = fingerprint(d)
    prev = last_fingerprint()
    force = os.environ.get("FORCE_PUSH") == "1"
    if not force and cur and prev and cur == prev:
        print(f"[push] 数据无实质变化（交易日与 TOP20 名次/分数均与上次一致），跳过推送")
        print(f"[push] 指纹：{cur[:90]}…")
        return
    if force:
        print("[push] FORCE_PUSH=1，忽略变化检测，强制推送")

    title = f"行业主升浪战报 · {d.get('trade_date', '-')}"
    if d.get("intraday"):
        title += "（盘中）"
    content = build_report(d)
    print(content)
    print("\n" + "=" * 40)
    ch = send(title, content)
    print(f"[push] 使用渠道: {ch}")


if __name__ == "__main__":
    main()
