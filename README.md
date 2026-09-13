# 团队 AI 写作经验连接程序

将一份文稿的指令、实际版本和段落关系保存在本地 Git 中；从真实修改生成候选经验，经 GitHub 人工审核并合并后，再供新任务使用。日常仍在 Codex、Kimi 等工具写作。

当前交付为 Python 命令行连接程序。GitHub 人工审核发布流程、Langfuse 接入与 Codex 桌面 Stop Hook 仍须在团队指定环境完成验收。实施状态见 [验收记录](docs/ACCEPTANCE.md)，原始需求见 [平台方案](团队AI写作经验沉淀与复用平台方案.md)。本仓库保存程序、测试和文档；文稿资产与采集记录由使用者在本机初始化。

## 运行

需要 Python 3.10+ 和 Git，运行时无第三方 Python 依赖。本版按 macOS/Linux 环境实现，已在 macOS 上完成本地验收。先克隆代码，再在项目目录运行：

```sh
git clone https://github.com/chutian1819/Langfuse_RSI.git
cd Langfuse_RSI
python3 -m writing_memory --help
python3 -m unittest discover -s tests -v
python3 -m writing_memory doctor
python3 -m writing_memory init
```

也可在自己的虚拟环境中 `python3 -m pip install -e .`，随后用 `writing-memory` 命令。所有命令支持前置 `--root /绝对路径/资产目录`；默认数据写入当前目录 `.writing-memory/`。

程序在资产目录初始化独立 Git 仓库，代码仓库默认忽略 `.writing-memory/`。自定义项目内资产路径时，还需自行将该路径加入外层 `.gitignore`。这样每轮文稿记录不会把开发代码或用户其他已暂存文件一起提交。原始材料不因 GitHub 尚未配置而丢失。

## 一次写作

先创建自己的 Markdown 文件，再绑定任务。下面的 `TASK`、`PARAGRAPH` 等是命令输出中的实际编号，需要替换；命令中的认可操作由人明确决定。

```sh
python3 -m writing_memory start \
  --title '业务试点方案' --purpose '帮助判断是否开展试点' \
  --audience '领导' --document-type '决策方案' --topic '业务试点' \
  --document /绝对路径/试点方案.md

python3 -m writing_memory show TASK
```

`start` 会保存现有文稿为 V1。修改文稿后记录本轮指令：

```sh
python3 -m writing_memory capture TASK \
  --instruction '先写推荐路径，再说明依据；只修改第二段。' \
  --session session-001 --event turn-001 \
  --target-version V1 --target-paragraph PARAGRAPH --excerpt '被评价的原文'
```

`--event` 是这轮操作的稳定编号：补记、断网后重试时沿用它；新一轮另取编号。只讨论、未检查文稿时加 `--no-document`。内容没有变化会保存指令，但不会制造新版本。

```sh
python3 -m writing_memory accept TASK V2 --actor '真实审核人'
python3 -m writing_memory restore TASK \
  --from-version V1 --from-paragraph OLD_PARAGRAPH \
  --current-paragraph CURRENT_PARAGRAPH \
  --instruction '恢复 V1 这段，其他段保留当前稿。' --actor '真实操作者'
python3 -m writing_memory status
```

恢复导致内容变化时会形成新版本，并保留段落来源；内容相同则只记录本次操作。待确认的关系可以用 `resolve TASK RELATION --paragraph PARAGRAPH --actor 姓名` 补充；省略 `--paragraph` 表示取消关联。

## 提炼、审核与复用

```sh
python3 -m writing_memory extract TASK
```

该命令生成提炼上下文和提示词快照。尚未接入 Langfuse 时使用本地初始模板，并明确标为 `local_bootstrap`。配置好团队 Langfuse text 提示词后，使用 `extract TASK --prompt-name team-writing-extraction --prompt-version 1` 从唯一管理位置取得实际版本；只在本地保留快照。也可用 `--prompt-label production` 获取标签当前指向的版本。下载失败会报错，不会偷偷改用其他提示词。

让当前 Codex 阅读返回的文件，结合指令及前后文提出少量候选，按 [候选 JSON 示例](examples/candidates.example.json) 保存。程序不会自行申请模型 API 或将内容发往未配置的服务。

```sh
python3 -m writing_memory candidates TASK /绝对路径/candidates.json --extraction-id EXTRACTION_ID
python3 -m writing_memory review CANDIDATE_ID
```

将 `review` 输出目录中的 `experiences/approved/*.json` 放进团队指定的私有 GitHub 仓库，以正常 PR 审核、修改或删除。程序不自动合并，不代替人作经验判断。使用已登录的 `gh` 核验合并和人工审核记录后登记发布：

```sh
python3 -m writing_memory confirm-merge --repo TEAM/REPO --pr 123
python3 -m writing_memory export NEW_TASK
```

将导出的 Markdown 实际提供给下一次写作的 AI，再记录加载：

```sh
python3 -m writing_memory load NEW_TASK EXPORT_ID --mode manual_attachment --actor '真实使用人'
python3 -m writing_memory feedback LOAD_ID EXPERIENCE_ID --rating useful --reason '开头已直接给推荐路径，减少了一轮调整。'
```

反馈可选 `useful`（有用）、`useless`（无用）、`misapplied`（误用）。导出文件本身不构成已使用；认可文稿也不构成事实核验。

撤销使用 `revoke EXPERIENCE_ID --reason 原因` 准备变更，经同一 GitHub 审核流程合并并 `confirm-merge` 后生效。新导出排除撤销经验，历史加载保留原版。

## 接入与排障

普通聊天导入：

```sh
python3 -m writing_memory import TASK /绝对路径/chat.md --source kimi
python3 -m writing_memory import-codex TASK /明确指定的/session.jsonl \
  --session SESSION_ID --project /明确选定的项目
```

导入内容只作为数据。缺少旧稿时保留缺失状态，不会用最后一份文稿反推历史。

Langfuse 通过环境变量配置（字段见 `.env.example`），密钥留在本机；程序不自动读取或执行 `.env`。配置后显式运行 `sync`，细节见 [接入说明](docs/INTEGRATIONS.md)。

```sh
python3 -m writing_memory doctor
python3 -m writing_memory status
python3 -m writing_memory recover
python3 -m writing_memory sync
```

`recover` 从已落盘记录重建队列并补提 Git，不联网。`sync` 会先恢复队列，再同步至明确配置的服务。送达结果不确定时先读回核验；确需重传时显式加 `--retry-uncertain`，该操作可能在远端形成重复记录。恢复旧段落时如果原文件写入中断，可用 `document-retry TASK` 补写；原文件已另有修改时会保留冲突，需先处理当前稿。

要理解为何采用这些设计，阅读 [工程说明](docs/ENGINEERING.md)。

查看本地演示：`python3 examples/run_local_demo.py --output /尚不存在的演示目录`。示例完全离线、内容为虚构，展示三轮修改、段落恢复与候选隔离；不会生成真实审核或发布记录。
