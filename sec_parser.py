"""
modules/sec_parser.py
模块 2：SEC EDGAR 10-K 风险章节（Item 1A）提取器

核心函数：get_item_1a(ticker: str) -> dict[str, Any]

免 Key 官方数据链路：
  1. https://www.sec.gov/files/company_tickers.json   —— ticker -> CIK
  2. https://data.sec.gov/submissions/CIK{010d}.json  —— 定位最新一期 10-K
  3. https://www.sec.gov/Archives/edgar/data/...      —— 下载主文档 HTML 并清洗

三级降级提取策略：
  Tier 1 行锚定 / Tier 2 全文宽容正则 / Tier 3 兜底截断前 20,000 字符
"""
from __future__ import annotations

import re
import time
from collections import Counter
from typing import Any

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# 合规常量
# ---------------------------------------------------------------------------
# SEC_USER_AGENT 必须符合 SEC 官方规范：产品名/版本 (联系邮箱)
# 严禁使用 example.com 等占位符域名，极易被 EDGAR WAF 风控拦截（403）
SEC_USER_AGENT = "InvestmentResearchLab/1.0 (compliance@invresearch-audit.org)"
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
REQUEST_INTERVAL = 0.4          # SEC 限速：两次请求之间至少间隔
MAX_ITEM1A_CHARS = 30_000       # Item 1A 文本硬上限
TIER3_FALLBACK_CHARS = 20_000   # Tier 3 兜底截断长度

# 模块级 ticker->CIK 缓存，避免同一会话重复下载大 JSON
_TICKERS_CACHE: dict[str, int] | None = None

ITEM1A_START = re.compile(r"^\s*item\s*1a\.?\s*[.:\-–—]?\s*(risk\s+factors?)?", re.I)
NEXT_ITEM = re.compile(r"^\s*item\s*(?:1b|1c|2)\b", re.I)
TIER2_PATTERN = re.compile(
    r"item\s*1a\.?\s*(?:risk\s+factors?)?\b.*?(?=item\s*1b|item\s*1c|item\s*2\b)",
    re.I | re.S,
)


# ---------------------------------------------------------------------------
# 底层请求（带合规 UA 与限速）
# ---------------------------------------------------------------------------
def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"})
    return s


def _get(session: requests.Session, url: str, **kwargs: Any) -> requests.Response:
    time.sleep(REQUEST_INTERVAL)
    resp = session.get(url, timeout=kwargs.pop("timeout", 30), **kwargs)
    resp.raise_for_status()
    return resp


# ---------------------------------------------------------------------------
# Step 1: ticker -> CIK
# ---------------------------------------------------------------------------
def _ticker_to_cik(session: requests.Session, ticker: str) -> int:
    global _TICKERS_CACHE
    if _TICKERS_CACHE is None:
        data = _get(session, COMPANY_TICKERS_URL).json()
        _TICKERS_CACHE = {
            str(v.get("ticker", "")).upper(): int(v["cik_str"])
            for v in data.values()
        }
    normalized = ticker.upper().strip().replace(".", "-")
    cik = _TICKERS_CACHE.get(normalized)
    if cik is None:
        raise ValueError(
            f"SEC company_tickers.json 中未找到代码 {ticker}"
            "（请确认是美股代码；带点代码如 BRK.B 会自动转换为 BRK-B）。"
        )
    return cik


# ---------------------------------------------------------------------------
# Step 2: CIK -> 最新一期 10-K
# ---------------------------------------------------------------------------
def _latest_10k(session: requests.Session, cik: int) -> dict[str, str]:
    data = _get(session, f"https://data.sec.gov/submissions/CIK{cik:010d}.json").json()
    recent = data.get("filings", {}).get("recent", {}) or {}
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    dates = recent.get("filingDate", [])
    for form, acc, doc, date in zip(forms, accessions, docs, dates):
        if form == "10-K" and doc:
            return {
                "accession_number": acc,
                "primary_document": doc,
                "filing_date": date,
            }
    raise ValueError(f"CIK {cik} 的申报记录中未找到任何 10-K 年报。")


# ---------------------------------------------------------------------------
# Step 3: 下载主文档并清洗
# ---------------------------------------------------------------------------
def _fetch_document(session: requests.Session, cik: int, accession: str, doc: str) -> str:
    acc_nodash = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{doc}"
    resp = _get(session, url, timeout=60)
    resp.encoding = resp.encoding or "utf-8"
    return resp.text


def _html_to_lines(html_text: str) -> list[str]:
    """剔除 script/style/隐藏 XBRL 节点，输出纯净非空文本行。"""
    try:
        soup = BeautifulSoup(html_text, "lxml")
    except Exception:
        soup = BeautifulSoup(html_text, "html.parser")

    for tag in soup(["script", "style"]):
        tag.decompose()
    # 隐藏 XBRL 节点 <ix:hidden>
    for tag in soup.find_all(lambda t: t.name and t.name.lower() in ("ix:hidden", "ix:nonnumeric")):
        tag.decompose()
    # display:none 元素
    for tag in soup.find_all(style=True):
        style = tag.get("style", "")
        if "display:none" in style.replace(" ", "").lower() or "display: none" in style.lower():
            tag.decompose()

    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln]


