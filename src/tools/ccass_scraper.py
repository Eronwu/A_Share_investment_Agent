"""
ccass_scraper.py — 从 HKEX CCASS 官网抓取港股经纪商持仓变化

使用:
    python -m src.tools.ccass_scraper --code 03986 --days 7
    python -m src.tools.ccass_scraper --code 03986 --date 2026-03-05

输出: 各日经纪商持仓 + 日间变化分析
"""

import argparse
import re
import time
from datetime import date, datetime, timedelta

import requests

# ── 常量 ──────────────────────────────────────────────────────────────────────
CCASS_URL = "https://www3.hkexnews.hk/sdw/search/searchsdw.aspx"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": CCASS_URL,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
TAG_RE = re.compile(r"<[^>]+>")

# ── 参与者分类 ────────────────────────────────────────────────────────────────
# 分类规则:
#   A-prefix  → Stock Connect (北向/南向资金，中国结算代理)
#   C-prefix  → 托管行 (纯机构托管)
#   B-prefix  → 券商，按名单细分:
#     INSTITUTIONAL: 外资投行、机构主导券商
#     RETAIL:        纯零售券商
#     MIXED:         中资券商 (机构+零售混合)

# 机构券商关键词（B前缀，主要服务机构客户）
INSTITUTIONAL_KEYWORDS = [
    "GOLDMAN SACHS", "MORGAN STANLEY", "UBS SECURITIES", "MERRILL LYNCH",
    "CITIGROUP", "JP MORGAN", "JPMORGAN", "BARCLAYS", "DEUTSCHE BANK",
    "CREDIT SUISSE", "NOMURA", "DAIWA", "MACQUARIE", "SOCIETE GENERALE",
    "ABN AMRO", "BNP PARIBAS SECURITIES", "JEFFERIES", "LAZARD",
    "COWEN", "PIPER SANDLER", "YUNFENG",  # 云锋：马云系机构基金
]

# 零售券商关键词（主要服务零售客户）
RETAIL_KEYWORDS = [
    "FUTU", "TIGER BROKERS", "INTERACTIVE BROKERS", "LONG BRIDGE", "LONGBRIDGE",
    "USMART", "WEBULL", "BOOM SECURITIES", "PHILLIP SECURITIES",
    "BRIGHT SMART", "EMPEROR SECURITIES", "CHIEF SECURITIES",
    "FIRST SHANGHAI", "CATHAY SECURITIES", "PUBLIC FINANCIAL",
    "DAH SING SECURITIES", "EAST ASIA SECURITIES", "UOB KAY HIAN",
    "MONMONKEY", "EDDID", "ZINVEST", "SOFI SECURITIES", "IFAST",
    "PRUDENTIAL BROKERAGE", "TAI FUNG", "PC SECURITIES",
    "VICTORY SECURITIES", "WING FUNG", "DELTA ASIA", "LUK FOOK",
]

# 中资混合券商关键词（机构+零售均有，单独列示）
MIXED_KEYWORDS = [
    "HUATAI", "CHINA INTERNATIONAL CAPITAL", "CICC",
    "HAITONG", "GUOTAI JUNAN", "CMB INTERNATIONAL", "CITIC SECURITIES",
    "CHINA MERCHANTS SECURITIES", "GF SECURITIES", "SHENWAN HONGYUAN",
    "EVERBRIGHT SECURITIES", "CCB INTERNATIONAL", "BOCI",
    "BOCOM INTERNATIONAL", "GUOSEN", "CHINA GALAXY", "ORIENT SECURITIES",
    "SDIC SECURITIES", "CAITONG", "ZHONGTAI", "ZHESHANG", "SOOCHOW",
    "SPDB", "HUAJIN", "CHINA SECURITIES (INTERNATIONAL)",
    "CHINA INDUSTRIAL SECURITIES", "CHINA SECURITIES BROKERAGE",
    "PING AN SECURITIES", "FOSTER SECURITIES", "HAFOO",
    "GUOYUAN", "HUAFU", "CHINA EVERBRIGHT",
]


