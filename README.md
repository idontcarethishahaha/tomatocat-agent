# 🍅🐱 番茄猫 TomatoCat

一只**住在桌面上的像素猫 AI 助手**——不只是被动回答问题，还能陪你学习、帮你记账、定时提醒、后台异步调研，支持 Telegram 移动端 + 桌面端双端交互。

---

## Quickstart

需要 Python 3.12。

**1. 安装 uv（如果还没有）**

```cmd
pip install uv
```

**2. 创建虚拟环境并安装依赖**

```cmd
uv venv --python 3.12.7
.venv\Scripts\activate
uv sync
```

**3. 配置 config.toml**

复制 `config.toml.example` 为 `config.toml`，填写你的 API Key 和渠道配置。


**4. 设置代理（可选）**

```cmd
set HTTP_PROXY=http://127.0.0.1:7897
set HTTPS_PROXY=http://127.0.0.1:7897
```

**5. 启动**

桌面宠物模式：

```cmd
uv run python main.py --desktop --workspace D:\tomatocat-v3-workspace
```

纯后台模式（无桌面宠物）：

```cmd
uv run python main.py --workspace D:\tomatocat-v3-workspace
```

给 bot 发一条消息即可开始对话。

---

## 系统全景

```
你的消息 → [被动回复] ──→ agent loop ──→ 回复
                │
                ├── 记忆系统 ─── 五层记忆 + 向量检索 + 自动整合
                │
                └── 插件系统 ─── 安全防护、工具注册、生命周期介入

[主动推送] ──→ 定时调度 ──→ cron 触发 ──→ LLM 生成内容 ──→ 推送
                │
                └── [子Agent] ──→ spawn 异步执行后台调研任务

[桌面端] ──→ PyQt6 像素猫 ──→ GIF 动画 + Meme 表情 + 文件分析
```

---

## 被动回复

收到消息 → 记忆检索 → 工具调用 → 流式回复。插件系统提供完整的生命周期介入能力。

## 多 Agent 异步架构

主 Agent 负责即时对话响应，遇到长时任务通过 `spawn` 工具创建子 Agent 异步执行：

- **沙箱隔离**：每个子任务有独立目录 `workspace/subagent-runs/<job_id>/`，文件操作不越界
- **权限隔离**：Profile-based（Research/Scripting/General），最小权限原则
- **结果回调**：子 Agent 完成后通过 send_fn 直接推送结果到用户所在渠道

## 记忆系统

**五层记忆架构**：SELF.md（人设）→ MEMORY.md（核心记忆）→ PENDING.md（待整合）→ HISTORY.md（对话历史）→ journal/（每日归档）。

**自研向量存储引擎**：

- SQLite + numpy 实现，向量存 BLOB，余弦相似度纯 numpy 计算
- 语义检索 + 关键词匹配 + RRF 混合排序
- 四种记忆类型（preference/event/procedure/profile）+ 强化计数机制
- 每 8 轮对话自动异步整合 PENDING → MEMORY，使用 fast LLM 不阻塞主对话

## 主动推送（Proactive）

定时任务系统，支持 cron 表达式调度，到点主动推送消息到 Telegram/QQ。全局中文工具调用解析，兼容 glm-4.5-flash 非标准 JSON 输出。

## 插件化安全防护

三层安全防护体系：

- **Shell 安全**：拦截 rm -rf、format、sudo 等危险命令
- **工具循环防护**：连续 3 次相同调用自动截断，防止死循环
- **策略委托**：智能决定 spawn 子 Agent 还是直接执行

EventBus 全链路追踪，结构化存储 TurnTrace、RagQueryLog、MemoryWriteTrace 到 SQLite。

## 桌面端

基于 PyQt6 的像素猫桌面宠物：

- GIF 动画精灵，尺寸/颜色可配置
- Meme 表情系统：LLM 输出 `<meme:xxx>` 标签自动解析为 GIF
- 文件分析在后台线程执行，pyqtSignal 安全更新 UI
- 番茄计时器、右键菜单等桌面扩展功能

---

## 其他命令

```cmd
uv run python main.py --workspace DIR     # 指定工作目录
uv run python main.py --config PATH       # 指定配置文件
uv run python main.py --desktop           # 桌面宠物模式
uv run python main.py --help              # 查看全部参数
```

## 工作区

所有运行时数据在通过 `--workspace` 指定的目录下，默认 `./workspace/`。

目录结构：

```
workspace/
├── memory/           # 五层记忆文件
│   ├── SELF.md
│   ├── MEMORY.md
│   ├── PENDING.md
│   ├── HISTORY.md
│   └── journal/
├── memory2/          # 向量记忆数据库
│   └── memory2.db
├── subagent-runs/    # 子 Agent 沙箱目录
├── schedules.json    # 定时任务
├── checkpoints/      # 检查点
└── logs/             # 日志
```
