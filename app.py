"""
app.py —— 10K-Auditor Streamlit 主程序

双 Tab 交互终端：
  Tab 1: 📊 资本配置与利润含金量（免 Key 直接可用）
  Tab 2: 🚩 10-K 隐秘风险红旗（AI 驱动）

设计要点：
  - 宽屏 layout="wide"，深色专业研报风格 CSS。
  - st.session_state 缓存财务数据 / 10-K 文本 / AI 审计结果，
    切换模型或点击下载时页面不重置。
  - 侧边栏 BYOK 通用控制台：6 大供应商 + 预设模型 + 手动模型覆盖输入。
"""
from __future__ import annotations

import html
import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules.ai_auditor import PROVIDER_PRESETS, audit_risk_factors
from modules.finance_data import get_financial_metrics
from modules.sec_parser import get_item_1a

st.set_page_config(
    page_title="10K-Auditor · 美股资本配置与 10-K 红旗审计终端",
    page_icon="🔍",
    layout="wide",
)

# ---------------------------------------------------------------------------
# 深色研报风格 CSS
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    .block-container { padding-top: 1.6rem; max-width: 1400px; }
    .title-text { font-size: 1.9rem; font-weight: 800; letter-spacing: .5px; margin-bottom: 0; }
    .subtitle-text { color: #9AA0A6; font-size: .95rem; margin-bottom: 1.2rem; }
    .exec-panel {
        background: rgba(75,155,255,.08); border: 1px solid #4B9BFF;
        border-radius: 10px; padding: 18px 22px; margin: 10px 0 18px 0;
    }
    .flag-card { border-radius: 10px; padding: 16px 20px; margin: 14px 0; }
    .flag-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
    .flag-badge { font-size: .78rem; font-weight: 800; padding: 3px 12px; border-radius: 999px; color: #0E1117; }
    .flag-cat { font-size: .8rem; color: #B0B6BE; letter-spacing: .5px; }
    .flag-title { font-size: 1.08rem; font-weight: 700; margin: 4px 0 8px 0; }
    .flag-label { font-size: .72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; opacity: .7; margin: 10px 0 4px 0; }
    .flag-quote { font-style: italic; opacity: .92; border-left: 3px solid currentColor; padding-left: 12px; margin: 4px 0; font-size: .92rem; line-height: 1.5; }
    .flag-take { font-size: .95rem; line-height: 1.55; }
    </style>
    """,
    unsafe_allow_html=True,
)

SEVERITY_COLORS = {"CRITICAL": "#FF4B4B", "HIGH": "#FFA500", "MEDIUM": "#4B9BFF"}

# ---------------------------------------------------------------------------
# 状态初始化（缓存，防止页面重置）
# ---------------------------------------------------------------------------
for _key, _default in {
    "fin_data": None,
    "sec_data": None,
    "audit": None,
    "audited_ticker": None,
}.items():
    st.session_state.setdefault(_key, _default)

# ---------------------------------------------------------------------------
# 格式化工具
# ---------------------------------------------------------------------------
def _finite(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(float(x))


def fmt_usd(x) -> str:
    if not _finite(x):
        return "N/A"
    v = float(x)
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e12:
        return f"{sign}${a/1e12:,.2f}T"
    if a >= 1e9:
        return f"{sign}${a/1e9:,.2f}B"
    if a >= 1e6:
        return f"{sign}${a/1e6:,.2f}M"
    return f"{sign}${a:,.0f}"


def fmt_pct(x) -> str:
    if not _finite(x):
        return "N/A"
    return f"{float(x)*100:,.1f}%"


def esc(s) -> str:
    return html.escape(str(s), quote=False)


# ---------------------------------------------------------------------------
# 侧边栏 —— BYOK 通用控制台
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ BYOK 通用控制台")
    st.caption("Bring Your Own Key · 零成本 · 纯前端内存保存")

    ticker_input = st.text_input("股票代码（美股）", value="AAPL").upper().strip()

    provider = st.selectbox("AI 供应商", list(PROVIDER_PRESETS.keys()))
    preset = PROVIDER_PRESETS[provider]

    if preset["models"]:
        model_preset = st.selectbox("预设模型", preset["models"])
    else:
        model_preset = ""
        st.info("该渠道无预设模型，请在下方手动指定模型 ID。")

    model_override = st.text_input(
        "手动指定模型 ID（可选，填写后优先覆盖预设）",
        help="关键防卡死设计：未来任何新模型发布，直接填入其官方模型 ID 即可使用，无需更新代码。",
    )
    model_final = (model_override or "").strip() or model_preset

    custom_base_url = ""
    if provider == "Custom (OpenAI Compatible)":
        custom_base_url = st.text_input(
            "自定义 Base URL",
            placeholder="例如 http://localhost:11434/v1",
            help="任意 OpenAI 兼容端点均可，如 Ollama / vLLM / OneAPI / LiteLLM 网关。",
        )

    api_key = st.text_input(
        "API Key",
        type="password",
        help="仅存于当前浏览器会话内存，刷新即清空，不会上传或持久化。",
    )

    run_btn = st.button("🚀 启动买方深度审计", use_container_width=True, type="primary")

    st.divider()
    st.caption(
        "数据链路：财务数据来自 yfinance（免 Key）；"
        "10-K 原文来自 SEC EDGAR 官方接口（免 Key，遵守限速）。"
    )

# ---------------------------------------------------------------------------
# 执行流水线
# ---------------------------------------------------------------------------
if run_btn:
    if not ticker_input:
        st.error("请先输入股票代码。")
    else:
        with st.spinner(f"📡 正在抓取 {ticker_input} 财务数据与资本配置…"):
            st.session_state["fin_data"] = get_financial_metrics(ticker_input)
        with st.spinner(f"📄 正在从 SEC EDGAR 抓取 {ticker_input} 最新 10-K Item 1A…"):
            st.session_state["sec_data"] = get_item_1a(ticker_input)

        st.session_state["audit"] = None
        sec = st.session_state["sec_data"]
        if api_key.strip() and sec and sec.get("item_1a_text"):
            with st.spinner(f"🧠 {provider} · {model_final or '默认模型'} 正在执行红旗审计…"):
                st.session_state["audit"] = audit_risk_factors(
                    ticker=ticker_input,
                    item_1a_text=sec["item_1a_text"],
                    provider=provider,
                    api_key=api_key,
                    model=model_final,
                    custom_base_url=custom_base_url,
                )
            st.session_state["audited_ticker"] = ticker_input
        elif not api_key.strip():
            st.info("未填写 API Key：Tab 1 财务分析与 Tab 2 的 10-K 原文已就绪，仅跳过 AI 审计。")

# ---------------------------------------------------------------------------
# 渲染工具
# ---------------------------------------------------------------------------
def _years_chronological(fin: dict) -> list[int]:
    return sorted(fin["data"].keys())  # FY 由远及近


def _safe_list(vals):
    return [v if _finite(v) else None for v in vals]


def render_finance_tab(fin: dict) -> None:
    data = fin["data"]
    years = _years_chronological(fin)
    xs = [f"FY{y}" for y in years]

    if fin.get("error"):
        st.error(fin["error"])
        return

    latest = years[-1] if years else None
    latest_rec = data[latest] if latest is not None else {}

    # ---- 4 张核心记分卡 ----
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("最新净利润 (Net Income)", fmt_usd(latest_rec.get("net_income")), f"FY{latest}")
    c2.metric("自由现金流 (FCF)", fmt_usd(latest_rec.get("fcf")), f"FY{latest}")
    c3.metric(
        "SBC / FCF 稀释率",
        fmt_pct(latest_rec.get("sbc_pct_fcf")),
        help="股权激励占自由现金流比重。越高，说明账面 FCF 被股权稀释成本侵蚀越严重。",
    )
    c4.metric(
        "股东回报 / CFO 占比",
        fmt_pct(
            (latest_rec.get("buyback_pct_cfo", float("nan")) or float("nan"))
            + (latest_rec.get("dividends_pct_cfo", float("nan")) or float("nan"))
        ),
        help="回购 + 分红占经营现金流比重，衡量股东回报力度。",
    )

    # ---- 图 1：净利润 vs FCF + SBC ----
    fig1 = go.Figure()
    fig1.add_bar(
        name="净利润 (Net Income)",
        x=xs,
        y=_safe_list([data[y].get("net_income") for y in years]),
        marker_color="#2E86C1",
    )
    fig1.add_bar(
        name="自由现金流 (FCF)",
        x=xs,
        y=_safe_list([data[y].get("fcf") for y in years]),
        marker_color="#28B463",
    )
    fig1.add_scatter(
        name="股权激励 SBC",
        x=xs,
        y=_safe_list([data[y].get("sbc") for y in years]),
        mode="lines+markers",
        yaxis="y2",
        line=dict(color="#E67E22", width=3),
        marker=dict(size=8),
    )
    fig1.update_layout(
        title="历年净利润 vs 自由现金流（柱）与股权激励（线）",
        barmode="group",
        template="plotly_dark",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        yaxis=dict(title="金额 (USD)"),
        yaxis2=dict(title="SBC (USD)", overlaying="y", side="right", showgrid=False),
        height=420,
        margin=dict(t=60),
    )
    st.plotly_chart(fig1, use_container_width=True)

    # ---- 图 2：CFO 现金流去向拆解 ----
    fig2 = go.Figure()
    for key, name, color in [
        ("capex_pct_cfo", "CapEx 资本支出", "#5D6D7E"),
        ("buyback_pct_cfo", "股份回购", "#2E86C1"),
        ("dividends_pct_cfo", "分红", "#28B463"),
        ("debt_pct_cfo", "偿债", "#C0392B"),
    ]:
        fig2.add_bar(
            name=name,
            x=xs,
            y=[float(data[y].get(key, float("nan")) * 100) if _finite(data[y].get(key)) else 0.0 for y in years],
            marker_color=color,
        )
    fig2.update_layout(
        title="CFO 现金流去向拆解（占经营现金流比例，%）",
        barmode="stack",
        template="plotly_dark",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        yaxis=dict(title="占 CFO 比例 (%)"),
        height=420,
        margin=dict(t=60),
    )
    st.plotly_chart(fig2, use_container_width=True)

    # ---- 完整数据表 ----
    with st.expander("📋 完整格式化财务数据", expanded=False):
        row_specs = [
            ("净利润 Net Income", "net_income", fmt_usd),
            ("经营现金流 CFO", "cfo", fmt_usd),
            ("资本支出 CapEx", "capex", fmt_usd),
            ("股权激励 SBC", "sbc", fmt_usd),
            ("股份回购 Buyback", "buyback", fmt_usd),
            ("分红 Dividends", "dividends", fmt_usd),
            ("偿债 Debt Repaid", "debt_repaid", fmt_usd),
            ("自由现金流 FCF", "fcf", fmt_usd),
            ("净利润 - FCF 差额", "ni_fcf_diff", fmt_usd),
            ("SBC / FCF", "sbc_pct_fcf", fmt_pct),
            ("CapEx / CFO", "capex_pct_cfo", fmt_pct),
            ("回购 / CFO", "buyback_pct_cfo", fmt_pct),
            ("分红 / CFO", "dividends_pct_cfo", fmt_pct),
            ("偿债 / CFO", "debt_pct_cfo", fmt_pct),
            ("资本配置合计 / CFO", "alloc_total_pct_cfo", fmt_pct),
            ("股东回报后 FCF 净剩余", "fcf_after_returns", fmt_usd),
        ]
        table = {}
        for label, key, fn in row_specs:
            table[label] = {f"FY{y}": fn(data[y].get(key)) for y in sorted(years, reverse=True)}
        st.dataframe(pd.DataFrame(table), use_container_width=True)

    # ---- 数据警告 ----
    warnings = fin.get("warnings") or []
    if warnings:
        with st.expander(f"⚠️ 数据警告（{len(warnings)}）", expanded=bool(fin.get("error"))):
            for w in warnings:
                st.warning(w)


def render_flag_card(flag: dict) -> None:
    sev = str(flag.get("severity", "MEDIUM")).upper()
    color = SEVERITY_COLORS.get(sev, SEVERITY_COLORS["MEDIUM"])
    st.markdown(
        f"""
        <div class="flag-card" style="border:1px solid {color}; border-left-width:6px; background:rgba(255,255,255,0.03);">
          <div class="flag-head">
            <span class="flag-badge" style="background:{color};">{esc(sev)}</span>
            <span class="flag-cat">{esc(flag.get("category", ""))}</span>
          </div>
          <div class="flag-title">{esc(flag.get("flag_title", ""))}</div>
          <div class="flag-label">英文原文铁证</div>
          <div class="flag-quote" style="color:{color};">“{esc(flag.get("evidence_quote", ""))}”</div>
          <div class="flag-label">买方审辩分析</div>
          <div class="flag-take">{esc(flag.get("analyst_take", ""))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def build_markdown_report(ticker: str, fin: dict, sec: dict, audit: dict | None) -> str:
    """组装可下载的完整 Markdown 审计报告。"""
    lines: list[str] = []
    lines.append(f"# 10K-Auditor 买方审计报告 · {ticker}")
    lines.append("")
    lines.append(f"- SEC 10-K 报送日期：{sec.get('filing_date') or 'N/A'}")
    lines.append(f"- Item 1A 提取字符数：{len(sec.get('item_1a_text', '')):,}")
    if audit and not audit.get("error"):
        lines.append(f"- 审计供应商：{audit.get('provider')} / {audit.get('model')}")
    lines.append("")

    # ---- 财务部分 ----
    lines.append("## 一、资本配置与利润含金量")
    lines.append("")
    if fin and not fin.get("error"):
        data = fin["data"]
        years = sorted(data.keys(), reverse=True)
        lines.append("| 指标 | " + " | ".join(f"FY{y}" for y in years) + " |")
        lines.append("|" + "---|" * (len(years) + 1))
        for label, key, fn in [
            ("净利润 Net Income", "net_income", fmt_usd),
            ("经营现金流 CFO", "cfo", fmt_usd),
            ("自由现金流 FCF", "fcf", fmt_usd),
            ("股权激励 SBC", "sbc", fmt_usd),
            ("净利润 - FCF 差额", "ni_fcf_diff", fmt_usd),
            ("SBC / FCF", "sbc_pct_fcf", fmt_pct),
            ("CapEx / CFO", "capex_pct_cfo", fmt_pct),
            ("回购 / CFO", "buyback_pct_cfo", fmt_pct),
            ("分红 / CFO", "dividends_pct_cfo", fmt_pct),
            ("偿债 / CFO", "debt_pct_cfo", fmt_pct),
            ("股东回报后 FCF 净剩余", "fcf_after_returns", fmt_usd),
        ]:
            lines.append(f"| {label} | " + " | ".join(fn(data[y].get(key)) for y in years) + " |")
        if fin.get("warnings"):
            lines.append("")
            lines.append("**数据警告：**")
            for w in fin["warnings"]:
                lines.append(f"- {w}")
    else:
        lines.append("（财务数据不可用）")
    lines.append("")

    # ---- AI 审计部分 ----
    lines.append("## 二、10-K 隐秘风险红旗（AI 买方审计）")
    lines.append("")
    if audit and not audit.get("error"):
        lines.append(f"### 高管风险摘要")
        lines.append("")
        lines.append(audit.get("executive_summary", ""))
        lines.append("")
        for i, flag in enumerate(audit.get("primary_concerns", []), 1):
            lines.append(f"### 红旗 {i} · [{flag.get('severity')}] {flag.get('category')} · {flag.get('flag_title')}")
            lines.append("")
            lines.append(f"> {flag.get('evidence_quote', '')}")
            lines.append("")
            lines.append(f"**买方审辩分析：** {flag.get('analyst_take', '')}")
            lines.append("")
    elif audit and audit.get("error"):
        lines.append(f"AI 审计失败：{audit.get('error')}")
        lines.append("")
        lines.append(f"排障指引：{audit.get('hint', '')}")
    else:
        lines.append("（未执行 AI 审计）")
    lines.append("")
    lines.append("---")
    lines.append("*Generated by 10K-Auditor · 仅供研究学习，不构成投资建议。*")
    return "\n".join(lines)


def render_sec_tab(sec: dict, fin: dict) -> None:
    if sec is None:
        st.info("尚无 10-K 数据。请填写股票代码并点击「🚀 启动买方深度审计」。")
        return
    if sec.get("error"):
        st.error(sec["error"])
        return

    # 顶部信息条
    m1, m2, m3 = st.columns(3)
    m1.metric("SEC 10-K 报送日期", sec.get("filing_date") or "N/A")
    m2.metric("Item 1A 提取字符数", f"{len(sec.get('item_1a_text', '')):,}")
    m3.metric("Item 1A 词数", f"{sec.get('word_count', 0):,}")
    if sec.get("warning"):
        st.warning(sec["warning"])

    with st.expander("📄 Item 1A 英文原文预览（免 Key 可查看）", expanded=False):
        preview = sec.get("item_1a_text", "")
        st.text_area(
            "原文",
            value=preview[:8000] + ("…（篇幅较长，此处仅预览前 8,000 字符）" if len(preview) > 8000 else ""),
            height=320,
            label_visibility="collapsed",
        )

    st.divider()
    st.subheader("🧠 AI 红旗审计")

    audit = st.session_state.get("audit")
    ticker_now = (st.session_state.get("audited_ticker") or "").upper()

    # 切换模型后，可基于已缓存原文重新审计（无需重复抓取）
    # Key 优先级：侧边栏即时输入 > 会话缓存；拿到有效 Key 立即写回缓存，
    # 避免「首次未填 Key → 后续补填 → 点重跑却报错」的状态丢失问题
    effective_key = api_key.strip() or st.session_state.get("last_api_key", "").strip()
    if effective_key:
        st.session_state["last_api_key"] = effective_key

    recol1, recol2 = st.columns([3, 1])
    with recol2:
        re_run = st.button("🔄 仅重跑 AI 审计（用当前模型）", use_container_width=True)
    if re_run:
        if not effective_key:
            st.error("请先在侧边栏填写 API Key。")
        else:
            provider_now = provider
            with st.spinner(f"🧠 {provider_now} · {model_final or '默认模型'} 正在执行红旗审计…"):
                st.session_state["audit"] = audit_risk_factors(
                    ticker=sec["ticker"],
                    item_1a_text=sec.get("item_1a_text", ""),
                    provider=provider_now,
                    api_key=effective_key,
                    model=model_final,
                    custom_base_url=custom_base_url,
                )
            st.session_state["audited_ticker"] = sec["ticker"]
            st.rerun()

    if audit is None:
        st.info("填写 API Key 并点击「🚀 启动买方深度审计」即可生成红旗报告；未填 Key 时上方原文仍可查看。")
        return

    if audit.get("error"):
        st.error(f"审计失败：{audit.get('error')}")
        if audit.get("hint"):
            st.info(audit["hint"])
        if audit.get("raw_preview"):
            with st.expander("模型原始输出预览（调试用）"):
                st.code(audit["raw_preview"], language="json")
        return

    # ---- 高管风险摘要看板 ----
    st.markdown(
        f"""
        <div class="exec-panel">
          <div style="font-size:.75rem;font-weight:700;letter-spacing:1.5px;color:#4B9BFF;margin-bottom:6px;">
            EXECUTIVE SUMMARY · 高管风险摘要
          </div>
          <div style="font-size:1.02rem;line-height:1.6;">{esc(audit.get('executive_summary', ''))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ---- 红旗卡片 ----
    concerns = audit.get("primary_concerns") or []
    st.caption(f"共识别 {len(concerns)} 项风险红旗")
    for flag in concerns:
        render_flag_card(flag)

    # ---- 导出 ----
    st.divider()
    md = build_markdown_report(sec["ticker"], fin or {}, sec, audit)
    st.download_button(
        label="📥 导出完整审计报告 (.md)",
        data=md,
        file_name=f"{sec['ticker']}_10K_Audit_Report.md",
        mime="text/markdown",
        use_container_width=True,
    )


# 记住最近一次使用的 Key（仅用于「重跑」按钮，不落盘）
if run_btn and api_key:
    st.session_state["last_api_key"] = api_key

# ---------------------------------------------------------------------------
# 主界面
# ---------------------------------------------------------------------------
st.markdown('<p class="title-text">🔍 10K-Auditor</p>', unsafe_allow_html=True)
st.markdown(
    '<p class="subtitle-text">美股上市公司资本配置与 10-K 财报红旗 AI 审计终端 · '
    "穿透账面净利润，审计自由现金流含金量与隐秘风险暗雷</p>",
    unsafe_allow_html=True,
)

tab1, tab2 = st.tabs(["📊 资本配置与利润含金量", "🚩 10-K 隐秘风险红旗"])

with tab1:
    fin = st.session_state.get("fin_data")
    if fin is None:
        st.info("尚无财务数据。请在侧边栏输入股票代码（默认 AAPL），点击「🚀 启动买方深度审计」。")
    else:
        render_finance_tab(fin)

with tab2:
    render_sec_tab(st.session_state.get("sec_data"), st.session_state.get("fin_data"))

st.caption("数据来源：yfinance / SEC EDGAR · Generated by 10K-Auditor · 仅供研究学习，不构成投资建议。")