def classify_participant(pid: str, name: str) -> str:
    """
    返回参与者分类:
      'institutional' | 'retail' | 'mixed' | 'stock_connect' | 'custodian'
    """
    upper = name.upper()

    # A-prefix: 中国结算 (Stock Connect 南向资金)
    if pid.startswith("A"):
        return "stock_connect"

    # C-prefix: 托管行（纯机构）
    if pid.startswith("C"):
        return "custodian"

    # B-prefix: 按名单判断
    for kw in RETAIL_KEYWORDS:
        if kw in upper:
            return "retail"
    for kw in INSTITUTIONAL_KEYWORDS:
        if kw in upper:
            return "institutional"
    for kw in MIXED_KEYWORDS:
        if kw in upper:
            return "mixed"

    # 默认归 mixed（中小券商，无法确定）
    return "mixed"


# 关键经纪商缩写映射（显示用）
BROKER_ABBREV = {
    "HUATAI FINANCIAL HOLDINGS (HONG KONG)": "华泰(HK)",
    "THE HONGKONG AND SHANGHAI BANKING": "汇丰",
    "CHINA INTERNATIONAL CAPITAL CORPORATION": "中金",
    "UBS SECURITIES HONG KONG LTD": "瑞银",
    "MERRILL LYNCH INTERNATIONAL": "美林",
    "CITIBANK": "花旗",
    "GOLDMAN SACHS": "高盛",
    "JP MORGAN": "摩根大通",
    "JPMORGAN": "摩根大通",
    "MORGAN STANLEY": "摩根士丹利",
    "HAITONG INTERNATIONAL": "海通(国际)",
    "CHINA MERCHANTS SECURITIES": "招商证券",
    "GF SECURITIES": "广发",
    "CITIC SECURITIES": "中信",
    "MACQUARIE": "麦格理",
    "BARCLAYS": "巴克莱",
    "DEUTSCHE": "德意志",
    "CREDIT SUISSE": "瑞信",
    "BNP PARIBAS": "法巴",
    "SOCIETE GENERALE": "法兴",
    "NOMURA": "野村",
    "MIZUHO": "瑞穗",
    "DAIWA": "大和",
    "INTERACTIVE BROKERS": "IB盈透",
    "FUTU SECURITIES": "富途",
    "TIGER BROKERS": "老虎",
    "ORIENT SECURITIES": "东方证券",
    "GUOTAI JUNAN": "国泰君安",
    "SHENWAN HONGYUAN": "申万宏源",
}


def _abbrev(name: str) -> str:
    """返回经纪商缩写，找不到则返回原名前20字"""
    upper = name.upper()
    for k, v in BROKER_ABBREV.items():
        if k in upper:
            return v
    return name[:20]


def _strip(html_fragment: str) -> str:
    return " ".join(TAG_RE.sub("", html_fragment).split())


