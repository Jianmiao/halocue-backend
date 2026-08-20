# 参考功能矩阵

本文件只记录对本地参考应用可观察到的工作流能力，以及 HaloCue 后端的对应状态。它不复制任何源码、素材、品牌资源、私有序列化格式或安装包内容。

| 参考对象 | 可观察能力 | HaloCue 对应能力 | 状态 | 处理原则 |
| --- | --- | --- | --- | --- |
| LingChat | 剧本模式、章节组织、角色设定、场景预览、模型 Provider 与本地语音/素材工作流 | `Work / Volume / Chapter / Scene`、CharacterCard、WritingPack、Provider 边界、`PerformanceDraft` | 已实现 / 部分实现 | 借鉴任务编排与信息分层，不复制源码或数据格式 |
| LingChat | Agent 工具调用、记忆、权限确认与人工介入 | Proposal、Evidence、Gate、AgentRun、授权策略、禁止模型输出直接成为正式产物 | 部分实现 | 以写作域的 Proposal、Revision、Release Gate 为事实源 |
| letsgal studio | 本地桌面创作壳、脚本段落/对白/选项、预览、历史与保存反馈 | 集成 Gateway、StoryForge 节点/选择/退出模型、离线预览、原子文件提交、不可变 Revision | 部分实现 | 只采纳可验证的本地工作流线索，不接入安装包内部实现 |
| letsgal studio | 具体运行时扩展与私有资源组织 | HaloCue 适配器能力与 `AssetManifest/1.0` 白名单 | 未纳入 1.0 | 需产品负责人确认后再形成版本化合同 |

## HaloCue 当前边界

- 写作域拥有作品、场景、Revision、Proposal、Gate 和 `ScriptRelease`；制作域只接收带哈希的正式 release 冻结副本。
- 正式 handoff 会创建并持久化 `formal_release_id`、`production_request_id`、`formal_work_id`、`production_run_id` 和内容哈希。相同身份与内容重复提交幂等，不同内容或身份尝试覆盖时返回稳定 409。
- 正式 handoff 初始创建空白 `AssetManifest/1.0`。制作侧通过任务素材登记和 successor manifest API 显式选择素材；manifest 是不可变 revision，过期 manifest、白名单外素材、URI/哈希/文件不一致都会被阻断。
- 资源查询只返回稳定标识和展示元数据，不返回本机绝对路径或素材库真实物理路径。

## 明确不纳入

- 不上传或引用 AA 源码、数据库、安装目录和素材；AA 继续 local-only。
- 不把 LingChat 源码、`app.asar`、扩展包或第三方素材加入仓库。
- 不以标题、数组下标、文件名或 UI DOM 作为跨域事实源；跨域唯一正式入口是版本化且带哈希的 `ScriptRelease`。
