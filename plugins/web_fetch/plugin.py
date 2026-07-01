"""网页抓取插件 - 抓取 URL 内容，支持 text/markdown/html 格式

参考 tomatocat 的 agent/tools/web_fetch.py 实现，
适配 v2 的 @tool 装饰器系统，用 httpx 替代 core.net.http。
保留 SSRF 防护、5MB 限制、50000 字符截断。
html2text / lxml 为可选依赖，未安装时自动降级为正则清理。
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from tomatocat.plugins import Plugin, tool

log = logging.getLogger(__name__)

# 可选依赖：html2text / lxml
try:
    import html2text
    _HAS_HTML2TEXT = True
except ImportError:
    _HAS_HTML2TEXT = False
    log.info("[web_fetch] html2text 未安装，HTML→Markdown 将使用简化版")

try:
    from lxml import html as lxml_html
    from lxml.etree import ParserError
    _HAS_LXML = True
except ImportError:
    _HAS_LXML = False

_MAX_BYTES = 5 * 1024 * 1024  # 5MB
_DEFAULT_TIMEOUT = 30
_MAX_TIMEOUT = 120
_USER_AGENT = "tomatocat/0.1.0"
_MAX_TEXT_CHARS = 50_000

_ACCEPT = {
    "markdown": "text/markdown;q=1.0, text/x-markdown;q=0.9, text/plain;q=0.8, text/html;q=0.7, */*;q=0.1",
    "text": "text/plain;q=1.0, text/markdown;q=0.9, text/html;q=0.8, */*;q=0.1",
    "html": "text/html;q=1.0, application/xhtml+xml;q=0.9, text/plain;q=0.8, */*;q=0.1",
}


class WebFetchPlugin(Plugin):
    name = "web_fetch"
    desc = "网页抓取（URL → text/markdown/html）"

    @tool(
        name="web_fetch",
        description=(
            "抓取指定 URL 的内容并返回。"
            "支持 text（纯文本）、markdown（转换后的 Markdown，默认）、html（原始 HTML）三种格式。"
            "仅支持 HTTP/HTTPS，响应上限 5MB。"
        ),
    )
    async def web_fetch(
        self,
        event: object,
        url: str,
        format: str = "markdown",
        timeout: int = 30,
    ) -> str:
        """抓取指定 URL 的内容

        Args:
            url: 要抓取的完整 URL，必须以 http:// 或 https:// 开头
            format: 返回格式 text/markdown/html，默认 markdown
            timeout: 超时秒数，默认 30，最大 120
        """
        # URL 安全校验
        if not url.startswith(("http://", "https://")):
            return _err(url, "URL 必须以 http:// 或 https:// 开头")
        ssrf_err = _validate_url_target(url)
        if ssrf_err:
            return _err(url, ssrf_err)

        timeout = min(max(1, int(timeout)), _MAX_TIMEOUT)

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": _USER_AGENT,
                        "Accept": _ACCEPT.get(format, "*/*"),
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    },
                )
        except httpx.TimeoutException:
            return _err(url, f"请求超时（>{timeout}s）")
        except httpx.ConnectError:
            return _err(url, "无法建立连接")
        except httpx.RequestError as e:
            return _err(url, f"请求失败：{e}")
        except Exception as e:
            return _err(url, f"请求失败：{e}")

        if resp.status_code != 200:
            return _err(url, f"HTTP {resp.status_code}")

        # 5MB 检查
        body = resp.content
        if len(body) > _MAX_BYTES:
            return _err(url, "响应过大（超过 5MB 限制）")

        content_type = resp.headers.get("content-type", "")
        encoding = resp.encoding or "utf-8"
        is_html = "text/html" in content_type
        is_binary = any(
            ct in content_type
            for ct in (
                "application/pdf",
                "application/octet-stream",
                "image/",
                "video/",
                "audio/",
            )
        )

        if is_binary:
            return _err(url, f"不支持二进制内容（{content_type}），请使用能处理该格式的专用工具")

        if format == "html":
            text = body.decode(encoding, errors="replace")
        elif format == "markdown" and is_html:
            text = _to_markdown(body.decode(encoding, errors="replace"))
        elif format == "text" and is_html:
            text = _to_text(body)
        else:
            text = body.decode(encoding, errors="replace")

        # 截断过长文本
        truncated = False
        if len(text) > _MAX_TEXT_CHARS:
            text = text[:_MAX_TEXT_CHARS]
            truncated = True

        result: dict[str, Any] = {
            "url": url,
            "final_url": str(resp.url),
            "status": resp.status_code,
            "content_type": content_type,
            "format": format,
            "length": len(text),
            "text": text,
        }
        if truncated:
            result["truncated"] = True
            result["note"] = f"内容已截断至 {_MAX_TEXT_CHARS} 字符"

        return json.dumps(result, ensure_ascii=False)


# ── 模块级工具函数 ────────────────────────────────────────────


def _err(url: str, msg: str) -> str:
    return json.dumps({"error": msg, "url": url}, ensure_ascii=False)


def _validate_url_target(url: str) -> str | None:
    """SSRF 防护：拒绝内网/回环/保留地址。"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return "URL 缺少主机名"
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
            return f"禁止访问内网/本地地址：{host}"
    except ValueError:
        if host.endswith(".local") or host.endswith(".localhost"):
            return f"禁止访问本地域名：{host}"
    return None


def _to_markdown(raw_html: str) -> str:
    """HTML → Markdown"""
    if _HAS_HTML2TEXT:
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = False
        h.body_width = 0
        h.unicode_snob = True
        h.protect_links = True
        return h.handle(raw_html).strip()
    return _strip_html_fallback(raw_html)


def _to_text(content: bytes) -> str:
    """HTML → 纯文本"""
    if _HAS_LXML:
        try:
            doc = lxml_html.fromstring(content)
        except ParserError:
            return content.decode("utf-8", errors="replace")
        for tag in ("script", "style", "noscript", "iframe", "object", "embed"):
            for el in doc.xpath(f"//{tag}"):
                parent = el.getparent()
                if parent is not None:
                    parent.remove(el)
        return " ".join(doc.text_content().split())
    return _strip_html_fallback(content.decode("utf-8", errors="replace"))


def _strip_html_fallback(raw_html: str) -> str:
    """简化版 HTML 清理（无第三方库时使用）"""
    raw_html = re.sub(
        r"<(script|style)[^>]*>.*?</\1>",
        "",
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<[^>]+>", " ", raw_html)
    return " ".join(text.split())
