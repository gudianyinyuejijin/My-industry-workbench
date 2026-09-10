# 行业主升浪工作台 · GitHub Pages 自动部署版

每天工作日自动更新、永不过期、固定网址可访问的行业五因子评分 + RPS + 二八指数 + 概念板块工作台。

> 数据引擎代码（`workbench_data.py` / `score_v2_final.py` / `updater.py` / `etf_map.py` / `erba.py` / `concept_score.py`）已生产验证，不要改动评分逻辑。本仓库只负责"打包成静态页 + Actions 跑批 + Pages 托管"。

---

## 0. 前置

1. 一个 GitHub 账号（没有的话去 https://github.com 注册）
2. 在 GitHub 上新建一个 **私有仓库**（建议命名 `industry-workbench` 或 `my-workbench`，随便起）
   - 勾不勾 README 都行，建议先不勾，我们手动 push 全部文件
3. 确认本地/网页能正常访问东方财富（`push2delay.eastmoney.com`）与申万宏源（`www.swsresearch.com`）—— Actions 跑在境外 runner 上，正常情况是通的，少数地区可能需要自备代理（这种情况下请改用自托管 runner，本仓库用 GitHub-hosted runner）

---

## 1. 推送代码到新仓库

把本目录所有文件（**包括隐藏的 `.github/`**）整推到仓库根：

```bash
cd gh_deploy                       # 本项目根目录
git init
git add .
git commit -m "init: workbench static deploy"
git branch -M main
git remote add origin git@github.com:<你的用户名>/<仓库名>.git
git push -u origin main
```

> 注意一定要 push `.github/workflows/daily_update.yml`，这是 Actions 自动运行的关键。如果只 push 文件而漏了 `.github`，定时更新不会跑。

---

## 2. 启用 GitHub Pages（Actions 来源）

进入 GitHub 仓库页面 → 顶栏 **Settings** → 左侧 **Pages**：

- **Source** 选 **GitHub Actions**（不是 "Deploy from a branch"）
- 保存即可

这样工作流 `daily_workbench` 跑完后会直接把 `index.html` 部署到 Pages。

---

## 3. 第一次手动触发（验证）

1. 仓库顶栏 **Actions** → 左侧选 **daily_workbench**
2. 右侧 **Run workflow** → 选 main 分支 → 点绿色 **Run workflow**
3. 大约 1–3 分钟跑完
   - 看到 ✅ 绿色对勾 = 成功
   - 看到 ❌ 红色叉 = 看日志定位错误（最常见是网络超时，下面有排查）

跑成功后，进入 Settings → Pages → 顶部会显示你的网址：

```
https://<你的用户名>.github.io/<仓库名>/
```

把这个链接收藏即可，永久有效。

---

## 4. 之后的自动更新

工作流已经配好定时（`.github/workflows/daily_update.yml`）：

```
schedule:
  - cron: '35 3,7 * * 1-5'    # 北京时间工作日 11:35 / 15:15
```

- 周一到周五每天更新 **两次**（收盘后 + 盘中）
- 周六周日不跑（股市不开）
- 每次跑完会自动重新部署 Pages，刷新浏览器或硬刷（Ctrl+Shift+R）即可看到新数据
- 数据文件 `data/workbench.json` 也会 commit 进仓库，方便回看历史

---

## 5. 改"只看不动"

数据引擎已封装在 `workbench_data.py`，**不要去改它**。

如果你只想换展示、不动评分逻辑：

- 改前端样式 / Tab 顺序 / 配色：改 `template.html`，然后重跑 `python generate.py`，会生成新的 `index.html`
- 想换一个默认打开的 Tab（默认是"强趋势"）：改 `template.html` 里的 `let TAB = "strong";` 那行
- 想加新字段：在 `workbench_data.py` 的 `payload` 里追加字段，`template.html` 里 `render()` 渲染一行即可

> 任何对 `template.html` / `generate.py` 的改动都会随 commit 推到 GitHub，下次定时跑自动生效（不用手动触发）。

---

## 6. 故障排查

### 6.1 Actions 失败显示红叉 ❌

点进失败的那次运行 → 展开有红 × 的步骤 → 看日志最后一段：

- `ImportError` / `ModuleNotFoundError` → 一般是 `pip install` 那一步挂了，重试即可（PyPI 偶发网络问题）
- `requests.exceptions.*` / 超时 → 申万或东财源临时挂了，**这很正常**，点 Actions 页面右上 "Re-run jobs" 重跑
- `KeyError` / 数据字段缺失 → 可能是某个源改了字段，发 issue 或临时 `python generate.py --force` 全量重抓

### 6.2 页面打开但不是最新的

- 检查 Actions 最近一次 run 是否成功（绿色 ✅）
- 浏览器**强制刷新**（Mac: ⌘+Shift+R；Windows: Ctrl+F5）
- Pages CDN 有缓存，第一次部署可能要等 1–2 分钟才生效

### 6.3 页面打开是 404

- Settings → Pages → Source 必须是 **GitHub Actions**，不是 branch
- 仓库必须是 **public**（免费 Pages）或你的账号开通了 Pro/Student（私有仓库也能用 Pages）

### 6.4 数据停留在某天不再更新

通常是定时任务被 GitHub 自动暂停了（连续 60 天无活动）：

- 仓库 → Actions → 顶部如果看到 *"Workflows have been disabled"* → 点启用
- 或者随便手动 Run workflow 一次，cron 就会重新激活

### 6.5 申万 / 东财源偶发超时

- 数据引擎（`updater.py` / `etf_map.py`）已经内置重试
- 单次运行 5–15 分钟内能完成就是正常的；超过 15 分钟还在跑多半是限流，建议直接 cancel 半小时后再 Run

---

## 7. 文件结构

```
gh_deploy/
├── generate.py                 # ★ 每日入口：算数据 + 生成 index.html
├── template.html               # ★ 静态模板（含 __DATA_JSON__ 占位符）
├── index.html                  # 生成产物（每日覆盖，提交到仓库）
├── workbench_data.py           # 数据引擎（不要改）
├── score_v2_final.py           # 五因子评分（不要改）
├── updater.py                  # 申万增量更新（不要改）
├── etf_map.py                  # 行业-ETF映射（不要改）
├── erba.py                     # 二八指数（不要改）
├── concept_score.py            # 概念板块评分（不要改）
├── requirements.txt            # Python 依赖
├── data/
│   ├── sw_industry.pkl         # 申万 155 行业历史 K线（14MB，提交进仓库）
│   ├── etf_final.json          # 行业-ETF映射数据
│   └── workbench.json          # 当日 payload（每日生成）
└── .github/
    └── workflows/
        └── daily_update.yml    # ★ GitHub Actions 配置
```

---

## 8. 本地试跑

不依赖 GitHub，本地就能跑一遍验证：

```bash
cd gh_deploy
pip install -r requirements.txt
python generate.py
# 看到 "[generate] OK · trade_date=..." 即成功
# 当前目录会生成 index.html，直接双击或浏览器打开即可
```

加 `--force` 强制全量重抓所有源（**慢、易被限流**，仅用于回填历史数据）。

---

## 9. 数据来源声明

- 行业评分：申万宏源研究官网（`www.swsresearch.com`）行情接口
- ETF / 指数：东方财富（`push2delay.eastmoney.com`）
- 概念板块：公开行情接口

本仓库仅做数据计算与展示，**不构成任何投资建议**。