import json
import os
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("rss")

ACK_FILE = Path(__file__).parent / "ack_state.json"
LAST_FETCH_FILE = Path(__file__).parent / "last_fetch.json"

POLL_HOURS = int(os.environ.get("RSS_POLL_HOURS", "6"))


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


def _parse_feeds() -> list[dict]:
    """从环境变量解析 RSS 源配置"""
    raw = os.environ.get("RSS_FEEDS", "")
    if not raw:
        return []
    feeds = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) >= 2:
            name = parts[0].strip()
            url = parts[1].strip()
            cats = [c.strip() for c in parts[2].split(",")] if len(parts) > 2 else []
            feeds.append({"name": name, "url": url, "categories": cats})
        elif len(parts) == 1 and parts[0].startswith("http"):
            feeds.append({"name": parts[0], "url": parts[0], "categories": []})
    return feeds


def _fetch_feed(feed: dict) -> list[dict]:
    try:
        req = urllib.request.Request(
            feed["url"],
            headers={"User-Agent": "akashic-agent/1.0", "Accept": "application/rss+xml, application/xml, text/xml"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return []

    try:
        root = ET.fromstring(data)
    except Exception:
        return []

    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("{")[1].rsplit("}", 1)[0] + "/"

    articles = []
    for item in root.findall(f".//{ns}item"):
        try:
            title_el = item.find(f"{ns}title") or item.find("title")
            link_el = item.find(f"{ns}link") or item.find("link")
            desc_el = item.find(f"{ns}description") or item.find("description")
            pub_el = item.find(f"{ns}pubDate") or item.find("pubDate")
            cat_el = item.find(f"{ns}category") or item.find("category")

            title = (title_el.text or "").strip() if title_el is not None else ""
            link = (link_el.text or "").strip() if link_el is not None else ""
            desc = (desc_el.text or "").strip() if desc_el is not None else ""
            pub = (pub_el.text or "")[:16] if pub_el is not None else ""
            cat = (cat_el.text or "").strip() if cat_el is not None else ""

            if not title or not link:
                continue

            articles.append({
                "title": title,
                "link": link,
                "description": desc[:300] if desc else "",
                "published": pub,
                "category": cat,
                "source": feed["name"],
                "source_categories": feed.get("categories", []),
            })
        except Exception:
            continue

    return articles


def _score_article(article: dict) -> float:
    score = 0.0
    title_lower = article["title"].lower()
    desc_lower = article["description"].lower()

    keywords = os.environ.get("RSS_KEYWORDS", "AI,人工智能,大模型,机器学习,深度学习,LLM,Agent,自动化").split(",")
    for kw in keywords:
        kw = kw.strip().lower()
        if kw and kw in title_lower:
            score += 3.0
        elif kw and kw in desc_lower:
            score += 1.0

    return score


def _format_article_content(article: dict) -> str:
    desc = article["description"]
    if not desc:
        desc = "无描述"
    return (
        f"标题：{article['title']}\n"
        f"来源：{article['source']}\n"
        f"分类：{article['category'] or '未知'}\n"
        f"发布时间：{article['published'] or '未知'}\n"
        f"摘要：{desc[:150]}...\n"
        f"链接：{article['link']}"
    )


@mcp.tool()
def get_proactive_events() -> str:
    acks = _load_acks()
    now = time.time()

    last_fetch = _load_last_fetch()
    last_time = last_fetch.get("timestamp", 0)
    cached_articles = last_fetch.get("articles", [])

    if now - last_time < POLL_HOURS * 3600 and cached_articles:
        articles = cached_articles
    else:
        feeds = _parse_feeds()
        all_articles = []
        for feed in feeds:
            fetched = _fetch_feed(feed)
            all_articles.extend(fetched)
        if all_articles:
            _save_last_fetch({"timestamp": now, "articles": all_articles})
            articles = all_articles
        elif cached_articles:
            articles = cached_articles
        else:
            return json.dumps([], ensure_ascii=False)

    if not articles:
        return json.dumps([], ensure_ascii=False)

    scored = []
    for article in articles:
        event_id = f"rss_{hash(article['link'])}"
        if event_id in acks and now < acks[event_id]:
            continue
        score = _score_article(article)
        scored.append((score, article, event_id))

    scored.sort(key=lambda x: x[0], reverse=True)

    events = []
    for score, article, event_id in scored[:5]:
        content = _format_article_content(article)
        events.append({
            "kind": "content",
            "event_id": event_id,
            "source_type": "rss",
            "source_name": article["source"],
            "title": f"[{article['source']}] {article['title'][:50]}",
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
    articles = last_fetch.get("articles", [])
    if not articles:
        feeds = _parse_feeds()
        all_articles = []
        for feed in feeds:
            fetched = _fetch_feed(feed)
            all_articles.extend(fetched)
        if all_articles:
            _save_last_fetch({"timestamp": time.time(), "articles": all_articles})
            articles = all_articles

    if not articles:
        return json.dumps({"available": False}, ensure_ascii=False)

    latest_titles = [a["title"] for a in articles[:5]]
    return json.dumps({
        "available": True,
        "latest_articles_count": len(articles),
        "latest_titles": latest_titles,
    })


if __name__ == "__main__":
    mcp.run(transport="stdio")
