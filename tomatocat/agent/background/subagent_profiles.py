"""
子 Agent Profile 配置：根据不同任务类型提供不同的工具集和权限。

Profile:
    research  — 只读调研（默认，最小权限）
    scripting — 执行型，可运行命令和写文件，禁止网络
    general   — 两者兼有，仅在明确需要时使用
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from ..llm import LLMProvider
from ..subagent import SubAgent

PROFILE_RESEARCH = "research"
PROFILE_SCRIPTING = "scripting"
PROFILE_GENERAL = "general"


@dataclass(frozen=True)
class SubagentRuntime:
    provider: LLMProvider
    model: str
    max_tokens: int
    tool_hooks: list[Any] = field(default_factory=list)


@dataclass
class SubagentSpec:
    tools: list[Any]
    system_prompt: str = ""
    max_iterations: int = 30
    mandatory_exit_tools: Sequence[str] = field(default_factory=tuple)

    def build(self, runtime: SubagentRuntime) -> SubAgent:
        agent = SubAgent(
            provider=runtime.provider,
            model=runtime.model,
            tools=self.tools,
            system_prompt=self.system_prompt,
            max_iterations=self.max_iterations,
            max_tokens=runtime.max_tokens,
            mandatory_exit_tools=self.mandatory_exit_tools,
        )
        if runtime.tool_hooks:
            agent.add_tool_hooks(runtime.tool_hooks)
        return agent


def _build_research_tools(
    workspace: Path,
    *,
    include_list_dir: bool = True,
) -> list[Any]:
    """构建只读调研工具集。"""
    from plugins.web_search.plugin import plugin as web_search_plugin
    from plugins.web_fetch.plugin import plugin as web_fetch_plugin
    from plugins.filesystem.plugin import plugin as filesystem_plugin

    tools = []
    if web_search_plugin and getattr(web_search_plugin, "tools", None):
        for tool in web_search_plugin.tools.values():
            tools.append(tool)
    if web_fetch_plugin and getattr(web_fetch_plugin, "tools", None):
        for tool in web_fetch_plugin.tools.values():
            tools.append(tool)
    if filesystem_plugin and getattr(filesystem_plugin, "tools", None):
        for name, tool in filesystem_plugin.tools.items():
            if name in ("read_file", "list_dir"):
                tools.append(tool)
    return tools


def _build_scripting_tools(task_dir: Path) -> list[Any]:
    """构建执行型工具集（可写文件、运行命令）。"""
    from plugins.shell.plugin import plugin as shell_plugin

    tools = []
    if shell_plugin and getattr(shell_plugin, "tools", None):
        for tool in shell_plugin.tools.values():
            tools.append(tool)
    return tools


def build_research_spec(
    *,
    workspace: Path,
    task_dir: Path,
    system_prompt: str,
    max_iterations: int = 20,
) -> SubagentSpec:
    """只读调研：搜索、读文件、抓网页；禁止执行命令和写文件。"""
    tools = _build_research_tools(workspace)
    return SubagentSpec(
        tools=tools,
        system_prompt=system_prompt,
        max_iterations=max_iterations,
    )


def build_scripting_spec(
    *,
    workspace: Path,
    task_dir: Path,
    system_prompt: str,
    max_iterations: int = 20,
) -> SubagentSpec:
    """执行型：运行命令、读写文件（仅限 task_dir）；禁止网络访问。"""
    tools = _build_scripting_tools(task_dir)
    return SubagentSpec(
        tools=tools,
        system_prompt=system_prompt,
        max_iterations=max_iterations,
    )


def build_general_spec(
    *,
    workspace: Path,
    task_dir: Path,
    system_prompt: str,
    max_iterations: int = 20,
) -> SubagentSpec:
    """通用型：调研与执行兼有；仅在任务明确需要两者时使用。"""
    tools = _build_research_tools(workspace) + _build_scripting_tools(task_dir)
    return SubagentSpec(
        tools=tools,
        system_prompt=system_prompt,
        max_iterations=max_iterations,
    )


_PROFILE_BUILDERS = {
    PROFILE_RESEARCH: build_research_spec,
    PROFILE_SCRIPTING: build_scripting_spec,
    PROFILE_GENERAL: build_general_spec,
}


def build_spawn_spec(
    *,
    workspace: Path,
    task_dir: Path,
    system_prompt: str,
    max_iterations: int = 20,
    profile: str = PROFILE_RESEARCH,
) -> SubagentSpec:
    """根据 profile 选择对应的工具集构建 SubagentSpec。

    profile:
        research  — 只读调研（默认，最小权限）
        scripting — 执行型，可运行命令和写文件，禁止网络
        general   — 两者兼有，仅在明确需要时使用
    """
    builder = _PROFILE_BUILDERS.get(profile, build_research_spec)
    return builder(
        workspace=workspace,
        task_dir=task_dir,
        system_prompt=system_prompt,
        max_iterations=max_iterations,
    )
