# HaloCue 1.0 核心合同包

状态：B0 的 1.0 冻结基线。本目录的八份 JSON 是正式领域合同样例，可执行规则位于 `src/halocue_production/contracts.py`。当前 AA 兼容 HTTP payload 仍由防腐层处理，不属于这些样例的替代响应。后续不兼容变更必须使用新的合同版本，不能静默改写 1.0 样例或哈希规则。

## 通用规则

- 每份 payload 必须包含 `schema_version: "1.0"`。
- 正式持久对象使用 canonical UUID；现有 `release-xxxxxxxxxxxx` 等 ID 只能在兼容边界转换。
- 哈希统一使用 `sha256:<64 lowercase hex>`。
- 文件和产物仅使用 `workspace://` 或 `artifact://` URI，不允许系统路径、反斜杠或 `..` 穿越。
- `canonical_json_bytes()` 使用 UTF-8、Unicode 键排序、无空白分隔符、`ensure_ascii=false` 且禁止 NaN/Infinity。PerformanceDraft 和 AssetManifest 对去掉顶层 `content_hash` 的 canonical JSON 求哈希；ProductionRequest 的 `idempotency_key` 对去掉自身字段的 envelope 求哈希。ScriptRelease 对冻结正文文件字节求哈希，BuildBundle 与 Artifact 对实际交付文件字节求哈希。固定向量在 `tests/test_formal_contracts.py` 中。
- 校验器严格拒绝未知字段、未知版本和密钥/物理路径字段。

## 合同职责

| 合同 | 样例 | 边界 |
|---|---|---|
| `ScriptRelease/1.0` | `examples/script-release-1.0.json` | 写作域拥有的不可变发布清单 |
| `ProductionRequest/1.0` | `examples/production-request-1.0.json` | 制作 Run 的固定输入、白名单和策略 |
| `PerformanceDraft/1.0` | `examples/performance-draft-1.0.json` | 与 AA/StoryForge 无关的标准演出模型 |
| `AssetManifest/1.0` | `examples/asset-manifest-1.0.json` | 任务冻结素材白名单 |
| `AdapterCapabilities/1.0` | `examples/adapter-capabilities-1.0.json` | 适配器、引擎、目标和合同版本发现 |
| `BuildBundle/1.0` | `examples/build-bundle-1.0.json` | 已验证输入与不可变交付物集合 |
| `ProductionEvent/1.0` | `examples/production-event-1.0.json` | Run/WorkItem/Attempt 关联的单调事件 |
| `ApiError/1.0` | `examples/api-error-1.0.json` | 可分类、可决策重试、不泄漏私密数据的错误 |

## PerformanceDraft 约束

- `scenes[].nodes[]` 显式区分演出行和选择组，Scene、node、line、choice group 和 branch 都使用稳定 UUID。
- branch 只能由 choice option 定义，在整份 Draft 中唯一；演出行的 `branch_id` 是对同 Scene option 的引用，不是新定义。
- choice option 的 `target_node_id` 必须存在于同一 Scene。跨 Scene 跳转不通过隐式 node ID 实现，留给未来显式版本合同。
- 每条演出行必须包含完整 `cast_state`，即使为空数组，不使用隐式增量状态。
- 站位使用归一化坐标和 anchor，不绑定三槽位。
- 背景、弹出图、BGM、语音和音效必须引用 AssetManifest 中的稳定 asset ID、URI 和哈希。
- `provenance.created_by` 不包含 `model`。模型产物只能通过 Proposal 人工采纳后形成 Draft，并保留 proposal/attempt 引用。

## 事件与错误

- `ProductionEvent.run_id/work_item_id/attempt_id/sequence/timestamp` 必填；`request_id` 仅用于适配器调用关联。
- `artifact_created` 和 `operation_succeeded` 必须包含已验证 `artifact_refs`，百分比不能代替成功证据。
- `ApiError` 是标准错误对象；现有 `{ok:false,error:{...}}` wrapper 将在后续 API 组合层显式映射。

本合同包只使用 Python 标准库，没有新增运行时或分发依赖。

## 兼容交接边界

- 现有 `WRITING_HANDOFF_CONTRACT.md` 的 payload 标记为 `WritingHandoff/1.0`，不是正式 `ScriptRelease/1.0`。
- 兼容交接继续验证冻结正文哈希、幂等和同 ID 异哈希冲突，但不伪造 UUID、manifest URI、Revision 或 Gate 快照。
- 正式入口只能接受完整 `ScriptRelease/1.0`；缺少字段时结构化拒绝，不从标题、文件名或数组位置推导。
- 未来转换层必须保留两种合同身份和原始哈希，不就地覆盖兼容记录。

## ProductionRequest 运行时入口缺口

`ProductionRequest/1.0` 当前只引用 ScriptRelease 的 `manifest_uri` 和正文
哈希；`ScriptRelease/1.0` 清单不包含正文 Artifact 的 URI，
`ProductionRequest/1.0` 也不包含制作工程展示名。因此当前不从
`manifest_uri` 的文件名或目录布局猜测正文，也不把展示版本当成工程身份。
在正式 HTTP 入口可创建可制作 Run 之前，需要产品负责人选择并冻结下列一种方案：

- 新增带显式 `content_uri` 的 `ScriptRelease/1.1`；或
- 冻结一份版本化的本地工作区布局合同，并为制作展示名增加独立、非身份字段。

这一缺口不影响现有 `WritingHandoff/1.0` 兼容路线，也不影响
AssetManifest 冻结、后继版本和白名单阻断。
