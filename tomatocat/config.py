from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib


@dataclass
class LLMConfig:
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    enable_thinking: bool = False
    multimodal: bool = False


@dataclass
class MemoryConfig:
    enabled: bool = True
    memory_window: int = 40
    vector_enabled: bool = True


@dataclass
class CLIChannelConfig:
    enabled: bool = True
    socket: str = "127.0.0.1:8768"


@dataclass
class TelegramChannelConfig:
    enabled: bool = False
    token: str = ""
    allow_from: list[str] = field(default_factory=list)


@dataclass
class QQChannelConfig:
    enabled: bool = False
    bot_uin: str = ""
    allow_from: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)


@dataclass
class ChannelsConfig:
    cli: CLIChannelConfig = field(default_factory=CLIChannelConfig)
    telegram: TelegramChannelConfig = field(default_factory=TelegramChannelConfig)
    qq: QQChannelConfig = field(default_factory=QQChannelConfig)


@dataclass
class AgentConfig:
    system_prompt: str = ""
    max_tokens: int = 8192
    max_iterations: int = 20


@dataclass
class MCPConfig:
    enabled: bool = False
    config_file: str = "mcp_servers.json"


@dataclass
class ProactiveTargetConfig:
    channel: str = "telegram"
    chat_id: str = ""


@dataclass
class ProactiveConfig:
    enabled: bool = False
    profile: str = "daily"
    poll_interval_seconds: int = 300
    target: ProactiveTargetConfig = field(default_factory=ProactiveTargetConfig)


@dataclass
class MemeConfig:
    enabled: bool = True
    meme_dir: str = "memes"


@dataclass
class SchedulerConfig:
    enabled: bool = True
    timezone: str = "Asia/Shanghai"
    default_channel: str = ""
    default_chat_id: str = ""


@dataclass
class ServerConfig:
    port: int = 2238


@dataclass
class Config:
    llm_main: LLMConfig = field(default_factory=LLMConfig)
    llm_fast: LLMConfig = field(default_factory=LLMConfig)
    llm_vl: LLMConfig = field(default_factory=LLMConfig)
    llm_embedding: LLMConfig = field(default_factory=LLMConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    channels: ChannelsConfig = field(default_factory=ChannelsConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    proactive: ProactiveConfig = field(default_factory=ProactiveConfig)
    meme: MemeConfig = field(default_factory=MemeConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    plugins_dir: Path = Path("plugins")

    @classmethod
    def load(cls, path: str | Path = "config.toml") -> Config:
        path = Path(path)
        with open(path, "rb") as f:
            data = tomllib.load(f)

        llm_data = data.get("llm", {})
        main_data = llm_data.get("main", {})
        fast_data = llm_data.get("fast", {})
        vl_data = llm_data.get("vl", {})
        emb_data = llm_data.get("embedding", {})

        agent_data = data.get("agent", {})
        channels_data = data.get("channels", {})
        memory_data = data.get("memory", {})
        mcp_data = data.get("mcp", {})
        proactive_data = data.get("proactive", {})
        meme_data = data.get("meme", {})
        scheduler_data = data.get("scheduler", {})
        server_data = data.get("server", {})

        return cls(
            llm_main=LLMConfig(
                model=main_data.get("model", ""),
                api_key=main_data.get("api_key", ""),
                base_url=main_data.get("base_url", ""),
                enable_thinking=main_data.get("enable_thinking", False),
                multimodal=main_data.get("multimodal", False),
            ),
            llm_fast=LLMConfig(
                model=fast_data.get("model", ""),
                api_key=fast_data.get("api_key", ""),
                base_url=fast_data.get("base_url", ""),
                enable_thinking=fast_data.get("enable_thinking", False),
            ),
            llm_vl=LLMConfig(
                model=vl_data.get("model", ""),
                api_key=vl_data.get("api_key", ""),
                base_url=vl_data.get("base_url", ""),
            ),
            llm_embedding=LLMConfig(
                model=emb_data.get("model", ""),
                api_key=emb_data.get("api_key", ""),
                base_url=emb_data.get("base_url", ""),
            ),
            agent=AgentConfig(
                system_prompt=agent_data.get("system_prompt", ""),
                max_tokens=agent_data.get("max_tokens", 8192),
                max_iterations=agent_data.get("max_iterations", 20),
            ),
            channels=ChannelsConfig(
                cli=CLIChannelConfig(
                    enabled=channels_data.get("cli", {}).get("enabled", True),
                    socket=channels_data.get("cli", {}).get("socket", "127.0.0.1:8768"),
                ),
                telegram=TelegramChannelConfig(
                    enabled=channels_data.get("telegram", {}).get("enabled", False),
                    token=channels_data.get("telegram", {}).get("token", ""),
                    allow_from=channels_data.get("telegram", {}).get("allow_from", []),
                ),
                qq=QQChannelConfig(
                    enabled=channels_data.get("qq", {}).get("enabled", False),
                    bot_uin=channels_data.get("qq", {}).get("bot_uin", ""),
                    allow_from=channels_data.get("qq", {}).get("allow_from", []),
                    groups=channels_data.get("qq", {}).get("groups", []),
                ),
            ),
            memory=MemoryConfig(
                enabled=memory_data.get("enabled", True),
                memory_window=memory_data.get("memory_window", 40),
                vector_enabled=memory_data.get("vector_enabled", True),
            ),
            mcp=MCPConfig(
                enabled=mcp_data.get("enabled", False),
                config_file=mcp_data.get("config_file", "mcp_servers.json"),
            ),
            proactive=ProactiveConfig(
                enabled=proactive_data.get("enabled", False),
                profile=proactive_data.get("profile", "daily"),
                poll_interval_seconds=proactive_data.get("poll_interval_seconds", 300),
                target=ProactiveTargetConfig(
                    channel=proactive_data.get("target", {}).get("channel", "telegram"),
                    chat_id=proactive_data.get("target", {}).get("chat_id", ""),
                ),
            ),
            meme=MemeConfig(
                enabled=meme_data.get("enabled", True),
                meme_dir=meme_data.get("meme_dir", "memes"),
            ),
            scheduler=SchedulerConfig(
                enabled=scheduler_data.get("enabled", True),
                timezone=scheduler_data.get("timezone", "Asia/Shanghai"),
                default_channel=scheduler_data.get("default_channel", ""),
                default_chat_id=scheduler_data.get("default_chat_id", ""),
            ),
            server=ServerConfig(
                port=server_data.get("port", 2238),
            ),
        )
