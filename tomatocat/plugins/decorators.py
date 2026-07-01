"""插件装饰器：@tool 用于注册工具函数"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Callable


_PY_TO_JSON: dict[str, str] = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "dict": "object",
    "list": "array",
}


@dataclass
class ToolInfo:
    name: str
    func: Callable[..., Any]
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    plugin_id: str | None = None
    risk: str = "read-write"


_tool_registry: list[ToolInfo] = []


def tool(
    name: str,
    *,
    description: str | None = None,
    risk: str = "read-write",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    装饰器：将插件方法注册为工具

    用法：
        @tool("my_tool", description="这是一个示例工具")
        async def my_tool(self, event, param1: str, param2: int = 0) -> str:
            ...
    """
    def deco(func: Callable[..., Any]) -> Callable[..., Any]:
        desc = description or _extract_description(func.__doc__ or "")
        params = _derive_params_schema(func)
        info = ToolInfo(
            name=name,
            func=func,
            description=desc,
            parameters=params,
            risk=risk,
        )
        _tool_registry.append(info)
        func._tool_info = info  # type: ignore[attr-defined]
        return func
    return deco


def _extract_description(docstring: str) -> str:
    """从 docstring 提取第一行作为描述"""
    if not docstring:
        return ""
    lines = docstring.strip().split("\n")
    for line in lines:
        line = line.strip()
        if line:
            return line
    return ""


def _derive_params_schema(func: Callable[..., Any]) -> dict[str, Any]:
    """从函数签名推导 JSON Schema"""
    sig = inspect.signature(func)
    props: dict[str, Any] = {}
    required: list[str] = []

    for pname, param in sig.parameters.items():
        if pname in ("self", "event", "cls"):
            continue

        ann = param.annotation
        type_name = getattr(ann, "__name__", "string")
        json_type = _PY_TO_JSON.get(type_name, "string")

        prop: dict[str, Any] = {"type": json_type}

        default = param.default
        if default is inspect.Parameter.empty:
            required.append(pname)
        else:
            prop["default"] = default

        props[pname] = prop

    return {
        "type": "object",
        "properties": props,
        "required": required,
    }


def get_tool_definition(info: ToolInfo) -> dict[str, Any]:
    """将 ToolInfo 转换为 OpenAI 工具定义格式"""
    return {
        "type": "function",
        "function": {
            "name": info.name,
            "description": info.description,
            "parameters": info.parameters,
        },
    }
