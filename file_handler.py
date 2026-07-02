"""File analysis — identify type, extract content, prepare for AI."""
import os
from pathlib import Path

# Max bytes to read from text files
MAX_TEXT_BYTES = 4000

TEXT_EXTS = {".txt", ".md", ".py", ".json", ".xml", ".html", ".css", ".js",
             ".ts", ".jsx", ".tsx", ".yaml", ".yml", ".ini", ".cfg", ".log",
             ".csv", ".sh", ".bat", ".ps1", ".java", ".c", ".cpp", ".h",
             ".rs", ".go", ".rb", ".php", ".sql", ".r", ".m", ".swift"}

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tiff"}

DOC_EXTS = {".docx", ".pdf", ".pptx", ".xlsx"}

def get_file_type(filepath):
    ext = Path(filepath).suffix.lower()
    if ext in TEXT_EXTS:
        return "text"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in DOC_EXTS:
        return "doc"
    return "unknown"

def get_file_info(filepath):
    """Return (file_type, description, content_text_or_none)."""
    ft = get_file_type(filepath)
    path = Path(filepath)
    name = path.name
    size = path.stat().st_size

    def size_fmt(n):
        if n < 1024:
            return f"{n}B"
        elif n < 1024 * 1024:
            return f"{n/1024:.1f}KB"
        else:
            return f"{n/(1024*1024):.1f}MB"

    info = f"文件名: {name}\n大小: {size_fmt(size)}\n类型: {ft}"

    content = None
    if ft == "text":
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            content = raw[:MAX_TEXT_BYTES]
            lines = raw.count("\n") + 1
            info += f"\n行数: {lines}"
            if len(raw) > MAX_TEXT_BYTES:
                info += f"\n(文件较大，仅读取前 {MAX_TEXT_BYTES} 字符)"
            info += f"\n\n--- 文件内容 ---\n{content}"
        except Exception as e:
            info += f"\n读取失败: {e}"

    elif ft == "image":
        try:
            from PIL import Image
            img = Image.open(filepath)
            info += f"\n尺寸: {img.width}x{img.height}"
            info += f"\n格式: {img.format}"
            info += f"\n模式: {img.mode}"
        except ImportError:
            info += "\n(PIL 未安装，无法读取图片尺寸)"

    elif ft == "doc":
        info += "\n(文档文件，请尝试用对应程序打开)"

    else:
        info += "\n(未知文件类型)"

    return ft, info, content

def build_analysis_prompt(filepath):
    """Build a system+user prompt pair for AI file analysis."""
    ft, info, content = get_file_info(filepath)
    name = Path(filepath).name

    system = ("你是一个 helpful 的桌面宠物助手，帮用户分析文件。"
              "回复简洁、友好、有用。用中文回答。")

    if ft == "text":
        user = (f"用户把文件「{name}」拖给你了。请分析以下文件内容，"
                f"用两三句话总结它的用途和关键信息：\n\n{info}")
    elif ft == "image":
        user = (f"用户把图片「{name}」拖给你了。这是一张图片文件。"
                f"请根据文件名和尺寸信息，用两句话描述它可能是什么，"
                f"并给出处理建议：\n\n{info}")
    elif ft == "doc":
        user = (f"用户把文档「{name}」拖给你了。"
                f"请解释这是什么类型的文档，并建议如何打开或处理：\n\n{info}")
    else:
        user = (f"用户把文件「{name}」拖给你了。"
                f"请识别这个文件类型并给出处理建议：\n\n{info}")

    return system, user
