# TomatoCat

TomatoCat（番茄猫）是一个带像素猫桌面形象的个人 AI 助手：支持多轮对话、工具调用、长期记忆、定时提醒、主动推送，以及 Telegram、QQ、CLI 和 PyQt6 桌面端。

## 快速开始

需要 Python 3.12+。推荐使用 [uv](https://docs.astral.sh/uv/) 管理环境：

```powershell
pip install uv
uv venv --python 3.12
.venv\Scripts\Activate.ps1
uv sync
Copy-Item config.toml.example config.toml
```

编辑 `config.toml`，填写 `[llm.main]` 下的 `model`、`api_key` 和（如需要）`base_url`，然后启动：

```powershell
# 后端/通道模式
uv run python main.py --workspace .\workspace

# PyQt6 桌面宠物模式
uv run python main.py --desktop --workspace .\workspace
```

可选代理（PowerShell）：

```powershell
$env:HTTP_PROXY = "http://127.0.0.1:7897"
$env:HTTPS_PROXY = "http://127.0.0.1:7897"
```

## 主要能力

- Agent 主循环：LLM 流式响应、思考过程、工具调用和多模态图片输入。
- 记忆系统：Markdown 五层记忆 + SQLite/numpy 向量检索，支持语义与关键词混合排序。
- 后台子 Agent：复杂任务异步执行，使用独立工作区隔离结果。
- 插件与 MCP：动态加载 `plugins/` 插件，也可接入 MCP 服务。
- 主动任务：定时调度、提醒和按 profile 主动推送到指定通道。
- 多端交互：CLI Socket、Telegram、QQ/NapCat、PyQt6 像素猫桌面端。

## 常用参数

```text
--workspace DIR   运行时数据目录（默认 ./workspace）
--config PATH     TOML 配置文件（默认 config.toml）
--desktop         启用桌面宠物模式
```

后台任务状态与显式重试：

```powershell
uv run python main.py jobs --workspace .\workspace
uv run python main.py retry JOB_ID --workspace .\workspace

# scripting/general 或旧任务可能包含外部副作用，需要人工确认后强制重试
uv run python main.py retry JOB_ID --force --workspace .\workspace
```

`jobs` 输出中的 `delivery=sending` 表示进程可能在消息发送期间中断。为避免重复通知，系统不会自动重发这类状态，应先到目标通道确认是否已经收到消息。

详细配置、工作区结构、插件开发、安全建议和故障排查请阅读 [项目文档](docs/PROJECT.md)。

## 项目结构

```text
main.py                 # 程序入口
tomatocat/agent/        # Agent、LLM 与子 Agent
tomatocat/channels/     # CLI、Telegram、QQ 通道
tomatocat/memory.py     # 文件记忆
tomatocat/memory2/      # 向量记忆
tomatocat/plugins/      # 插件框架
tomatocat/proactive/    # 主动推送
plugins/                # 用户插件
config.toml.example     # 配置模板
```

## 开发与测试

```powershell
uv sync --group dev
uv run pytest
```

请勿提交包含 API Key、Bot Token 或个人对话数据的 `config.toml` 与 workspace 文件。当前仓库尚未声明开源许可证，对外发布前请补充 LICENSE。
