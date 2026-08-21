# HaloCue Backend

HaloCue 1.0 的本地优先后端仓库。当前采用模块化单体，包含写作域、制作域和单入口组合层；正式跨域入口是版本化且带哈希的 `ScriptRelease`。

## 仓库范围

- `07-正式版产品设计/`：领域模型与制作适配器合同。
- `08-HaloCue-1.0/`：制作域、AA 防腐层、正式合同样例与测试。
- `09-HaloCue-1.0-Writing/`：写作域和不可变发布流程。
- `10-HaloCue-1.0-Integrated/`：本地单入口组合层。
- `B0_CONTRACT_AUDIT.md`：B0 合同审计、实现矩阵和测试基线。
- `REFERENCE_FEATURE_MATRIX.md`：LingChat 与 letsgal studio 的功能参考边界。

## 当前实现状态

后端正式制作纵向链路已经在功能分支完成并验证：

```text
ScriptRelease/1.1
  -> ProductionRequest/1.1
  -> ProductionRun
  -> AssetManifest/1.0
  -> PerformanceDraft/1.0
  -> 人工审查
  -> StoryForge 预览渲染或 AA 兼容编译
  -> BuildBundle/1.0
```

正式链路支持幂等 handoff、不可变 PerformanceDraft Revision、乐观并发冲突、
持久 JobAttempt、重启放弃与显式重试、取消后的晚到结果隔离，以及
BuildBundle 对 ScriptRelease、PerformanceDraft 和 AssetManifest 的输入哈希绑定。
具体请求路径和示例见 [`08-HaloCue-1.0/README.md`](08-HaloCue-1.0/README.md)。

当前实现位于 `codex/halocue-production-loop`，提交到公开远程后通过 PR 合并到
`main`；本 README 不代表该功能分支已经自动合并。

## 外部仓库与本地输入

HaloCue 后端 GitHub 仓库为 [Jianmiao/halocue-backend](https://github.com/Jianmiao/halocue-backend.git)。历史阶段通过独立 PR 合并；当前正式制作闭环使用 `codex/halocue-production-loop` 分支，保持小提交和 PR 审查流程。

`https://github.com/SlimeBoyOwO/LingChat.git` 仅对应本地 `D:\StoryForge\LingChat` 的无效源码目录，不是 HaloCue 后端仓库，也不应作为本仓库的上传目标。`F:\LingChat` 仍只作为本地行为和数据参考。

本仓库不跟踪 `01-完整程序/`、`references/` 或原交接包校验清单。它们分别属于 AA 兼容运行依赖、只读逆向/StoryForge 证据和交接归档，不是 HaloCue 的产品源码，也不得随本仓库分发。

当前 AA 兼容测试仍可读取本地 `01-完整程序/aa`。后续应通过明确的外部仓库版本或适配器包提供该依赖，不能把 AA 反编译源码并入 HaloCue。

本地依赖的固定坐标、校验摘要和降级规则见 `DEPENDENCIES.md`；机器可读记录位于 `external-dependencies.json`。

## 本地验证

使用 Python 3.11 或 3.12，分别执行：

```powershell
Set-Location 08-HaloCue-1.0
python -m pytest -q

Set-Location ..\09-HaloCue-1.0-Writing
python -m pytest -q

Set-Location ..\10-HaloCue-1.0-Integrated
python -m pytest -q
```

当前正式制作闭环分支的验证结果为：制作域 `260 passed`、写作域 `58 passed`、集成域 `6 passed`。八份正式合同的冻结规则见 `08-HaloCue-1.0/contracts/README.md`。

## 集成边界

- `10-HaloCue-1.0-Integrated` 是唯一公开入口；写作和制作服务只绑定本机临时 upstream 地址。
- Gateway 统一转发请求体限制、分块传输拒绝、upstream 不可用错误和 `ApiError/1.0` 显式协商。默认仍返回旧 `{ok:false,error:{...}}` wrapper。
- 写作 SQLite 使用 `PRAGMA user_version` 和 `writing_schema_migrations` 逐步升级；启动会拒绝更新版本数据库并报告损坏数据库。
- 组合层只负责路由、生命周期和边界映射，不修改 Work、Revision、ScriptRelease 或制作领域状态。
- StoryForge 只作为制作适配器和预览/渲染引擎，不拥有 Work、Revision 或 ScriptRelease。
- AA 保持 local-only；仓库不上传 AA 源码、数据库、素材或安装目录。

## 已知未完成项

- AA 真实安装和编译验证需要用户本机已授权的 AA 环境，自动化测试使用 fixture，不依赖真实安装。
- StoryForge 视频导出器是可选能力；未配置时能力发现会明确返回缺失能力，不伪装成可用。
- UI 尚未替代后端合同；前端应按正式 API 和 `AdapterCapabilities/1.0` 接入。

## 开发约束

- 现有兼容 HTTP API 与正式 1.0 合同并存，转换必须显式且可审计。
- 模型或 Agent 输出只能形成 Proposal，不能直接成为正式 Revision、PerformanceDraft 或 BuildBundle。
- SQLite 保存身份、状态和审计；正文与产物通过工作区 URI 和 SHA-256 引用。
- 安装与 Android 公共导出必须保持独立、明确的用户操作。
- 不提交本地数据、构建产物、密钥、绝对路径或私有素材。
