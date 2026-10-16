"""
modules/ai_auditor.py
模块 3：全平台多供应商 AI 红旗审计网关

核心函数：audit_risk_factors(...) -> dict[str, Any]

设计要点：
  1. 统一走 OpenAI SDK 兼容模式，覆盖 OpenRouter / Gemini / DeepSeek /
     OpenAI / Groq / Custom 六大渠道。
  2. 不依赖特定平台易报 400 的 response_schema：System Prompt 强制纯 JSON
     输出 + 后端鲁棒清洗（剥围栏、截取 {..}、修尾随逗号）+ Pydantic 校验。
  3. 人设：顶级激进对冲基金首席法务与基本面审计师，专狙暗雷。
  4. 异常脱敏：401/402/403/404/429 与网络超时返回中文排障指引，
     绝不回显 API Key。
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

try:
    import openai
    from openai import OpenAI
except ImportError:  # 允许在未安装 openai 时导入本模块
    openai = None
    OpenAI = None

# ---------------------------------------------------------------------------
# 供应商路由表
# ---------------------------------------------------------------------------
PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "OpenRouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "models": [
            "openrouter/auto",
            "deepseek/deepseek-chat:free",
            "google/gemini-2.0-flash-exp:free",
            "openai/gpt-4o-mini",
        ],
        "extra_headers": {
            "HTTP-Referer": "https://github.com/",
            "X-Title": "10K-Auditor",
        },
    },
    "Google Gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "models": [
            "gemini-2.0-flash",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
            "gemini-2.5-flash-preview-05-20",
        ],
    },
    "DeepSeek": {
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "OpenAI": {
        "base_url": "",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
    },
    "Groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
    },
    "Custom (OpenAI Compatible)": {
        "base_url": "",
        "models": [],
    },
}

# ---------------------------------------------------------------------------
# 结构化输出 Pydantic 模型
# ---------------------------------------------------------------------------
class RiskFlag(BaseModel):
    category: str = Field(description="简短中文类别，如 诉讼风险 / 客户集中度 / 债务契约 / 技术淘汰")
    severity: str = Field(description="CRITICAL | HIGH | MEDIUM")
    flag_title: str = Field(description="20 字以内中文标题")
    evidence_quote: str = Field(description="逐字引用 1-2 句英文原句")
    analyst_take: str = Field(description="中文买方审辩分析：如何传导损害 FCF 与股东价值")

    @field_validator("severity")
    @classmethod
    def _normalize_severity(cls, v: str) -> str:
        v = str(v).upper().strip()
        mapping = {
            "CRITICAL": "CRITICAL", "严重": "CRITICAL",
            "HIGH": "HIGH", "高": "HIGH",
            "MEDIUM": "MEDIUM", "MED": "MEDIUM", "中": "MEDIUM", "LOW": "MEDIUM",
        }
        return mapping.get(v, "MEDIUM")


class AuditReport(BaseModel):
    executive_summary: str = Field(description="150 字以内中文高管摘要")
    primary_concerns: list[RiskFlag] = Field(description="3-5 个风险红旗")


# ---------------------------------------------------------------------------
# System Prompt（激进对冲基金买方审计师人设）
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """你是一位顶级激进对冲基金（Activist Hedge Fund）的首席法务与基本面审计师，以挖掘上市公司管理层刻意隐瞒或淡化表述的致命风险著称。

任务：审计下面这段美股 10-K 年报 Item 1A (Risk Factors) 原文，输出一份买方视角的隐秘风险红旗审计。

铁律：
1. 严禁输出「宏观经济波动」「市场不确定性」「行业竞争加剧」等套话。
2. 专门狙击隐晦诉讼、单一客户/供应商依赖、债务契约违约风险、技术淘汰、监管突击、关联交易、商誉与减值暗雷、表外承诺等具体暗雷。
3. evidence_quote 必须逐字引用 1-2 句英文原句（来自提供的原文，不得编造）。
4. category 使用简短中文类别（如「诉讼风险」「客户集中度」「债务契约」「技术淘汰」）。
5. severity 只能是 "CRITICAL" / "HIGH" / "MEDIUM" 三者之一。
6. flag_title 为 20 字以内中文标题；analyst_take 用中文说明该风险如何传导损害自由现金流（FCF）与股东价值。

输出格式（极其重要）：
- 仅输出一个纯 JSON 对象，禁止任何其他文字、解释、注释或 markdown 代码围栏。
- 结构如下：
{
  "executive_summary": "150 字以内中文高管摘要",
  "primary_concerns": [
    {
      "category": "中文类别",
      "severity": "CRITICAL|HIGH|MEDIUM",
      "flag_title": "20字内中文标题",
      "evidence_quote": "英文原句逐字引用",
      "analyst_take": "中文买方审辩分析"
    }
  ]
}
primary_concerns 输出 3-5 个 RiskFlag。"""

USER_PROMPT_TEMPLATE = """请审计以下 {ticker} 最新 10-K 年报 Item 1A (Risk Factors) 原文：

----- 原文开始 -----
{item_1a_text}
----- 原文结束 -----

