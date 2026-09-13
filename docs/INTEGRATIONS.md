# 接入、采集与送达核验

核查日期：2026-09-08。此文区分程序测试、官方接口能力与尚待真实环境验收的部分。导入内容是历史材料，程序不执行其中的命令或指令。

## 已实现的边界

Python 3.10+ 标准库连接器支持指定 MD/TXT 和指定 Codex JSONL；不搜索个人账户的全部聊天。原始字节先保存到资产仓库 `sources/<SHA256>.<扩展名>`，解析结果与完整行游标保存到 `imports/`，随后才产生文稿快照和本地待发送事件。原文和导入报告通过 `Store.commit_path` 逐文件进入独立资产 Git 仓库；提交失败进入 Git 待补队列。

文本角色标记支持独立一行的 `## user`、`[assistant]`、`用户：` 等。未标记前言保留为 `unknown`；代码围栏里的角色词不作为结构；消息原有顺序、换行与来源可追溯。没有历史文稿时固定记录 `history_versions_missing=true`，配套的当前文稿只保存一次观察快照，不把它复制成多轮历史版本。

```python
from writing_memory.integrations import SourceImporter

importer = SourceImporter("/absolute/path/to/assets")
importer.import_file("task_ID", "/explicit/history.md", source="kimi",
                     document_path="/explicit/current-draft.md")
importer.import_codex("task_ID", "/explicit/rollout.jsonl", "EXACT_SESSION_ID")
```

JSONL 的 `session_meta.id` 必须与显式会话编号一致；提供 `project_path` 时，还验证 `session_meta.cwd`。每个已取得的原始行与工具字段完整保留；事件编号由任务、会话、文件、字节位置及原行摘要计算。重复运行沿用编号；文件被改写或截断会报错并保留旧副本。尾部半行不推进游标，不把它当作完成标记。程序每 32 行保存完整行检查点；崩溃后可重放归档原文，读文件及关闭文件错误也有落盘状态。

## Codex 实时绑定与 Stop Hook

历史导入只能说明“现在取得了这些材料”；逐轮采集还必须知道“上一轮结束时文稿是什么”。因此写作前先运行一次 `import_codex(..., live=True, document_path=..., project_path=...)`，返回 `live_status=armed` 后才建立采集基线。已有聊天不自动冒充历史修改。后续只有一个完整新回合时，使用其原始用户消息关联原有快照与实际新文稿；当前桌面使用的 `response_item` 用户消息和 `event_msg.user_message` 均可识别，同时存在时优先后者。

Stop 有时先于 `task_complete` 写盘。适配器接收 Hook 明确传来的 `turn_id`，把那一轮结束时取得的文稿独立存为待关联观察；下一次取得完成标记时用这份旧快照补齐，不拿新一轮文稿替代它。没有完成标记仍标 `pending_completion`。若漏过多轮且没有每轮快照，只保存当前文稿并显示 `unobserved_intermediate_versions`，不制造中间版本。

配置模板在 `hooks/writing-memory-hook.example.json`。将填写后的文件放在选定项目根目录，命名为 `.writing-memory-hook.json`：

```json
{
  "enabled": true,
  "project_path": "/absolute/path/to/project",
  "storage_root": "assets",
  "task_id": "task_ID",
  "session_id": "EXACT_SESSION_ID",
  "transcript_path": "/explicit/rollout.jsonl",
  "document_path": "draft.md"
}
```

适配器只读取进程当前目录的这一份配置。项目、会话、transcript 必须全部匹配，文稿必须位于该项目内；不向父目录寻找配置，不改全局配置，也不自动调用远端同步。路径带空格时，Hook 的 shell 命令中应给脚本绝对路径加引号。

