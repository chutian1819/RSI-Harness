# RSI 写作工作台

**每次修改都有来处，确认过的好习惯留给下一篇。**

在本机浏览器中写材料、提修改意见、比较前后稿。每轮保存原文、你的原始指令、AI 草稿和实际使用的规则；你采用后才更新正式稿。AI 自动提炼候选写作经验，你确认后才在同类材料中使用。

第一次使用，请打开 **[使用指南](docs/GETTING_STARTED.md)**。想先看原理和目录，请读 **[架构与文件地图](docs/ARCHITECTURE.md)**。

```text
所选参考资料 + 原稿（可空）+ 本轮要求 + 有效历史要求 + 已确认偏好
                  ↓
         RSIH / 派生 Genome → DeepSeek
                  ↓
      草稿与前后差异 → 你采用 → 正式新版本
                                 ↓
                      自动提炼 → 你确认 → 个人规则
```

这里改进的是 AI 工作时读取的规则，**不会训练或修改 DeepSeek 的模型权重**。保存记录、加载规则、生成效果改善是三个不同的结果，工作台分别提供证据。

## 安装一次，日常一个命令

macOS，先准备 Git、Python 3.10+、Node 22.19+ 和 npm，以及你自己的 DeepSeek API Key。逐步安装和报错处理见[使用指南](docs/GETTING_STARTED.md)。

```sh
git clone https://github.com/chutian1819/RSI-Harness.git ~/writing-workbench
cd ~/writing-workbench
./scripts/install-macos.sh
.venv/bin/writing-memory-rsih setup
.venv/bin/writing-memory-rsih web
```

浏览器会自动打开本机网页。以后进入项目文件夹，运行最后一条命令即可。关闭服务按终端 `Ctrl+C`，已发送的请求会先保存结果；再次启动不会自动重发完成情况不明的调用。

安装器固定上游 RSIH 引擎提交，并保存在 `~/.local/share/rsih-writing-runtime/`，不会把原版引擎源码混进本仓库。重复配置保留你的密钥和基础 Genome。仓库与引擎的区别见[架构说明](docs/ARCHITECTURE.md)。

## 你可以做什么

- 从零起草、根据多份参考资料生成，或修改已有原稿；新建时可填写要求直接生成。
- 多选或拖入 PDF、PPTX、DOCX、TXT、Markdown、PNG/JPG/WEBP；旧 `.ppt` 通过本机 LibreOffice 转换。
- 本地解析文字、表格、幻灯片备注和 OCR 文字，按需选择资料，每次调用保留实际资料快照。
- 多轮改稿、并排查看正文和差异、采用或拒绝草稿。
- 手工编辑、导出 Markdown、从旧稿生成新的正式版本。
- 查看每轮原始要求、完整版本和本次实际加载的规则。
- 自动提炼候选；核对依据、编辑范围、确认、暂缓、拒绝或撤销。
- 默认按文种和读者应用偏好；一次性的要求可不沿用到后续轮次。
- 导出写作习惯：Markdown 附跨 Agent 使用说明，JSON 可导入其他 RSI 工作台，确认后生效。

采用稿件与确认记忆是两个动作。自动提炼会额外调用一次模型，可能得到零条候选。模型请求使用个人 API 额度；会员订阅不等于 API 额度。

## 数据在你自己的机器上

默认数据根：`~/.local/share/rsih-writing-lab/`。

| 位置 | 内容 |
| --- | --- |
| `credentials.json` | 自己的密钥，不提交 GitHub |
| `genomes/writing-demo/` | 基础写作说明书 |
| `manuscripts/` | 正文、正式版本、每轮指令、差异、模型调用快照 |
| `manuscript-assets/` | 带 Git 历史的权威文稿记录、草稿和经验 |
| `manuscript-assets/experiences/personal/` | 个人确认的规则与修订记录 |
| `references/` | 上传原件、解析文字、页码 / 幻灯片编号、文件指纹与解析提示 |
| `web-jobs/` | 草稿生成和候选提炼的任务状态 |

每位同事独立安装，文稿和偏好不会自动共享。网页只监听本机；调用模型时会发送本次上下文。源码更新不会覆盖个人数据。分享规则包不包含原稿和密钥，但仍应在预览中核对规则文字里的业务信息。

## 学习与开发

- [使用指南](docs/GETTING_STARTED.md)：原理 → 安装 → 改稿 → 追溯 → 验证记忆 → 分享。
- [架构与文件地图](docs/ARCHITECTURE.md)：文件作用、上下文流向、为何草稿和正式稿分开。
- [工程实现](docs/ENGINEERING.md)：存储、恢复、并发和个人记忆设计。
- [验收记录](docs/ACCEPTANCE.md)：自动化、浏览器与真实模型验证的明确边界。
- [原有终端入口](docs/RSIH.md)、[高级团队命令](docs/CLI.md)、[可选接入](docs/INTEGRATIONS.md)。

```sh
.venv/bin/python -m pip install -c requirements.lock '.[test]'
.venv/bin/python -m unittest discover -s tests -v
uv build --wheel
```

以 macOS 为主，参考资料支持多格式，生成和正文编辑统一为 Markdown；不提供多人服务器和登录账户。保留旧 CLI；旧 `revise` 仍直接更新当前稿，新网页使用“先草稿、后采用”流程。GitHub 团队审核与个人确认分属两套权威，个人确认不会冒充团队发布。

参考文件每份最多 20 MB、每篇最多 30 份，PDF / PPTX 每份最多 100 页。macOS 使用 Apple Vision 做本地中文 OCR；其他系统需要自行安装 Tesseract 及中文语言包。OCR 提取文字，不能完整解释图表关系、版式和图像含义。完整上下文受本机预算约束，超出会停止调用并提示拆分或取消部分资料，不静默截断。详见[参考资料与 Agent 迁移指南](docs/REFERENCES_AND_AGENTS.md)。
