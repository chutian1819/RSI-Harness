# 架构与文件地图

## 三层分工

```text
RSI 工作台（本仓库 / Python）
  网页 → 草稿、正式版本、指令与差异 → 候选 → 个人确认
            ↓ 为每轮编制上下文、派生 Genome、保存调用证据
RSIH 引擎（上游独立安装 / TypeScript / Pi）
            ↓ 加载 Genome，按受限配置调用模型
DeepSeek API
            ↓ 返回文本；不会替用户作出采用或批准决定
```

[上游 RSI-Harness](https://github.com/CosmosMind-ai/RSI-Harness) 提供 Agent 运行环境和 Genome 配置机制，本仓库提供写作业务功能。上游固定提交：`33c4f8dfac4359987f2e814e187de67c332498de`。这不是把整个上游复制并重命名，也不是训练 DeepSeek。

## 上游的文件夹

| 位置 | 作用 | 日常使用是否需要修改 |
| --- | --- | --- |
| `src/` | CLI、模型请求、Genome 加载和 Pi 适配 | 不需要 |
| `config/genomes/` | 出厂 Genome，如 paperlab 和 harness-rsi | 不需要 |
| `examples/` | 社区或模型调用示例 | 学习时查看 |
| `docs/` | CLI、Genome 与组件协议 | 进阶参考 |
| `scripts/` | 构建、打包、同步工具 | 安装器调用 |
| `test/` | 自动化测试 | 开发者使用 |
| `dist/` | 构建后的可运行内容 | 不手工编辑 |
| `~/.rsih/genomes/` | 普通 rsih 的个人 Genome | 原版进阶使用 |
| `~/.rsih/sessions/` | 普通 rsih 的会话 JSONL | 是会话记录，不是文稿版本库 |

Genome 通常包含 `genome.json` 清单、`components/*.json` 配置、`contracts/*.dev.md` 契约、`skills/` 方法和 `extension/` 扩展。instructions 管写作规则，model 管模型选择，policies 可管记忆及运行策略。还有 tools、skills、commands、runtime、integrations、appearance、settings、keybindings、resources，共 12 类组件。

原版 `gee` 等价于加载 `harness-rsi` Genome：从用户选择的历史中取证，提出并确认新的配置。它不是所有写作应用的自动记录器。原版配置中的 memory 文本也不等于自动增长、自动检索的经验库。

## 本仓库代码

| 路径 | 职责 |
| --- | --- |
| `writing_memory/core.py` | 权威文稿记录、段落与版本、幂等保存、Git 资产提交 |
| `writing_memory/rsih_workspace.py` | 原有终端工作台、RSIH 调用、结果校验、候选提炼 |
| `writing_memory/rsih_setup.py` | 首次配置、模型选择、诊断 |
| `writing_memory/workbench.py` | 网页业务：草稿、采用、手工保存、上下文、派生 Genome |
| `writing_memory/references.py` | 多格式参考资料、独立解析进程、本地 OCR、来源指纹 |
| `writing_memory/agent_export.py` | 跨 Agent Markdown 习惯导出与使用说明 |
| `writing_memory/personal_memory.py` | 个人规则确认、范围匹配、冲突核对、撤销和分享 |
| `writing_memory/jobs.py` | 落盘的单工作线程队列，显式重试 |
| `writing_memory/web.py` | 仅本机的 HTTP 接口与连接保护 |
| `writing_memory/static/` | 网页 HTML / CSS / JavaScript，无前端构建工具 |
| `writing_memory/experience.py` | 有证据的候选与经 GitHub 审核的团队经验，保持独立 |
| `writing_memory/integrations.py` 等 | 原有可选工具接入，不是日常使用的前提 |
| `scripts/install-macos.sh` | 检查工具、固定引擎版本、创建环境和安装 |
| `docs/`、`tests/`、`examples/` | 教程、自动化验证、明确标注的虚构测试 |

## 你的数据在哪里

代码目录可以更新，个人数据默认保存在 `~/.local/share/rsih-writing-lab/`。`~` 就是自己的用户主目录。Mac Finder 按 `Command + Shift + G` 可粘贴这个路径进入。

```text
rsih-writing-lab/
  credentials.json               # 密钥，权限 0600
  runtime.json                   # 引擎路径
  writing-settings.json          # 模型选择与保守上下文预算
  agent/models.json              # 模型端点和支持列表，无明文密钥
  managed-agent/                 # 独立 RSIH 运行配置，避免污染普通 rsih
  genomes/writing-demo/          # 你自己的基础 Genome
  manuscripts/doc_.../
    document.json                # 材料编号到权威任务编号的关联
    current.md                   # 当前正式全文
    versions/V1.md, V2.md        # 正式版本的可读副本
    turns/<event>/               # 原始指令、版本关联、差异
    operations/<request>/       # 请求、回复、事件及派生 Genome 快照
  manuscript-assets/             # 独立 Git 资产库，权威记录
    tasks/task_....json           # 版本、轮次、草稿、采用和恢复意图
    extractions/                 # 提炼输入、提示词和来源哈希
    experiences/candidates/      # 尚未批准的候选及来源原文
    experiences/personal/        # 本人确认的规则、导入候选和修订历史
    experiences/releases/        # 仅经 GitHub 核验的团队发布
  web-jobs/                      # 请求排队、结果、失败与重试状态
  manuscripts.html              # 旧版静态查看页面，不是新网页入口
```

`current.md` 与 `versions/` 便于查看，权威正文来自资产库任务记录。不要随意编辑历史版本文件。未采用草稿在权威任务的 `draft_proposals` 中，也在 operations 中保留模型原始回复，不伪装成 V2。

## 为什么这样设计

**草稿与正式版本分开。** 旧存储约定“最后版本就是当前稿”。把未采用草稿直接放到版本末尾，会让恢复、差异、下次改稿都读到用户未认可的内容。新增独立草稿是为了保留这个不变量。

**先落盘事实，再写回文件。** 采用时同时保存新版本和写回意图。若进程中断，下次根据前后哈希恢复，发现外部新改动则停下，不覆盖。Git 提交是额外的可重试步骤，提交失败不意味着已保存正文消失。

**记录与回忆分开。** 原始历史完整保存，生成请求只携带当前正文、选中的有效历史要求和匹配规则；目前不做不透明的自动摘要。提炼限定到本次已采用轮次的完整前后文，来源仍能回到总档案。

**个人确认与团队发布分开。** 个人偏好不需要同事替你批准，但也不能冒充团队规则。两套记录不同目录、不同 authority、不同导出语义。

**偏好按调用投影。** 基础 Genome 不被自动改写。每轮复制基础 Genome，把匹配规则附加到 instructions，记录规则修订和文件哈希，校验后执行。未来撤销不改变过去发生过什么。

**模型输出不是执行权限。** 写作调用禁用工具、技能、扩展和上下文文件发现；输入文稿作为数据，网页不执行其中的 HTML。规则只影响写作说明，不允许 AI 任意修改程序。

## 边界

本版本不保证模型每次都遵守全部规则，不自动训练模型，不做跨用户同步，不直接读取任意聊天软件。规则范围精确匹配，冲突提示采用范围重叠检测而不是完整的语义逻辑证明。学习质量需要用户检查实际稿件。
