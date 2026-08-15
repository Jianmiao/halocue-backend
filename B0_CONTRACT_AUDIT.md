# HaloCue 1.0 B0 合同审计

更新日期：2026-08-15

状态：B0 可实施范围已完成。八份核心合同已形成 1.0 冻结基线，包含固定 JSON 样例、可执行校验器、哈希向量和契约测试；不兼容变更必须升合同版本。正式合同尚未接入现有 HTTP API、持久化或任务运行时，本阶段未进入 B1。

## 资料与代码边界

- 六份优先资料均存在并已阅读：两份 `07-正式版产品设计` 文档、写作交接合同、写作/制作/集成 README。
- 已阅读现有领域模型、仓储、任务、HTTP 路由、AA 防腐层及相关测试。仓库中没有 `AGENTS.md`。
- `01-完整程序/aa` 和 AA 反编译资料仅作行为证据；B0 没有修改、复制或引入其源码和素材。
- 本目录已按多仓库策略建立独立 Git 仓库；正式设计、写作/制作/集成代码、合同和测试进入基线，`01-完整程序/`、`references/`、缓存、数据和交接归档清单由根 `.gitignore` 明确排除。

## B0 冻结决定

- 每份 payload 都包含 `schema_version: "1.0"`，八份合同统一拒绝未知版本和未知字段。
- 正式持久对象使用小写 canonical UUID；现有前缀 ID 只留在明确标记的兼容边界。
- 哈希文本使用 `sha256:<64 lowercase hex>`。canonical JSON 使用 UTF-8、Unicode 键排序、无空白分隔、`ensure_ascii=false` 并禁止 NaN/Infinity。
- `ScriptRelease.content_hash` 对冻结正文文件字节求哈希；`PerformanceDraft` 和 `AssetManifest` 对移除顶层 `content_hash` 后的 canonical JSON 求哈希；`ProductionRequest.idempotency_key` 对移除自身字段后的 envelope 求哈希；BuildBundle/Artifact 对实际交付文件字节求哈希。
- 工作区对象使用 `workspace://`，产物引用使用 `artifact://`；绝对路径、反斜杠、明文或多重编码路径穿越均拒绝。
- PerformanceDraft 的 branch 只由 choice option 定义且全 Draft 唯一；演出行只能引用同 Scene branch；choice target 必须是同 Scene node。跨 Scene 跳转需要未来显式版本合同。
- `ProductionEvent` 强制包含 `run_id/work_item_id/attempt_id/sequence/timestamp`，`request_id` 仅作可选关联。
- 正式 `ScriptRelease/1.0` 与现有 `WritingHandoff/1.0` 分离，不从兼容 payload 伪造 UUID、Revision、Gate 或 manifest URI。
- 模型生成只产生 Proposal 和证据；只有人工采纳且 CAS 校验通过后才能更新兼容草稿。旧版已写入 Proposal 继续按 `legacy_applied` 兼容处理。

## 核心合同矩阵

| 合同 | 运行实现状态 | B0 合同状态 | 已验证行为 | 运行缺口或冲突 |
|---|---|---|---|---|
| `ScriptRelease/1.0` | 部分实现 | 1.0 已冻结 | 冻结正文字节 SHA-256 固定向量、未知版本拒绝、兼容交接幂等和同 ID 异哈希 409、兼容 payload 不会冒充正式合同 | 运行模型仍用前缀 ID、裸哈希和缩减字段，没有正式 manifest/Revision/Gate 快照 |
| `ProductionRequest/1.0` | 部分实现 | 1.0 已冻结 | canonical envelope 幂等键固定向量和过期键拒绝；现有 `/production-runs` 可复用相同兼容 Release | 现有入参无 request UUID、AssetManifest、完整 policy 和正式合同入口 |
| `PerformanceDraft/1.0` | 部分实现 | 1.0 已冻结 | Scene/node/line/branch UUID、完整 `cast_state`、同 Scene 分支图、来源哈希、审查状态；模型结果保持 Proposal-only | 运行仍是 AA card/CG 兼容模型，尚未持久化正式 PerformanceDraft |
| `AssetManifest/1.0` | 未实现 | 1.0 已冻结 | canonical hash、UUID/URI/哈希/类型/允许列表校验和路径泄漏阻断 | 运行仅冻结可变 `resources.json`，无正式 manifest 身份、URI 和登记 |
| `AdapterCapabilities/1.0` | 部分实现 | 1.0 已冻结 | 标准 adapter/engine/version、目标、合同版本和能力枚举校验；兼容能力明确报告正式合同未接入 | 现有运行形状仍是 AA 功能树，没有 StoryForge 能力实现 |
| `BuildBundle/1.0` | 部分实现 | 1.0 已冻结 | 三类输入哈希、producer、不可变 deliverable 引用校验；已有隔离编译和单文件 SHA-256 清单 | 无正式 Artifact UUID/URI、三类输入哈希登记和不可变 BuildBundle 仓储 |
| `ProductionEvent/1.0` | 未实现 | 1.0 已冻结 | Run/WorkItem/Attempt、sequence、时间和成功 Artifact 证据校验 | 现有 JobRecord 无 WorkItem/Attempt/事件流，状态集合也未对齐 |
| `ApiError/1.0` | 部分实现 | 1.0 已冻结 | category、retryability、detail/scope/attempt refs 校验；兼容错误和诊断已阻止路径与异常原文泄漏 | 兼容 API wrapper 尚无正式字段和 payload 版本 |

