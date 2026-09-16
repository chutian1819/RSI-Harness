# RSIH 写作工作台

让 AI 帮你改材料，同时留下“你说了什么、原稿是什么、改成了什么”的记录，再从中提炼值得复用的写作经验。

**第一次使用？从 [新手操作手册](docs/GETTING_STARTED.md) 开始。** 按顺序安装、配置自己的 DeepSeek 密钥，再用一份虚构材料走完流程。日常使用只需打开终端菜单。

## 它能帮你做什么

例如，你提交一份经营分析，发现摘要重复了正文的数据，便要求 AI 精简摘要。工作台会保存：

1. 修改前的 V1 原稿。
2. 你的这条修改意见。
3. DeepSeek 返回的 V2 修改稿，以及前后差异。
4. 你选择“提炼候选”后，AI 提出的写作经验，以及它依据的原文。

你可以在本地网页查看这些记录。每篇材料各有自己的目录，下一轮修改继续生成新版本。

**保存记录不等于 AI 已经学会。** 候选需要人判断；本版不会自动修改 Genome，也不会训练 DeepSeek 的模型参数。团队经验需经过 GitHub 实际人工审核、合并和核验，再导出提供给下一次写作的 AI。该团队流程仍需在实际团队环境验收。

## 这个仓库和原版 RSIH 有什么关系

本仓库是 Python 写作工作台及连接程序，**不是上游 RSI-Harness 引擎的源码镜像**。上游 [CosmosMind-ai/RSI-Harness](https://github.com/CosmosMind-ai/RSI-Harness) 单独安装，负责读取 Genome 并调用模型。本工作台负责文稿版本、修改意见、候选经验和查看页面，两者通过命令行衔接。

```text
你在终端输入修改要求
        ↓
写作工作台：保存原稿和指令
        ↓
RSIH 引擎 + Genome 写作规则 → DeepSeek 生成修改稿
        ↓
写作工作台：保存新版本、展示差异
        ↓ 你选择提炼
候选经验 → 人工判断与团队审核 → 导出后供下一次写作使用
```

Genome 可以理解为“交给 AI 的写作说明书”，不是模型的脑子。本版仅在每次修改时复制它作为记录，不会自行改写它。

## 安装完成后，平时怎么用

下面假设你已按新手手册安装到 `~/writing-workbench`：

```sh
cd ~/writing-workbench
.venv/bin/writing-memory-rsih menu
```

菜单中依次选择“新建材料”“修改材料”“查看页面”；需要总结习惯时选择“提炼候选”。输入多行内容后，另起一行输入英文句号 `.` 并回车。输入 `0` 退出。

首次使用还需要安装引擎和执行 `setup`；仅下载这个仓库不能直接调用模型。

## 文件放在哪里

| 位置 | 用途 | 是否共享到代码仓库 |
| --- | --- | --- |
| `writing_memory/`、`tests/`、`docs/` | 程序、测试和说明 | 是 |
| `~/.local/share/rsih-writing-lab/credentials.json` | 每个人自己的 DeepSeek 密钥 | 否 |
| 同目录的 `genomes/writing-demo/` | 本机写作规则 | 否 |
| 同目录的 `manuscripts/` | 文稿、版本、意见与模型调用记录 | 否 |
| 同目录的 `manuscript-assets/` | 独立 Git 资产库、提炼快照和候选 | 否 |
| 同目录的 `manuscripts.html` | 本地查看页面 | 否 |

下载代码不会下载作者的文稿、密钥或个人记忆。每位同事运行 `setup` 后有自己的本机配置。材料只有在你调用模型修改或提炼时才发送给模型服务；共享代码不会自动同步材料。

## 使用说明

- [新手操作手册](docs/GETTING_STARTED.md)：从安装到第一轮修改，含常见问题。
- [RSIH 详细说明](docs/RSIH.md)：各目录、命令参数、失败恢复及工作原理。
- [基础连接程序与团队审核命令](docs/CLI.md)：适用于进阶使用及团队管理员。
- [外部服务接入](docs/INTEGRATIONS.md)：Langfuse、GitHub、选定 Codex 会话适配。
- [工程设计](docs/ENGINEERING.md)：为什么这样保存、去重和恢复。
- [验收记录](docs/ACCEPTANCE.md)：哪些已经验证，哪些仍需真实环境测试。

当前支持 macOS/Linux、UTF-8 文本和 Markdown，已在 macOS 验证。Windows 请使用 WSL Linux 环境（尚未实测）。暂不直接处理 Word/PDF，也不自动采集其他聊天软件。查看页面是本地 HTML，操作仍在终端完成。

## 开发者验证

Python 3.10+，运行时无第三方 Python 依赖；RSIH 引擎有自己的安装依赖。

```sh
python3 -m unittest discover -s tests -v
uv build --wheel
```

原始需求见 [平台方案](团队AI写作经验沉淀与复用平台方案.md)。其中聊天示例是需求材料，不代表系统已经完成所有能力。