def _get_viewstate(session: requests.Session) -> dict:
    """GET 首页，提取 ASP.NET 隐藏字段"""
    r = session.get(CCASS_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    fields = {}
    for m in re.finditer(r'<input[^>]+name="(__[A-Z_]+)"[^>]*value="([^"]*)"', r.text):
        fields[m.group(1)] = m.group(2)
    # 也提取 today
    m = re.search(r'<input[^>]+name="today"[^>]*value="(\d+)"', r.text)
    if m:
        fields["today"] = m.group(1)
    return fields


def fetch_ccass(code: str, query_date: date, session: requests.Session) -> list[dict]:
    """
    抓取指定股票代码在指定日期的 CCASS 持仓列表。

    Returns:
        list of {
            participant_id, name, abbrev, shareholding, pct
        }
    """
    vs = _get_viewstate(session)
    date_str = query_date.strftime("%Y%m%d")

    payload = {
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": vs.get("__VIEWSTATE", ""),
        "__VIEWSTATEGENERATOR": vs.get("__VIEWSTATEGENERATOR", ""),
        "today": vs.get("today", date.today().strftime("%Y%m%d")),
        "sortBy": "shareholding",
        "sortDirection": "desc",
        "originalShareholdingDate": "",
        "alertMsg": "",
        "txtShareholdingDate": date_str,
        "txtStockCode": code,
        "txtStockName": "",
        "txtParticipantID": "",
        "txtParticipantName": "",
        "btnSearch": "Search",
    }

    r = session.post(CCASS_URL, data=payload, headers=HEADERS, timeout=30)
    r.raise_for_status()
    html = r.text

    # 找主数据表（第二个 table，含 Participant ID）
    tables = re.findall(r"<table[^>]*>(.*?)</table>", html, re.DOTALL | re.IGNORECASE)
    # 找含 "Participant ID" 表头的数据表（条款表也含 participant 但不含此表头）
    data_table = None
    for t in tables:
        if "Participant ID" in t:
            data_table = t
            break

    if not data_table:
        return []

    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", data_table, re.DOTALL | re.IGNORECASE)
    results = []

    for row in rows[1:]:  # 跳过表头
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
        cells = [_strip(td) for td in tds]
        if len(cells) < 4:
            continue

        # 解析 "Participant ID: B01829"  → 提取 ID
        pid_raw = cells[0]
        m = re.search(r"([A-Z]\d+)", pid_raw)
        pid = m.group(1) if m else pid_raw

        # 名称
        name_raw = cells[1]
        m2 = re.search(r"\):\s*(.+)", name_raw)
        name = m2.group(1).strip() if m2 else name_raw

        # 持仓数（格式: "Shareholding: 5,032,700"）
        shares_m = re.search(r"[\d,]+", cells[3].replace(",", "").replace("Shareholding:", ""))
        if not shares_m:
            shares_m = re.search(r"([\d,]+)", cells[3])
        if not shares_m:
            continue
        try:
            shares = int(shares_m.group().replace(",", ""))
        except ValueError:
            continue

        # 比例（格式: "% of the total ...Units: 15.13%"）
        pct_raw = cells[4] if len(cells) > 4 else ""
        pct_m = re.search(r"([\d.]+)%", pct_raw)
        pct = float(pct_m.group(1)) if pct_m else 0.0

        results.append(
            {
                "participant_id": pid,
                "name": name,
                "abbrev": _abbrev(name),
                "shareholding": shares,
                "pct": pct,
                "category": classify_participant(pid, name),
            }
        )

    return results


def _trading_days_back(n: int) -> list[date]:
    """返回最近 n 个交易日（周一到周五，简单过滤）"""
    days = []
    d = date.today()
    # 如果今天是周末，从上周五开始
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return days


def analyze_changes(
    history: dict[str, list[dict]], top_n: int = 15
) -> None:
    """打印多日持仓变化分析"""
    dates = sorted(history.keys())
    if not dates:
        print("无数据")
        return

    # 所有经纪商并集
    all_pids: dict[str, str] = {}
    for recs in history.values():
        for r in recs:
            all_pids[r["participant_id"]] = r["abbrev"] or r["name"][:20]

    # 最新一天 top_n
    latest_date = dates[-1]
    latest = history[latest_date]

    print(f"\n{'═'*72}")
    print(f"  CCASS 经纪商持仓  |  代码 03986 兆易创新(港股)")
    print(f"{'═'*72}")

    # ── 1. 最新持仓 Top N ──
    print(f"\n▶ {latest_date} 最新持仓（前{top_n}）")
    print(f"  {'经纪商':<22} {'持股数':>12}  {'占比':>7}")
    print(f"  {'-'*22} {'-'*12}  {'-'*7}")
    for rec in latest[:top_n]:
        print(f"  {rec['abbrev']:<22} {rec['shareholding']:>12,}  {rec['pct']:>6.2f}%")

    # 总持仓
    total = sum(r["shareholding"] for r in latest)
    total_pct = sum(r["pct"] for r in latest)
    print(f"  {'合计(CCASS参与者)':>22} {total:>12,}  {total_pct:>6.2f}%")

    # ── 1b. 投资者结构分析 ──
    CAT_LABEL = {
        "institutional": "外资机构",
        "custodian":     "托管行(机构)",
        "stock_connect": "沪深港通",
        "mixed":         "中资券商(混合)",
        "retail":        "零售券商",
    }
    CAT_ORDER = ["custodian", "institutional", "stock_connect", "mixed", "retail"]

    total_shares = sum(r["shareholding"] for r in latest)
    cat_shares: dict[str, int] = {c: 0 for c in CAT_ORDER}
    cat_brokers: dict[str, list] = {c: [] for c in CAT_ORDER}
    for r in latest:
        c = r.get("category", "mixed")
        cat_shares[c] = cat_shares.get(c, 0) + r["shareholding"]
        cat_brokers.setdefault(c, []).append(r)

    print(f"\n▶ 投资者结构分析（{latest_date}，CCASS总持仓基础）")
    print(f"  {'类别':<14} {'持股(万股)':>10}  {'占CCASS%':>8}  {'代表性经纪商'}")
    print(f"  {'-'*14} {'-'*10}  {'-'*8}  {'-'*30}")
    retail_pct = 0.0
    institutional_pct = 0.0
    for cat in CAT_ORDER:
        sh = cat_shares.get(cat, 0)
        pct_of_ccass = sh / total_shares * 100 if total_shares else 0
        reps = sorted(cat_brokers.get(cat, []), key=lambda x: -x["shareholding"])[:3]
        rep_str = "  ".join(r["abbrev"] for r in reps)
        label = CAT_LABEL.get(cat, cat)
        print(f"  {label:<14} {sh/10000:>10.1f}  {pct_of_ccass:>7.1f}%  {rep_str}")
        if cat in ("retail",):
            retail_pct += pct_of_ccass
        if cat in ("custodian", "institutional"):
            institutional_pct += pct_of_ccass

    print(f"  {'─'*60}")
    print(f"  {'机构小计':<14} {'':>10}  {institutional_pct:>7.1f}%  (托管行+外资投行)")
    print(f"  {'零售小计':<14} {'':>10}  {retail_pct:>7.1f}%  (纯零售券商估算下限)")
    print(f"  {'中资券商(混合)':<14} {'':>10}  {cat_shares.get('mixed',0)/total_shares*100:>7.1f}%  (含机构+零售，无法精确拆分)")
    print(f"\n  ⚠ 注: 散户真实比例 ≈ 零售小计({retail_pct:.1f}%) + 中资券商(混合)的零售部分")
    print(f"       中资券商散户占比行业均值约30-50%，保守估算散户总持仓约")
    mixed_low = retail_pct + cat_shares.get("mixed", 0) / total_shares * 100 * 0.30
    mixed_high = retail_pct + cat_shares.get("mixed", 0) / total_shares * 100 * 0.50
    print(f"       {mixed_low:.1f}% ~ {mixed_high:.1f}%（中资散户比例假设30-50%）")

    if len(dates) < 2:
        return

    # ── 2. 日间变化（最新 vs 前一天）──
    prev_date = dates[-2]
    prev = {r["participant_id"]: r for r in history[prev_date]}
    curr = {r["participant_id"]: r for r in history[latest_date]}

    changes = []
    for pid, rec in curr.items():
        prev_shares = prev.get(pid, {}).get("shareholding", 0)
        delta = rec["shareholding"] - prev_shares
        delta_pct = delta / prev_shares * 100 if prev_shares else float("inf")
        if abs(delta) > 0:
            changes.append(
                {
                    "pid": pid,
                    "abbrev": rec["abbrev"],
                    "curr": rec["shareholding"],
                    "curr_pct": rec["pct"],
                    "delta": delta,
                    "delta_pct": delta_pct,
                }
            )
    # 新进入
    for pid, rec in prev.items():
        if pid not in curr:
            changes.append(
                {
                    "pid": pid,
                    "abbrev": rec["abbrev"],
                    "curr": 0,
                    "curr_pct": 0,
                    "delta": -rec["shareholding"],
                    "delta_pct": -100,
                }
            )

    changes.sort(key=lambda x: -abs(x["delta"]))
    print(f"\n▶ {prev_date} → {latest_date} 变化（按变动量排序）")
    print(f"  {'经纪商':<22} {'变动量':>12}  {'变动%':>8}  {'现持仓':>12}  {'占比':>7}")
    print(f"  {'-'*22} {'-'*12}  {'-'*8}  {'-'*12}  {'-'*7}")
    for c in changes[:top_n]:
        arrow = "▲" if c["delta"] > 0 else "▼"
        color_in = "+" if c["delta"] > 0 else ""
        print(
            f"  {c['abbrev']:<22} {color_in}{c['delta']:>11,}{arrow}  "
            f"{color_in}{c['delta_pct']:>7.1f}%  {c['curr']:>12,}  {c['curr_pct']:>6.2f}%"
        )

    # ── 3. 多日趋势（每天各类净变化）──
    if len(dates) >= 3:
        print(f"\n▶ 多日各类持仓变化趋势")
        print(f"  {'日期':<12}  {'机构净(万)':>10}  {'沪深港通净(万)':>13}  {'中资净(万)':>10}  {'零售净(万)':>10}  {'总净(万)':>9}")
        print(f"  {'-'*12}  {'-'*10}  {'-'*13}  {'-'*10}  {'-'*10}  {'-'*9}")
        for i in range(1, len(dates)):
            d_curr = dates[i]
            d_prev = dates[i - 1]
            # 按category分组
            def cat_net(cat):
                prev_sh = sum(r["shareholding"] for r in history[d_prev] if r.get("category") == cat)
                curr_sh = sum(r["shareholding"] for r in history[d_curr] if r.get("category") == cat)
                return (curr_sh - prev_sh) / 10000

            net_inst = cat_net("institutional") + cat_net("custodian")
            net_sc   = cat_net("stock_connect")
            net_mix  = cat_net("mixed")
            net_ret  = cat_net("retail")

            total_prev = sum(r["shareholding"] for r in history[d_prev])
            total_curr = sum(r["shareholding"] for r in history[d_curr])
            net_total = (total_curr - total_prev) / 10000

            def fmt(v): return f"+{v:.1f}" if v >= 0 else f"{v:.1f}"
            print(
                f"  {d_curr}  {fmt(net_inst):>10}  {fmt(net_sc):>13}  "
                f"{fmt(net_mix):>10}  {fmt(net_ret):>10}  {fmt(net_total):>9}"
            )

    print(f"\n{'═'*72}\n")


def main():
    parser = argparse.ArgumentParser(description="CCASS 经纪商持仓抓取工具")
    parser.add_argument("--code", default="03986", help="股票代码（不含HK前缀），默认03986")
    parser.add_argument("--days", type=int, default=5, help="抓取最近N个交易日，默认5")
    parser.add_argument("--date", help="指定单个日期 (YYYY-MM-DD)")
    parser.add_argument("--top", type=int, default=15, help="显示前N个经纪商，默认15")
    args = parser.parse_args()

    code = args.code.lstrip("HKhk").zfill(5)

    if args.date:
        query_dates = [datetime.strptime(args.date, "%Y-%m-%d").date()]
    else:
        query_dates = list(reversed(_trading_days_back(args.days)))

    session = requests.Session()
    history: dict[str, list[dict]] = {}

    for d in query_dates:
        ds = d.strftime("%Y-%m-%d")
        print(f"  抓取 {ds} ...", end="", flush=True)
        try:
            recs = fetch_ccass(code, d, session)
            if recs:
                history[ds] = recs
                print(f" {len(recs)} 个参与者")
            else:
                print(" 无数据（可能为非交易日）")
        except Exception as e:
            print(f" 失败: {e}")
        time.sleep(1.5)  # 礼貌性延迟

    analyze_changes(history, top_n=args.top)


if __name__ == "__main__":
    main()
