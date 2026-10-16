# 10K-Auditor · 美股上市公司资本配置与 10-K 财报红旗 AI 审计终端

> **穿透账面净利润，审计「自由现金流含金量」与「10-K 隐秘风险暗雷」。**
> A buy-side style AI terminal that audits FCF quality, SBC dilution, capital allocation, and hidden risk factors in 10-K filings.

---

## 🇨🇳 中文介绍

### 项目背景

账面上的净利润可能是故事，只有现金流才是事实。**10K-Auditor** 以激进对冲基金买方的视角，从两个维度审计一家美股公司：

1. **量化端**——穿透账面净利润，审计自由现金流（FCF）含金量、股权激励（SBC）稀释率，以及经营现金流（CFO）的资本配置去向；
2. **定性端**——自动从 SEC EDGAR 官方接口抓取最新 10-K 年报 Item 1A (Risk Factors)，以「激进对冲基金首席审计师」人设挖掘管理层隐瞒或淡化的致命风险红旗；
3. **架构端**——零成本、纯前端内存保存的 **BYOK (Bring Your Own Key)** 多模型通用网关，全球任何用户使用市面上任何主流 API 都能顺利跑通。

### 功能亮点

- 📊 **资本配置与利润含金量（免 Key 直接可用）**
  - 4 张核心记分卡：净利润、FCF、SBC/FCF 稀释率、股东回报/CFO 占比
  - 历年净利润 vs FCF 对比柱状图 + SBC 折线（含金量趋势一目了然）
  - CFO 现金流去向拆解堆叠柱状图（CapEx / 回购 / 分红 / 偿债）
  - 完整格式化财务数据表 + yfinance 脏数据降级警告
- 🚩 **10-K 隐秘风险红旗（AI 驱动）**
  - 免 Key 抓取 SEC EDGAR 官方 10-K Item 1A 原文，可折叠预览
  - 高管风险摘要看板 + 按 CRITICAL / HIGH / MEDIUM 分级渲染的红旗卡片
  - 每项红旗包含：中文类别、20 字内标题、**英文原文逐字铁证引用**、中文买方审辩分析
  - 一键导出完整 Markdown 审计报告
- 🧠 **全平台多供应商 AI 网关**
  - 6 大渠道预设：OpenRouter / Google Gemini / DeepSeek / OpenAI / Groq / Custom (OpenAI 兼容)
  - 预设模型 + **手动模型 ID 覆盖输入**：未来任何新模型发布，直接填入即可使用
  - 不依赖易报 400 的 response_schema，纯 JSON 提示词 + 鲁棒清洗 + Pydantic 校验
  - 401/402/403/404/429 与网络异常返回**中文排障指引**，绝不回显 API Key
- 🛡️ **高鲁棒性数据引擎**
  - yfinance 字段别名归一化、MultiIndex 自动拍平、防零/防 inf 除法
  - SEC 提取三级降级策略（行锚定 → 全文正则 → 兜底截断），绝不崩溃

### 支持的全平台 API 列表

| 供应商 | Base URL | 预设模型示例 |
|---|---|---|
| OpenRouter | `https://openrouter.ai/api/v1` | `openrouter/auto`、`deepseek/deepseek-chat:free`、`google/gemini-2.0-flash-exp:free` |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-2.0-flash`、`gemini-1.5-pro` |
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat`、`deepseek-reasoner` |
| OpenAI | 官方默认 | `gpt-4o-mini`、`gpt-4o` |
| Groq | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile`、`llama-3.1-8b-instant` |
| Custom (OpenAI 兼容) | 用户自填 | Ollama / vLLM / OneAPI / LiteLLM 等任意兼容端点 |

### 目录结构

```
10K-Auditor/
├── app.py                  # Streamlit 主程序（双 Tab 交互终端）
├── requirements.txt        # 生产环境依赖
├── README.md
└── modules/
    ├── __init__.py
    ├── finance_data.py     # 模块 1：yfinance 财务与资本配置计算引擎
    ├── sec_parser.py       # 模块 2：SEC EDGAR 10-K 原文抓取与清洗引擎
    └── ai_auditor.py       # 模块 3：全平台多供应商 AI 红旗审计网关
```

### 本地运行

```bash
git clone https://github.com/<your-username>/10K-Auditor.git
cd 10K-Auditor
pip install -r requirements.txt
streamlit run app.py
```

浏览器打开 `http://localhost:8501` 即可使用。

### Streamlit Cloud 一键部署

1. 将本项目 Fork 到你的 GitHub 账号（Public 仓库）；
2. 登录 [share.streamlit.io](https://share.streamlit.io)，点击 **New app**；
3. 选择仓库、分支 `main`、主文件 `app.py`，点击 **Deploy**；
4. 等待构建完成（约 1-2 分钟），即可获得公开访问链接；
5. 每位用户在侧边栏填入**自己的 API Key** 即可使用（BYOK，Key 仅存于浏览器会话内存，不会持久化或上传）。

### 免责声明

本项目仅供研究学习与技术交流，所有数据来自公开信息源（yfinance / SEC EDGAR），
AI 生成的审计结论不构成任何投资建议。

---

## 🇬🇧 English

### About

Book net income can be a story; only cash flow is a fact. **10K-Auditor** audits a US-listed company from two angles with a buy-side activist lens:

1. **Quantitative** — looks through reported net income to audit **Free Cash Flow (FCF) quality**, **Stock-Based Compensation (SBC) dilution**, and where **Cash Flow from Operations (CFO)** is actually allocated (CapEx, buybacks, dividends, debt repayment).
2. **Qualitative** — automatically pulls the latest 10-K **Item 1A (Risk Factors)** from the official **SEC EDGAR** API and surfaces hidden red flags (buried litigation, customer/supplier concentration, debt covenant risk, technology obsolescence) written in an activist-hedge-fund chief auditor persona.
3. **Architecture** — a zero-cost, browser-only **BYOK (Bring Your Own Key)** multi-provider AI gateway that works with any mainstream LLM API on Earth.

### Highlights

- 📊 **Capital allocation & earnings quality tab** — works without any API key: metric scorecards, Net Income vs FCF chart with SBC overlay, CFO allocation stacked bars, full data table with data-quality warnings.
- 🚩 **10-K risk red flags tab** — Item 1A pulled directly from SEC EDGAR (no key needed, official User-Agent, rate-limited), executive summary panel, severity-graded red-flag cards with verbatim English evidence quotes and Chinese buy-side analysis, one-click Markdown report export.
- 🧠 **Multi-provider AI gateway** — 6 presets (OpenRouter / Google Gemini / DeepSeek / OpenAI / Groq / Custom OpenAI-compatible) + a **manual model-ID override** input so any future model just works. No reliance on fragile `response_schema`; strict JSON prompting + robust cleaning + Pydantic validation. Friendly Chinese troubleshooting hints for 401/402/403/404/429/timeouts, and API keys are never echoed.
- 🛡️ **Bulletproof data engines** — yfinance field-alias normalization, MultiIndex flattening, zero/inf-safe math, and a 3-tier SEC text extraction fallback that never crashes.

### Quick Start

```bash
git clone https://github.com/<your-username>/10K-Auditor.git
cd 10K-Auditor
pip install -r requirements.txt
streamlit run app.py
```

### Deploy on Streamlit Cloud

1. Fork this repo (public);
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**;
3. Select the repo, branch `main`, main file `app.py`, **Deploy**;
4. Each user brings their own API key in the sidebar (keys live only in browser session memory).

### Disclaimer

For research and educational purposes only. Data comes from public sources (yfinance / SEC EDGAR). AI-generated audit opinions do not constitute investment advice.
