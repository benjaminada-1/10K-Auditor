"""
modules/finance_data.py
模块 1：yfinance 财务与资本配置计算引擎

核心函数：get_financial_metrics(ticker: str, years: int = 5) -> dict[str, Any]

职责：
  1. 从 yfinance 抓取利润表 / 现金流量表，提取 8 个基础科目。
  2. 计算自由现金流含金量、SBC 稀释率、资本配置占比等派生比率。
  3. 对 yfinance 脏数据高度鲁棒：字段别名归一化、MultiIndex 拍平、
     防零 / 防 inf 除法、缺失科目降级为 NaN 并记入 warnings，绝不抛异常中断。
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# 字段归一化别名表
# 归一化规则：转小写、仅保留字母数字
# ---------------------------------------------------------------------------

def _normalize(name: Any) -> str:
    """将财报行名归一化为小写纯字母数字，用于跨公司行名差异匹配。"""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


FIELD_ALIASES: dict[str, list[str]] = {
    "net_income": [
        "netincome",
        "netincometocommon",
        "netincomeloss",
        "netincomeavailabletocommonshareholders",
        "profitloss",
    ],
    "cfo": [
        "totalcashfromoperatingactivities",
        "operatingcashflow",
        "netcashprovidedbyusedinoperatingactivities",
        "cashfromoperatingactivities",
        "netcashfromoperatingactivities",
    ],
    "capex": [
        "capitalexpenditures",
        "capex",
        "capitalexpenditure",
        "paymentstoacquirepropertyplantandequipment",
        "purchasesofpropertyandequipment",
        "additionstopropertyplantandequipment",
    ],
    "sbc": [
        "stockbasedcompensation",
        "sharebasedcompensation",
        "sharebasedcompensationexpense",
        "stockcompensation",
    ],
    "buyback": [
        "repurchaseofstock",
        "repurchaseofcommonstock",
        "purchaseofcommonstock",
        "paymentforrepurchaseofcommonstock",
        "treasurystock",
        "commonstockrepurchased",
    ],
    "dividends": [
        "commonstockdividendpaid",
        "dividendspaidcommon",
        "dividendspaid",
        "paymentsofdividends",
        "paymentsofdividendscommon",
        "dividendspaidoncommonstock",
    ],
    "debt_repaid": [
        "repaymentofdebt",
        "longtermdebtrepayment",
        "repaymentoflongtermdebt",
        "debtrepayments",
        "repaymentsofdebt",
        "paymentstorepaydebt",
    ],
    "fcf": [
        "freecashflow",
    ],
}

# 每个字段优先检索的报表
PRIMARY_STATEMENT: dict[str, str] = {
    "net_income": "income",
    "cfo": "cash",
    "capex": "cash",
    "sbc": "cash",
    "buyback": "cash",
    "dividends": "cash",
    "debt_repaid": "cash",
    "fcf": "cash",
}

BASE_FIELDS = list(FIELD_ALIASES.keys())

# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _num(x: Any) -> float:
    """安全数值化：任何异常一律降级为 NaN。"""
    if x is None:
        return float("nan")
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v if math.isfinite(v) else float("nan")


def _isnum(x: Any) -> bool:
    """是否为有效有限数值（NaN / inf / None 均为 False）。"""
    v = _num(x)
    return not math.isnan(v)


def _flatten_columns(df: pd.DataFrame | None) -> pd.DataFrame:
    """若列为 MultiIndex，则拍平提取其中的日期层。"""
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        chosen = None
        for lvl in range(df.columns.nlevels):
            level_vals = list(df.columns.get_level_values(lvl))
            parsed = pd.to_datetime(pd.Series(level_vals), errors="coerce")
            if parsed.notna().any():
                chosen = parsed
                break
        if chosen is not None:
            df = df.copy()
            df.columns = chosen.values
        else:
            df = df.copy()
            df.columns = ["_".join(str(x) for x in tup) for tup in df.columns]
    return df


def _extract_row(df: pd.DataFrame | None, aliases: list[str]) -> pd.Series | None:
    """
    按归一化别名从报表中提取行：
      - 第一轮：精确匹配
      - 第二轮：前缀宽容匹配（应对 "... Of Stock" 类前后缀差异）
    仅返回至少含一个有效数值的行，否则视为未找到。
    """
    if df is None or df.empty:
        return None
    norm_index = {idx: _normalize(idx) for idx in df.index}
    for exact in (True, False):
        for alias in aliases:
            for idx, norm_idx in norm_index.items():
                hit = norm_idx == alias if exact else norm_idx.startswith(alias)
                if not hit:
                    continue
                row = df.loc[idx]
                if isinstance(row, pd.DataFrame):  # 重复行名兜底
                    row = row.iloc[0]
                row = pd.to_numeric(row, errors="coerce")
                if row.notna().any():
                    return row
    return None


def _series_by_year(row: pd.Series | None, years: int) -> dict[int, float]:
    """将行转为 {年份: 数值}，按年份倒序排列并截断到最近 N 年。"""
    out: dict[int, float] = {}
    if row is None:
        return out
    series = pd.to_numeric(row, errors="coerce").dropna()
    for date, val in series.items():
        try:
            year = int(pd.Timestamp(date).year)
        except Exception:
            continue
        out[year] = _num(val)
    return dict(sorted(out.items(), key=lambda kv: kv[0], reverse=True)[:years])


# ---------------------------------------------------------------------------
# 派生比率计算
# ---------------------------------------------------------------------------

def _derive(rec: dict[str, float]) -> None:
    """就地计算派生比率。所有除法均做防零 / 防 NaN / 防 inf 处理。"""
    ni = rec.get("net_income", float("nan"))
    cfo = rec.get("cfo", float("nan"))
    capex = rec.get("capex", float("nan"))
    sbc = rec.get("sbc", float("nan"))
    buyback = rec.get("buyback", float("nan"))
    dividends = rec.get("dividends", float("nan"))
    debt = rec.get("debt_repaid", float("nan"))
    fcf = rec.get("fcf", float("nan"))

    # FCF 缺失时，用 CFO + CapEx 兜底估算（CapEx 为负数，故相加）
    if not _isnum(fcf) and _isnum(cfo) and _isnum(capex):
        fcf = cfo + capex
        rec["fcf"] = fcf

    # 账面利润与现金流之差：>0 提示净利润缺乏现金流支撑
    rec["ni_fcf_diff"] = ni - fcf if _isnum(ni) and _isnum(fcf) else float("nan")

    # SBC 稀释率：仅当 FCF > 0 时计算
    rec["sbc_pct_fcf"] = (
        sbc / fcf if _isnum(sbc) and _isnum(fcf) and fcf > 0 else float("nan")
    )

    # 资本配置占比：流出项统一取绝对值后除以 CFO，仅当 CFO > 0 时计算
    def _pct(num: Any) -> float:
        if _isnum(num) and _isnum(cfo) and cfo > 0:
            return abs(num) / cfo
        return float("nan")

    rec["capex_pct_cfo"] = _pct(capex)
    rec["buyback_pct_cfo"] = _pct(buyback)
    rec["dividends_pct_cfo"] = _pct(dividends)
    rec["debt_pct_cfo"] = _pct(debt)

    alloc_keys = ("capex_pct_cfo", "buyback_pct_cfo", "dividends_pct_cfo", "debt_pct_cfo")
    parts = [rec[k] for k in alloc_keys if _isnum(rec[k])]
    rec["alloc_total_pct_cfo"] = sum(parts) if parts else float("nan")

    # 股东回报后的 FCF 净剩余
    if _isnum(fcf):
        b = abs(buyback) if _isnum(buyback) else 0.0
        d = abs(dividends) if _isnum(dividends) else 0.0
        rec["fcf_after_returns"] = fcf - b - d
    else:
        rec["fcf_after_returns"] = float("nan")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def get_financial_metrics(ticker: str, years: int = 5) -> dict[str, Any]:
    """
    抓取并计算指定美股的基础财务科目与资本配置比率。

    参数:
        ticker: 美股代码（如 "AAPL" / "MSFT" / "BRK-B"）
        years:  回溯年数，默认 5

    返回:
        {
          "ticker":    str,
          "data":      {int(年份): {字段: 数值}}  # 年份倒序（FY2025 在前）
          "warnings":  list[str],
          "error":     str | None,
        }
    """
    result: dict[str, Any] = {
        "ticker": str(ticker).upper().strip(),
        "data": {},
        "warnings": [],
        "error": None,
    }

    try:
        import yfinance as yf
    except ImportError:
        result["error"] = "yfinance 未安装，请执行：pip install yfinance"
        return result

    try:
        tk = yf.Ticker(result["ticker"])
        # 属性兼任兼容：新版 yfinance 属性名（income_stmt / cash_flow）
        # 不存在或为空时，回退到旧版属性名（financials / cashflow）
        income_raw = getattr(tk, "income_stmt", None)
        if income_raw is None or getattr(income_raw, "empty", True):
            income_raw = getattr(tk, "financials", None)
        cash_raw = getattr(tk, "cash_flow", None)
        if cash_raw is None or getattr(cash_raw, "empty", True):
            cash_raw = getattr(tk, "cashflow", None)
        income = _flatten_columns(income_raw)
        cashflow = _flatten_columns(cash_raw)
    except Exception as exc:  # yfinance 网络 / 限流 / 脏数据
        result["error"] = f"yfinance 数据抓取失败：{exc}"
        return result

    if (income is None or income.empty) and (cashflow is None or cashflow.empty):
        result["error"] = (
            f"未找到 {result['ticker']} 的财务数据，请检查股票代码是否正确"
            "（美股代码，如 AAPL / MSFT / BRK-B）。"
        )
        return result

    statements = {"income": income, "cash": cashflow}

    raw: dict[str, dict[int, float]] = {}
    for field, aliases in FIELD_ALIASES.items():
        order = [PRIMARY_STATEMENT[field]]
        order.append("cash" if PRIMARY_STATEMENT[field] == "income" else "income")
        row = None
        for stmt_name in order:
            row = _extract_row(statements.get(stmt_name), aliases)
            if row is not None:
                break
        raw[field] = _series_by_year(row, years)
        if row is None:
            result["warnings"].append(
                f"科目 [{field}] 在所有报表中均未找到，已降级为 NaN。"
            )

    # 合并年份（倒序），仅保留核心科目至少有一项有值的年份
    all_years = sorted(
        {y for series in raw.values() for y in series}, reverse=True
    )[:years]

    fcf_fallback_used = False
    for year in all_years:
        rec: dict[str, float] = {f: raw[f].get(year, float("nan")) for f in BASE_FIELDS}
        before = rec["fcf"]
        _derive(rec)
        if not _isnum(before) and _isnum(rec["fcf"]):
            fcf_fallback_used = True
        result["data"][year] = rec

    if fcf_fallback_used:
        result["warnings"].append(
            "部分年份报表未提供 Free Cash Flow 行，已使用 CFO + CapEx 兜底估算。"
        )

    if not result["data"]:
        result["warnings"].append("未解析出任何有效年份数据，请检查代码是否正确。")

    return result