## 合同与代码差异证据

1. canonical JSON、哈希边界和幂等键在 `08-HaloCue-1.0/src/halocue_production/contracts.py:204-252`；八合同校验入口在 `contracts.py:1075`。固定样例 round-trip、未知版本、缺字段和未知字段测试在 `tests/test_formal_contracts.py:40-101`，哈希固定向量在 `test_formal_contracts.py:237-295`。
2. `ScriptRelease`：正式字段在 `contracts.py:368`；现有模型从 `models.py:14` 起生成前缀 ID，`models.py:23` 的 Release 仍是缩减形状。兼容入口在 `service.py:583` 起校验、冻结和创建 Run，`service.py:666` 返回稳定身份冲突。
3. `ProductionRequest`：正式字段在 `contracts.py:403`；现有 `service.py:583` 的创建入口仍直接消费 generation mode、project、source 和可选兼容交接。
4. `PerformanceDraft`：正式顶层在 `contracts.py:466`，分支和 target 约束在 `contracts.py:545-766`。现有运行仍由 `legacy_adapter.py` 的 card/CG 模型承载；Proposal 查询与采纳事务在 `legacy_adapter.py:1011-1257`，模型执行只保存建议和证据在 `legacy_adapter.py:1908-1956`。
5. `AssetManifest`：正式校验在 `contracts.py:769`；现有 `legacy_adapter.py:269-314` 直接维护 `resources.json`，没有正式 manifest 身份和 Artifact 登记。
6. `AdapterCapabilities`：正式校验在 `contracts.py:825`；现有能力树从 `legacy_adapter.py:73` 生成，`service.py:127-151` 明确报告 `WritingHandoff/1.0` 以及正式合同 `not_connected`。
7. `BuildBundle`：正式校验在 `contracts.py:882`；现有 `legacy_adapter.py:1633` 创建隔离编译快照，但未形成正式不可变 BuildBundle。公开 Job 在 `service.py:1400-1451` 移除底层错误、重试上下文和 `bundle_dir`。
8. `ProductionEvent`：正式校验在 `contracts.py:956`；当前 `jobs.py:47-95` 仍以线程池、内存 Future 和零散 Job JSON 为核心，遗留运行只恢复为 `interrupted`，不是正式 Attempt/Event 模型。
9. `ApiError`：正式校验在 `contracts.py:1010`；当前 `errors.py:4-26` 仍生成兼容 `{ok:false,error:{code,message,details}}`，`app.py:335-342` 负责 HTTP 错误映射。

## 已固定的兼容行为

- 兼容交接 fixture 为 `08-HaloCue-1.0/tests/fixtures/script_release_handoff_1_0.json`，能力与冻结元数据分别标记为 `WritingHandoff/1.0` 和 `formal_script_release:false`。
- 正式样例的 `ScriptRelease.content_hash` 与上述 fixture 的 `source.text` UTF-8 字节哈希完全一致；兼容边界仅去掉 `sha256:` 前缀，不改变哈希内容。
- 未提供 `schema_version` 时只按兼容合同解释为 `1.0`；正式合同必须显式提供。非 `1.0` 兼容 Release 在写入 Release/Run 前返回 `400 unsupported_script_release_version`。
- 相同上游 release ID 和内容哈希返回原 Run；相同 ID 对应不同内容返回 `409 script_release_identity_conflict`。
- 兼容 payload 缺少正式字段时由正式校验器结构化拒绝，不自动补造身份或发布证据。
- 公开 API 不返回构建、安装、AA 工作区、写作数据目录或语料库物理路径；公开 Job 错误保留稳定 code，不返回底层异常原文。
- 新模型生成记录使用 `application_mode:proposal_only`。拒绝只记录决定，采纳才更新草稿；生成期间发生用户编辑时，晚到模型结果不能覆盖当前草稿。

## 新建与修改文件

