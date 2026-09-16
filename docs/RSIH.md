# RSIH 文稿管理适配器

这是 `writing_memory.rsih_workspace` 提供的可选入口。它补齐“每篇材料独立目录、逐轮版本、意见关联、候选列表”，继续使用 RSIH 调用模型，复用本项目已有 Store 和 ExperienceLibrary。没有修改上游 RSIH 或覆盖以前的实验数据。

## 使用边界

- 支持 UTF-8 纯文本和 Markdown。暂不直接解析 Word/PDF，也不截取其他聊天工具中的对话。
- 只有从本入口执行的修改自动生成文稿版本；原有裸 RSIH 终端聊天不受影响，也不会被偷偷采集。
- 每次修改将当前实际文稿和新指令交给 RSIH。调用有独立 session，保存在该材料的 sessions 目录；不依赖模型自行挑选历史版本。
- 生成成功才写新版本。相同内容保留指令但不制造版本。模型失败、回复截断或文件存在未记录的外部修改时不覆盖当前稿。
- 全文前后对比是真实快照；段落拆分/合并仍保持待确认，不宣称自动理解了段落身份。
- 提炼结果只是候选。处理动作是暂缓、不采纳、准备 GitHub 审核包；不调用 `accept`，不改变已发布经验或 Genome。正式发布仍要求本项目原有的实际 GitHub 审核合并核验。
- 这是本地材料管理与真实模型接入；不意味着团队审核、Langfuse、桌面 Hook 或完整真实业务流程已验收。

## 安装及启动

首次使用请先完成 [新手操作手册](GETTING_STARTED.md)。安装引擎和本工作台后，运行 `writing-memory-rsih setup` 创建自己的配置，再用 `writing-memory-rsih doctor` 检查。以下为进阶参考，不要求同事复制作者的私有环境。

现有 RSIH 试用环境的默认目录为 `~/.local/share/rsih-writing-lab`。适配器读取其中的：

- `credentials.json`：DeepSeek 凭据，文件权限0600。环境变量已设置时优先用环境变量。
- `agent/models.json`：现有 provider 配置。
- `genomes/writing-demo/`：当前写作 Genome。

以上路径可通过 `--state` 整体切换；`--model` 可覆盖默认模型 `deepseek-study/deepseek-flash`。本版凭据适配器面向 DeepSeek。

源码运行：

```sh
python3 -m writing_memory.rsih_workspace --help
python3 -m writing_memory.rsih_workspace menu
```

安装 wheel 后运行 `writing-memory-rsih menu`。不需要修改 RSIH 安装目录。

终端菜单流程：

1. 新建材料：输入名称、用途、读者、文种、主题，粘贴初始材料，以独立一行 `.` 结束。原稿先保存为 V1。没有原稿时保存明确的空初始版本。
2. 修改材料：选择材料，输入本轮指令。程序显示事件编号，成功后保存下一版和差异。每次模型调用会产生用户模型服务的费用。
3. 提炼候选：选择已发生修改的材料；先创建提炼上下文和提示词快照，再调用模型并严格校验来源。可以没有候选。
4. 查看页面：显示每篇材料、各版内容、逐轮原始指令、差异、操作状态和候选证据。
5. 处理候选：输入候选编号及操作人，暂缓/不采纳/准备审核包。审核包不是发布。
6. 记录手动修改：如你直接编辑了该材料的 current.md，先说明修改内容并记录，再继续调用模型。

页面是本地生成的 HTML，处理动作在终端进行；没有常驻 HTTP 服务。

## 命令行自动化

```sh
writing-memory-rsih new --title '九月经营分析' --purpose '月度汇报' \
  --audience '部门负责人' --document-type '经营分析' --topic '充电业务' --initial /绝对路径/原稿.md

writing-memory-rsih revise DOC_ID --instruction '摘要不要逐项重复正文数据' --event revision-001
writing-memory-rsih extract DOC_ID --event extraction-001
writing-memory-rsih view
writing-memory-rsih decide CANDIDATE_ID defer --actor '实际使用者' --reason '需要更多实例'
```

同一操作重试沿用事件编号，不同操作使用新编号。失败或中断后需要显式加 `--retry` 才重新调用模型；已保存完整回复时直接恢复落盘，不再调用。状态不确定时重试可能再次收费。

`revise` 支持 `--paragraph` 与 `--excerpt`，定位基于当前指定的实际前版，不根据最后一版猜测。`record DOC_ID --instruction ... --event ...` 记录人工直接编辑当前文件后的版本。

使用既有 `writing-memory --root <state>/manuscript-assets ...` 命令可以查看、认可实际版本、审核发布；这些动作必须符合原有人工认可和 GitHub 发布约定。不要同时用两种入口修改同一材料：适配器会发现当前稿与资产版本不一致并停止，需先核对文件。

## 新增的数据目录

```text
<state>/
  manuscripts/
    doc_<id>/
      document.json            材料身份及资产任务关联
      current.md               当前文稿
      versions/V1.md,V2.md...   从不可变资产记录导出的逐稿文件
      turns/<event>/
        instruction.txt        原始修改意见
        association.json       实际前后版本及段落关系
        change.diff            文本差异
      operations/<event>/
        operation.json         prepared/requesting/response_saved/completed等状态
        prompt.txt             实际请求输入
        before.md              修改前快照（修改操作）
        genome/                本次调用使用的配置快照（修改操作）
        genome-hashes.json     文件内容指纹
        response.md            原始回复，失败恢复时复用
        attempt_*/             每次调用的事件日志、错误、模型及目录信息
      sessions/                RSIH 原生会话
  manuscript-assets/           现有 Store 管理的独立本地 Git 资产仓库
    tasks/                     完整不可变版本及轮次的权威记录
    extractions/               真正使用的上下文和提示词快照
    experiences/candidates/    校验后的候选，尚未发布
    candidate-decisions.json   处理历史，不代表发布
    reviews/                   GitHub 审核提案
  managed-agent/               本入口独立的 RSIH 运行设置
  manuscripts.html             材料和候选查看页面
```

代码、密钥与材料分开存放。`manuscript-assets` 自带独立 Git；本适配器不会自动推送，也不会执行 Langfuse sync。

## 为什么这样设计

原先只有对话历史，模型看到“这段重复”时可能不知道指的是哪一版。新入口在请求前固定当前版本、指令和 Genome，成功后记录真实前后稿，所以可追溯每轮效果。

RSIH 启动时会编译运行设置。新入口使用独立 managed-agent 目录，并串行执行共享此目录的模型调用，减少与旧快捷入口或其他材料争用设置的风险。

长请求可能失败，保存文件也可能中断。因此先保存操作，再请求模型，再保存回复，再写入权威资产记录，最后更新 current.md。恢复时用稳定事件编号去重。历史版本文件被改动会报错，而不是偷偷覆盖。

Genome 只复制到每次操作中读取，不会被模型修改。更新上游 RSIH 或扩展本适配器后仍需重新验证 CLI 兼容性，不能保证未来任意版本都兼容。

## 验证

```sh
python3 -m unittest discover -s tests -v
uv build --wheel
```

适配器测试覆盖逐轮版本、同一事件去重、失败保稿、显式重试、保存中断恢复、手动改稿保护、候选来源快照及本地处理不发布等。离线测试中的角色、规则和认可不是实际人工验收证据。