请严格按照 System Prompt 的要求，仅输出纯 JSON 审计报告对象。"""

# ---------------------------------------------------------------------------
# 鲁棒 JSON 清洗
# ---------------------------------------------------------------------------
def _clean_json_text(raw: str) -> str:
    """剥离 markdown 代码围栏、截取首个 { 至末个 }、正则修复尾随逗号。"""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```\s*$", "", text, flags=re.I)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    # 修复对象/数组中的尾随逗号
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


# ---------------------------------------------------------------------------
# 异常中文排障指引
# ---------------------------------------------------------------------------
ERROR_HINTS: dict[int, str] = {
    401: "API Key 无效或未授权（401）。请检查 Key 是否复制完整、是否有多余空格，以及该 Key 是否有权访问所选模型。",
    402: "账户余额不足或未开通付费（402）。请前往对应供应商控制台充值，或改用免费模型（如 OpenRouter 的 :free 系列）。",
    403: "无权限访问该模型或地区受限（403）。请确认账号权限、模型 ID 拼写，或更换供应商。",
    404: "模型 ID 不存在或 Base URL 错误（404）。请在「手动指定模型 ID」框中填写该平台文档中的准确模型 ID；Custom 渠道请核对自定义 Base URL。",
    429: "请求频率超限或配额耗尽（429）。请稍后重试，或降低调用频率、更换模型。",
}

TIMEOUT_HINT = "请求超时。请检查网络环境（部分海外 API 需要代理），稍后重试。"
NETWORK_HINT = "网络连接失败。请检查网络连通性或代理设置后重试。"


def _error_dict(message: str, hint: str) -> dict[str, Any]:
    return {
        "error": message,
        "hint": hint,
        "executive_summary": None,
        "primary_concerns": [],
    }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def audit_risk_factors(
    ticker: str,
    item_1a_text: str,
    provider: str,
    api_key: str,
    model: str,
    custom_base_url: str = "",
) -> dict[str, Any]:
    """
    调用用户自选供应商的大模型，对 Item 1A 文本执行激进买方审计。

    返回 AuditReport 结构字典；失败时返回 {"error", "hint", ...}。
    全程不回显 API Key。
    """
    if openai is None:
        return _error_dict("openai SDK 未安装，请执行：pip install openai", "安装依赖后重试。")

    if not api_key or not api_key.strip():
        return _error_dict("未提供 API Key", "请在侧边栏填写所选供应商的 API Key。")
    if not item_1a_text or not item_1a_text.strip():
        return _error_dict("Item 1A 文本为空", "请先在 Tab 2 成功抓取 10-K 原文后再执行审计。")

    preset = PROVIDER_PRESETS.get(provider)
    if preset is None:
        return _error_dict(f"未知供应商：{provider}", "请从支持的 6 大渠道中选择。")

    base_url = preset["base_url"]
    if provider == "Custom (OpenAI Compatible)":
        base_url = (custom_base_url or "").strip()
        if not base_url:
            return _error_dict(
                "Custom 渠道未填写 Base URL",
                "请在侧边栏填写自定义的 OpenAI 兼容 Base URL，例如 http://localhost:11434/v1",
            )
    if not model:
        return _error_dict(
            "未指定模型 ID",
            "请选择预设模型，或在「手动指定模型 ID」框中填写目标平台文档中的准确模型 ID。",
        )

    # 长文本智能首尾采样：避免直接截断导致后半段风险暗雷被丢掉。
    # 超长时取前 16,000 + 省略提示 + 后 12,000 字符拼合送入 prompt。
    HEAD_CHARS = 16_000
    TAIL_CHARS = 12_000
    MAX_PROMPT_CHARS = 28_000
    if len(item_1a_text) > MAX_PROMPT_CHARS:
        text_for_prompt = (
            item_1a_text[:HEAD_CHARS]
            + "\n\n[...部分宏观叙述已省略，以下为年报后半段核心风险披露...]\n\n"
            + item_1a_text[-TAIL_CHARS:]
        )
    else:
        text_for_prompt = item_1a_text

    # 兼容不支持 system 角色的推理模型（deepseek-reasoner / R1 / o1 / o3 等）：
    # 将 SYSTEM_PROMPT 直接拼接到 user 内容前部，仅发送单条 role="user" 消息；
    # 其余模型保持 system + user 双消息结构。
    model_lower = model.lower()
    no_system_role = any(
        token in model_lower for token in ("reasoner", "r1", "o1", "o3")
    )
    user_content = USER_PROMPT_TEMPLATE.format(
        ticker=ticker.upper(), item_1a_text=text_for_prompt
    )
    if no_system_role:
        messages = [
            {
                "role": "user",
                "content": f"{SYSTEM_PROMPT}\n\n{user_content}",
            }
        ]
    else:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    try:
        client = OpenAI(
            api_key=api_key.strip(),
            base_url=base_url or None,
            default_headers=preset.get("extra_headers"),
            timeout=120.0,
        )
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as exc:  # 统一脱敏处理，绝不回显 Key
        status = getattr(exc, "status_code", None)
        name = type(exc).__name__
        if status in ERROR_HINTS:
            return _error_dict(f"API 请求失败（HTTP {status}）：{name}", ERROR_HINTS[status])
        if "Timeout" in name or "timeout" in str(exc).lower():
            return _error_dict("API 请求超时", TIMEOUT_HINT)
        if "Connection" in name:
            return _error_dict("网络连接失败", NETWORK_HINT)
        return _error_dict(
            f"API 调用异常：{name}",
            f"{NETWORK_HINT} 若持续失败，请检查模型 ID、Base URL 与供应商状态。原始信息：{str(exc)[:300]}",
        )

    if not raw.strip():
        return _error_dict("模型返回了空内容", "请更换模型或稍后重试。")

    cleaned = _clean_json_text(raw)
    try:
        report = AuditReport.model_validate_json(cleaned)
    except Exception as exc:
        return _error_dict(
            f"模型输出 JSON 校验失败：{exc}",
            "部分模型在长文本下容易输出不完整 JSON。建议：1) 换用更稳定的模型（如 deepseek-chat / gpt-4o-mini）；"
            "2) 检查原文是否过长。",
        ) | {"raw_preview": raw[:500]}

    out = report.model_dump()
    out["ticker"] = ticker.upper()
    out["provider"] = provider
    out["model"] = model
    return out