- 新建合同层：`08-HaloCue-1.0/src/halocue_production/contracts.py`、`08-HaloCue-1.0/contracts/README.md`、`08-HaloCue-1.0/contracts/examples/*.json` 八份样例。
- 新建契约测试：`08-HaloCue-1.0/tests/test_formal_contracts.py`、`08-HaloCue-1.0/tests/fixtures/script_release_handoff_1_0.json`。
- 修改制作兼容层与测试：`service.py`、`legacy_adapter.py`、`ui/app.js`、`tests/test_contracts.py`、`tests/test_service.py`、`tests/test_http_api.py`。
- 修改写作侧脱敏与测试：`09-HaloCue-1.0-Writing/src/halocue_writing/service.py`、`official_reference_catalog.py`、`tests/test_settings_hub.py`、`tests/test_http_api.py`。
- 更新本审计记录：`B0_CONTRACT_AUDIT.md`。

## 测试基线与结果

| 套件 | B0 初始基线 | B0 当前结果 |
|---|---:|---:|
| HaloCue 制作域 | 72 passed | 138 passed |
| 其中正式合同测试 | 0 | 57 passed |
| HaloCue 写作域 | 57 passed | 57 passed |
| HaloCue 集成域 | 3 passed | 3 passed |
| StoryForge Vitest | 26 passed | 26 passed |
| StoryForge `studio-core` | 25 passed | 25 passed |
| StoryForge build | passed | passed |

- 正式合同测试覆盖八样例 round-trip、未知版本/字段、稳定身份、URI/私密字段、完整 cast、事件成功证据、错误分类，以及 canonical hash/idempotency 固定向量与过期哈希拒绝。
- Proposal 专项覆盖生成不改草稿、采纳后才写入、拒绝不写入、旧数据兼容和晚到模型结果不覆盖用户编辑。`ui/app.js` 通过 Node 语法检查。
- 本轮 StoryForge Playwright 已成功启动 Vite，但前两个用例均在 `page.goto("/")` 等待 90 秒后超时；确认同类基础设施导航问题后停止其余六个重复用例。该目录未被 B0 修改，不能把这次未完成套件记为通过。
- 完整 Rust 工作区桌面 IPC 测试仍受本机 `STATUS_ENTRYPOINT_NOT_FOUND` 的既有环境问题影响；独立 `studio-core` 基线套件全部通过。
- 制作域尚无 SQLite 实现，因此 B0 不可伪称已验证 SQLite 新建、升级、损坏和重启恢复。

## 兼容性影响

- 现有 `/api/v1` 路由、请求形状和成功响应未重命名、未删除。
- 正式校验器尚未在 HTTP 路由中自动执行，因此不会将现有前缀 ID 或裸哈希 payload 突然判为非法。
- 兼容 `ScriptRelease` 只新增确定性缺陷修复：未知版本预写入拒绝、同 ID 异哈希 409 与幂等重复提交。
- 方向模型行为有一项有意的领域修复：新生成结果不再自动写草稿，调用方必须明确采纳 Proposal；旧 `legacy_applied` 记录仍可读取和决定。
- 公开错误和诊断响应收紧为脱敏信息；内部编译、安装和调试数据仍保留完成本地操作所需的物理路径。

## 尚未解决的风险与决定

1. AA 兼容运行依赖已从 HaloCue 仓库排除，但其独立仓库地址、允许分发的包边界和固定版本尚未提供。当前测试继续读取被忽略的本地 `01-完整程序/aa`；建立 CI 前必须把它替换成明确版本的外部依赖，不能复制反编译源码回 HaloCue。
2. 正式合同尚未接入 HTTP、仓储和运行时。这是 B1-B4 的计划实现缺口，不是 B0 合同歧义；能力发现已明确返回 `formal_contract_state:not_connected`，避免调用方误判。
3. 1.0 明确不支持跨 Scene 隐式分支跳转。若产品确需此能力，应定义显式 Scene/entry target 并升级合同版本，不能放宽当前 node ID 语义。
4. Playwright 的本地导航超时和完整桌面 Rust 环境错误仍会阻止上层 StoryForge 工作区全绿，但都不位于本次独立 HaloCue 仓库。进入集成阶段前仍应由对应仓库修复或建立稳定的 CI 环境基线。

当前没有会影响两名协作者分工的未决合同语义。Git 仓库归属已经确定；尚缺的是 AA 外部依赖坐标和新仓库远程地址，不影响冻结 B0 本地基线。

## 下一阶段建议

B0 独立仓库基线建立后，单独确认是否进入 B1。B1 应从新分支开始，只处理持久 `ProductionRun/WorkItem/JobAttempt`、恢复与取消，不顺带实现 B2 或新适配器。
