import json
import os
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("arxiv")

ACK_FILE = Path(__file__).parent / "ack_state.json"
LAST_FETCH_FILE = Path(__file__).parent / "last_fetch.json"

ARXIV_BASE = "http://export.arxiv.org/api/query"

CATEGORIES = os.environ.get("ARXIV_CATS", "cs.CL,cs.AI,cs.LG,cs.MA,cs.RO").split(",")
MAX_RESULTS = int(os.environ.get("ARXIV_MAX", "20"))
KEYWORDS = os.environ.get("ARXIV_KEYWORDS", "agent,LLM,large language model")
POLL_HOURS = int(os.environ.get("ARXIV_POLL_HOURS", "6"))


def _load_acks() -> dict[str, float]:
    try:
        return json.loads(ACK_FILE.read_text(encoding="utf-8")) if ACK_FILE.exists() else {}
    except Exception:
        return {}


def _save_acks(acks: dict[str, float]) -> None:
    ACK_FILE.write_text(json.dumps(acks, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_last_fetch() -> dict:
    try:
        return json.loads(LAST_FETCH_FILE.read_text(encoding="utf-8")) if LAST_FETCH_FILE.exists() else {}
    except Exception:
        return {}


def _save_last_fetch(data: dict) -> None:
    LAST_FETCH_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_query() -> str:
    cat_parts = [f"cat:{c.strip()}" for c in CATEGORIES if c.strip()]
    kw_parts = []
    for kw in KEYWORDS.split(","):
        kw = kw.strip()
        if kw:
            kw_parts.append(f'all:"{kw}"')
    query_parts = []
    if cat_parts:
        query_parts.append("(" + " OR ".join(cat_parts) + ")")
    if kw_parts:
        query_parts.append("(" + " OR ".join(kw_parts) + ")")
    return " AND ".join(query_parts) if query_parts else "cat:cs.AI"


def _fetch_papers() -> list[dict]:
    query = _build_query()
    params = {
        "search_query": query,
        "start": 0,
        "max_results": MAX_RESULTS,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = ARXIV_BASE + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "akashic-agent/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read().decode("utf-8")
    except Exception:
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(data)
    except Exception:
        return []

    papers = []
    for entry in root.findall("atom:entry", ns):
        try:
            arxiv_id = entry.find("atom:id", ns).text.strip()
            title = entry.find("atom:title", ns).text.strip().replace("\n", " ")
            summary = entry.find("atom:summary", ns).text.strip().replace("\n", " ")
            published = entry.find("atom:published", ns).text
            authors = [
                a.find("atom:name", ns).text
                for a in entry.findall("atom:author", ns)
            ]
            cats = [c.get("term") for c in entry.findall("atom:category", ns)]
            pdf_link = ""
            for link in entry.findall("atom:link", ns):
                if link.get("title") == "pdf":
                    pdf_link = link.get("href", "")
                    break
            abs_link = arxiv_id

            papers.append({
                "id": arxiv_id,
                "title": title,
                "summary": summary,
                "published": published,
                "authors": authors[:5],
                "categories": cats,
                "pdf_url": pdf_link,
                "abs_url": abs_link,
            })
        except Exception:
            continue

    return papers


def _score_paper(paper: dict) -> float:
    score = 0.0
    title_lower = paper["title"].lower()
    summary_lower = paper["summary"].lower()

    agent_kw = ["agent", "agents", "multi-agent", "multiagent", "autonomous"]
    llm_kw = ["large language model", "llm", "gpt", "transformer"]
    hot_kw = ["reasoning", "planning","tool use", "rag", "finetune", "alignment"]

    for kw in agent_kw:
        if kw in title_lower:
            score += 3.0
        elif kw in summary_lower:
            score += 1.0

    for kw in llm_kw:
        if kw in title_lower:
            score += 2.0
        elif kw in summary_lower:
            score += 0.5

    for kw in hot_kw:
        if kw in title_lower:
            score += 1.5

    if "cs.cl" in [c.lower() for c in paper["categories"]]:
        score += 1.0
    if "cs.ai" in [c.lower() for c in paper["categories"]]:
        score += 0.5

    return score


def _format_paper_content(paper: dict) -> str:
    authors = ", ".join(paper["authors"][:3])
    if len(paper["authors"]) > 3:
        authors += " 等"
    summary = paper["summary"][:200]
    if len(paper["summary"]) > 200:
        summary += "..."
    cats = ", ".join(paper["categories"][:5])
    return (
        f"标题：{paper['title']}\n"
        f"作者：{authors}\n"
        f"分类：{cats}\n"
        f"发布：{paper['published'][:10]}\n"
        f"摘要：{summary}\n"
        f"链接：{paper['abs_url']}"
    )


@mcp.tool()
def get_proactive_events() -> str:
    acks = _load_acks()
    now = time.time()
    today = time.strftime("%Y-%m-%d", time.localtime())

    last_fetch = _load_last_fetch()
    last_time = last_fetch.get("timestamp", 0)
    cached_papers = last_fetch.get("papers", [])

    if now - last_time < POLL_HOURS * 3600 and cached_papers:
        papers = cached_papers
    else:
        papers = _fetch_papers()
        if papers:
            _save_last_fetch({"timestamp": now, "papers": papers})
        elif cached_papers:
            papers = cached_papers
        else:
            return json.dumps([], ensure_ascii=False)

    if not papers:
        return json.dumps([], ensure_ascii=False)

    scored = []
    for p in papers:
        pid = p["id"].replace("http://arxiv.org/abs/", "")
        event_id = f"arxiv_{pid}"
        if event_id in acks and now < acks[event_id]:
            continue
        score = _score_paper(p)
        scored.append((score, p, event_id))

    scored.sort(key=lambda x: x[0], reverse=True)

    events = []
    for score, paper, event_id in scored[:5]:
        content = _format_paper_content(paper)
        events.append({
            "kind": "content",
            "event_id": event_id,
            "source_type": "arxiv",
            "source_name": "arXiv",
            "title": f"[论文] {paper['title'][:60]}",
            "content": content,
            "severity": "normal",
            "score": round(score, 1),
        })

    return json.dumps(events, ensure_ascii=False)


@mcp.tool()
def acknowledge_events(event_ids: list[str], ttl_hours: int = 0) -> str:
    acks = _load_acks()
    until = time.time() + ttl_hours * 3600 if ttl_hours > 0 else float("inf")
    for eid in event_ids:
        acks[eid] = until
    _save_acks(acks)
    return json.dumps({"ok": True, "acked": len(event_ids)})


@mcp.tool()
def get_context() -> str:
    last_fetch = _load_last_fetch()
    papers = last_fetch.get("papers", [])
    if not papers:
        papers = _fetch_papers()
        if papers:
            _save_last_fetch({"timestamp": time.time(), "papers": papers})

    if not papers:
        return json.dumps({"available": False}, ensure_ascii=False)

    latest_titles = [p["title"] for p in papers[:5]]
    context = {
        "available": True,
        "latest_papers_count": len(papers),
        "latest_titles": latest_titles,
    }
    return json.dumps(context, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