# ---------------------------------------------------------------------------
# 文本后处理
# ---------------------------------------------------------------------------
def _looks_like_toc(lines: list[str]) -> bool:
    """目录跳转项特征：含大量点号引导符或大量纯页码行。"""
    dotted = sum(1 for ln in lines if re.search(r"\.{4,}", ln))
    pagenums = sum(1 for ln in lines if ln.isdigit())
    return dotted >= 2 or (pagenums >= 2 and len(lines) < 40)


def _clean_segment(seg: str) -> str:
    """剔除重复页眉与纯页码行，并规范化换行结构。"""
    lines = [ln.strip() for ln in seg.split("\n") if ln.strip()]
    counts = Counter(lines)
    seen: set[str] = set()
    out: list[str] = []
    for ln in lines:
        if ln.isdigit():
            continue
        if counts[ln] > 3 and len(ln) < 150:
            if ln in seen:
                continue
            seen.add(ln)
        out.append(ln)
    text = "\n".join(out)
    # 换行规范化：3 个及以上连续换行压缩为自然的段落双换行 \n\n，
    # 行尾多余空白清除，保证大模型读取时段落结构清晰
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# 三级提取策略
# ---------------------------------------------------------------------------
def _tier1(lines: list[str]) -> str | None:
    """行锚定：Item 1A 行首 -> 下一章节（1B/1C/2），过滤目录与短片段。"""
    candidates: list[str] = []
    n = len(lines)
    i = 0
    while i < n:
        if ITEM1A_START.match(lines[i]):
            j = i + 1
            while j < n and not NEXT_ITEM.match(lines[j]):
                j += 1
            seg_lines = lines[i:j]
            seg = "\n".join(seg_lines)
            if len(seg) >= 1000 and not _looks_like_toc(seg_lines):
                candidates.append(seg)
            i = max(j, i + 1)
        else:
            i += 1
    if candidates:
        # 正文片段通常远长于目录项，取最长者
        return max(candidates, key=len)
    return None


def _tier2(full_text: str) -> str | None:
    """全文宽容正则：跨行 / 断词匹配，倒序取最后一个有效正文段落。"""
    best: str | None = None
    for m in TIER2_PATTERN.finditer(full_text):
        seg = m.group(0)
        if len(seg) >= 1000:
            best = seg
    return best


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def get_item_1a(ticker: str) -> dict[str, Any]:
    """
    抓取最新一期 10-K 的 Item 1A (Risk Factors) 纯文本。

    返回:
        {
          "ticker":          str,
          "filing_date":     str,
          "item_1a_text":    str,   # 硬上限 30,000 字符
          "word_count":      int,
          "warning":         str | None,
          "error":           str | None,
        }
    """
    result: dict[str, Any] = {
        "ticker": str(ticker).upper().strip(),
        "filing_date": None,
        "item_1a_text": "",
        "word_count": 0,
        "warning": None,
        "error": None,
    }
    session = _session()
    try:
        cik = _ticker_to_cik(session, result["ticker"])
        filing = _latest_10k(session, cik)
        result["filing_date"] = filing["filing_date"]
        html_text = _fetch_document(
            session, cik, filing["accession_number"], filing["primary_document"]
        )
    except requests.exceptions.RequestException as exc:
        result["error"] = f"SEC EDGAR 网络请求失败：{exc}"
        return result
    except ValueError as exc:
        result["error"] = str(exc)
        return result
    except Exception as exc:
        result["error"] = f"SEC 10-K 解析失败：{exc}"
        return result

    lines = _html_to_lines(html_text)
    full_text = "\n".join(lines)

    # Tier 1：行锚定
    segment = _tier1(lines)
    if segment is not None:
        text = _clean_segment(segment)
    else:
        # Tier 2：全文宽容正则
        result["warning"] = "行锚定未命中，已降级为全文正则宽松匹配。"
        text = _tier2(full_text) or ""

    if not text:
        # Tier 3：兜底保护，绝不崩溃
        result["warning"] = (
            "非标排版导致 Item 1A 无法定位，已截取正文前 "
            f"{TIER3_FALLBACK_CHARS:,} 字符作为兜底文本。"
        )
        text = full_text[:TIER3_FALLBACK_CHARS]

    if len(text) > MAX_ITEM1A_CHARS:
        text = text[:MAX_ITEM1A_CHARS]
        extra = f"文本已截断至 {MAX_ITEM1A_CHARS:,} 字符上限。"
        result["warning"] = f"{result['warning']} {extra}" if result["warning"] else extra

    result["item_1a_text"] = text
    result["word_count"] = len(text.split())
    return result