官方支持 `<repo>/.codex/hooks.json` 或项目 `config.toml` 内的 Hook 定义，项目配置层和具体 Hook 均须受信任；内容变更可能要求重新审核。`features.hooks` 是当前规范配置项，默认开启。Hook 进程的 cwd 是会话 cwd，Stop 成功退出须输出 JSON。本仓库的 `hooks/hooks.example.json` 是项目注册模板，`hooks/codex_stop.py` 使用规定的 `continue`/`systemMessage` 输出；没有跳过平台信任机制。[OpenAI 官方 Hooks 文档](https://learn.chatgpt.com/docs/hooks)

官方也明确 transcript 格式不是稳定接口。因此应使用实际版本的脱敏样本做回归；未识别的行仍保留原文，不由解析器猜测意义。CLI 启动、桌面启动、Hook 发现、Hook 信任和真实自动调度必须分别验收。[OpenAI 官方 Hooks 文档](https://learn.chatgpt.com/docs/hooks)

本次本机检查发现 `codex --version` 因所需原生二进制缺失而报 ENOENT。本实现未修复全局 CLI，也不能据单元测试宣称桌面自动调度已通过。可先对明确选定会话使用适配器手动验证，再在客户端信任项目 Hook，观察真实自动触发。关闭应用之后不会有程序继续轮询；再次打开后需重新采集该显式文件，补齐最后的完成标记。

## 官方 Langfuse 插件的使用位置

丰富的模型步骤、工具树、子代理与 token 追踪优先使用 [Langfuse 官方 Codex 插件](https://github.com/langfuse/codex-observability-plugin)。当前 README 要求 Node 22+、Codex 0.128+；marketplace 名称为 `langfuse/codex-observability-plugin`，插件名为 `tracing@codex-observability-plugin`。插件显式开启才发送，`TRACE_TO_LANGFUSE` 默认关闭。试点只使用项目级启用与项目级凭据范围，不将 shell 的全局开关作为默认配置。

插件负责丰富的会话观察；本连接器负责文稿实际快照、原始来源、缺失状态与补处理。插件默认 `LANGFUSE_CODEX_MAX_CHARS=20000`，上传失败通常记录后放行，不能只凭聊天正常结束或插件启用就认定记录完整。完整原稿以本地资产为准。[插件 README](https://github.com/langfuse/codex-observability-plugin)、[Langfuse 接入说明](https://langfuse.com/integrations/developer-tools/codex)

注意官方网页与仓库 README 的配置曾有版本差异：网页仍出现 `plugin_hooks`，仓库已说明 Codex 0.146 删除该项；当前 OpenAI 官方文档使用 `hooks`。安装版本、信任提示和实际采集结果要一起核对，不能直接照抄旧网页。

本连接器的 `Imported ...` 事件是原始记录观察，不重建官方插件的完整工具父子树，也不推算 token 或操作耗时；原始时间和工具参数留在消息记录里。若同时开启官方插件与本地队列上传，将出现两类明确命名的观察记录；它们不是两个独立的文稿版本系统。

## Langfuse 传输与可靠性

联网核查发现原方案需适应新接口：旧 trace/observation ingestion 在 Langfuse Cloud 于 **2026-11-16** 停用；自托管 v4 `events_only` 模式也不再接受旧 trace 事件。新实现默认把完整观察作为 OTLP/HTTP JSON 发送到 `/api/public/otel/v1/traces`，使用 Basic Auth 和 `x-langfuse-ingestion-version: 4`。`score-create` 继续使用 `/api/public/ingestion` 的事件信封与逐项回执。[Public API 文档](https://langfuse.com/docs/api-and-data-platform/features/public-api)、[OTEL 接入](https://langfuse.com/integrations/native/opentelemetry)

凭据只从环境读取，不写日志、仓库或 Hook 配置：

| 变量 | 用途 |
| --- | --- |
| `LANGFUSE_HOST` | 团队实例 HTTPS 根地址；本机测试允许 localhost HTTP |
| `LANGFUSE_PUBLIC_KEY` | 选定 Langfuse 项目的公钥 |
| `LANGFUSE_SECRET_KEY` | 同项目私钥 |

本连接器使用 `LANGFUSE_HOST`；官方插件的对应配置是 `LANGFUSE_BASE_URL`，不能混淆。凭据未配置时，本地功能照常工作，`sync` 返回配置缺失与待发送数量。生产 TLS 校验保持开启；若本机 Python 证书链缺失，应修复其受信任 CA 环境，不能用关闭证书校验来解决。

```python
from writing_memory.integrations import Outbox, trace_id

box = Outbox("/absolute/path/to/assets")
box.enqueue("unique-event", {
    "name": "一次写作修改", "input": "原始批示", "output": "实际文稿",
    "sessionId": "EXACT_SESSION_ID", "metadata": {"task_id": "task_ID"}
})  # 只落本地
box.sync(max_items=100)  # 唯一显式发送步骤
```

`outbox/` 保存不可覆盖的事件内容、attempts、错误与核验状态；相同 event_id、相同内容不重复入队，相同编号不同内容直接拒绝。原始材料及任务清单可在本地队列丢失时重建队列，但远端送达历史不应随意删除。网络同步使用独立 `.sync-lock/`，不会长时间占用本地采集锁；每次最多处理 100 个事件，其余留待后续显式同步。

| 状态 | 说明与后续动作 |
| --- | --- |
| `pending` | 尚未发送或明确被拒绝；后续 sync 可以重试 |
| `sending` | 网络调用前已落盘的状态；进程意外结束可辨认未完成发送 |
| `accepted` | 接收端明确收下，尚未完成公开 API 读回；后续只核验 |
| `verified` | 公开 API 读回匹配编号、内容摘要以及关键内容/评分归属 |
| `delivery_uncertain` | 超时、模糊 5xx 或发送中崩溃；可能已经送到，默认只核验 |

OTLP 每个请求发送一个完整 span；检查 `partialSuccess.rejectedSpans`。评分批量发送则检查 `successes` 内的事件 ID 与状态，同时排除 `errors`；空回执或仅 HTTP 200 都不算评分发送成功。429 尊重数字形式的 Retry-After；超大单项明确留为待处理，不静默截断。首版请求上限取 3 MB，超过者保留完整本地记录并显示 `payload_too_large`，需后续采用独立附件等策略。

trace 核验读取 `/api/public/v2/observations`，限定 traceId 和时间窗口，核对 spanId、payload 摘要及 input/output；评分读取 `/api/public/v3/scores?id=...`，核对名称、值、摘要和归属。读取延迟或内容不一致保留未核验状态，不能把 `accepted` 当成可靠落地。[Observations API](https://langfuse.com/docs/api-and-data-platform/features/observations-api)、[Scores API](https://langfuse.com/docs/api-and-data-platform/features/scores-api)

Langfuse v4 **不保证相同 span ID 重传可靠去重**。因此不存在本连接器能够承诺的远端 exactly-once：已 accepted 不重发；送达不确定先查远端。`sync(retry_uncertain=True)` 是显式选择重新发送这些事件，可能产生远端重复观察。稳定编号保证本地幂等和可追踪，但不能替接收端补造其没有的去重能力。[Langfuse v4 迁移说明](https://langfuse.com/integrations/native/opentelemetry/migration-to-v4)

## 恢复与验收

`SourceImporter.recover_saved()` 用已保存的导入清单和 document_source 完成在 capture 前中断的工作，不重新读取外部原文件、不联网。应用的 `recover` 还从持久任务、来源与反馈重建待发送事件并重试待提交 Git 资产。对于仍在写入的尾轮，重新采集显式 transcript 才能取得新增字节；恢复程序不能创造从未取得的日志。

`python3 -m unittest tests.test_integrations -v` 使用临时独立 Git 仓库与本机模拟 HTTP 服务，覆盖原始字节、消息顺序、历史文稿缺失、JSONL 半行、重启补采、重复事件、错误会话、读取失败、写作前基线、真实用户消息格式、Stop 早于完成标记、漏过多轮、断网恢复、模糊超时、逐项部分失败、429、长内容、请求上限、发送并发锁与单次处理上限。

这些测试没有上传真实材料。仍须在团队选定的 Langfuse 项目验证真实身份、区域、服务版本、远端可见性和字段映射，并完成桌面自动触发、关闭应用与再次打开的实际验收，之后才能宣布自动接入已完成。
