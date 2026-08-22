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

后端正式制作纵向链路已经完成并验证。这里的“闭环”指不依赖真实 AA
安装、真实模型密钥或不可分发素材的本地自动化链路：

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

当前闭环代码已推送到公开远程分支
`codex/halocue-production-loop`。分支推送不等于合并到 `main`；合并状态以
GitHub PR 页面为准。

闭环验收范围：写作发布、制作请求、持久运行、标准演出草稿、人工审查、StoryForge
预览/可选视频导出、AA 兼容编译边界和不可变 `BuildBundle` 均由合同、SQLite
状态和 Artifact 哈希串联。真实 AA 编译/安装仍属于用户本机的手动或 integration
验收，不作为公开仓库的自动化前提。

## 产品定位

HaloCue Backend 是 HaloCue 1.0 的本地优先创作后端，负责把写作域发布的
不可变剧本版本转换成可审查、可预览、可编译的制作交付物。它解决的是“版本
冻结、素材边界、演出审查和可恢复任务”问题，而不是提供一个临时的页面状态
存储层。

它的核心特点是：

- **写作与制作隔离**：制作端只读取 `ScriptRelease` 的冻结副本，不回写
  Work、Revision 或正式发布记录。
- **本地可恢复**：SQLite 保存身份、状态、审计和 JobAttempt；正文、素材、日志
  和产物通过工作区 URI 与 SHA-256 文件保存。
- **引擎可替换**：标准 `PerformanceDraft/1.0` 不依赖 AA 私有格式；StoryForge
  和 AA 通过能力声明与适配器接入。
- **显式交付**：人工审查通过后才能 render/compile；安装和 Android 公共导出
  不会被编译操作隐式触发。

## 具体使用场景

### 场景一：写作发布后进入正式制作

写作服务先发布 `ScriptRelease/1.1`，并登记对应的正文 Artifact 和
`AssetManifest/1.0`。制作服务收到 `ProductionRequest/1.1` 后会重新校验正文
哈希、清单哈希和 URI，创建本地冻结副本与 `ProductionRun`。同一 request ID
和同一内容重复提交会返回原 Run；相同身份但内容不同会返回稳定的 HTTP 409。

适合：章节已经通过写作侧 Gate，需要交给制作人员继续处理的正式流程。

### 场景二：StoryForge 独立预览与渲染

将 `production_policy.target` 设为 `storyforge_preview`，创建标准
`PerformanceDraft/1.0`，在审查界面修订场景、对白、角色状态和媒体引用，提交
人工批准后执行 `render`。成功结果会登记为 `BuildBundle/1.0`，并绑定三组输入
哈希：ScriptRelease、PerformanceDraft 和 AssetManifest。

适合：不依赖 AA 安装、需要快速预览分支和演出效果的本地创作流程。

`storyforge_video` 只有在本机配置视频 exporter 并由能力发现明确声明后才可用；
缺少能力时返回结构化错误，不伪装成成功。

### 场景三：AzureArchive 兼容编译与显式安装

将目标设为 `pc_aap`，适配器使用本地 AA 资源索引和授权工作区执行兼容编译，
生成标准 `BuildBundle/1.0`。安装是单独的用户操作，服务只做安装前检查和目标
冲突提示，不把 AA 安装目录提交到仓库，也不在编译时自动安装。

适合：已有 AzureArchive 制作资产、需要兼容 `.aap` 交付，同时保持 HaloCue
领域模型独立的本地工作流。

### 场景四：服务中断后的继续处理

制作任务执行期间服务退出，遗留的 `queued/running` Attempt 会被标记为
`abandoned`。重新启动后只能基于持久化的 request、适配器、目标、Revision 和
输入哈希显式创建新的 Attempt；旧 Attempt 的迟到结果不能成为正式 Artifact。

适合：长时间渲染、模型调用或桌面应用重启后的安全恢复。

## 核心参数

### 服务运行参数

| 参数 | 默认值 | 作用 |
|---|---|---|
| `host` | `127.0.0.1` | 仅监听本机，避免把制作 API 暴露到局域网 |
| `port` | `8892` | 后端 HTTP 端口 |
| `HALOCUE_DATA_DIR` | `08-HaloCue-1.0/data` | SQLite、工作区 Artifact 和运行状态目录 |
| `HALOCUE_LEGACY_ROOT` | 本地 AA 兼容模块目录 | 只读加载兼容行为，不上传其源码 |
| `HALOCUE_RESOURCE_INDEX` | 未配置 | AA 角色、背景、音效等资源索引 |
| `HALOCUE_AA_DATA` | 未配置 | AA 安装前检查和显式安装使用的本地工作区 |

生产环境仍应保持 `host=127.0.0.1`。未配置 AA 资源或工作区时，StoryForge
预览和合同、任务、Artifact 测试仍可独立工作；AA 编译或安装会明确报告能力或
环境缺失。

### `ProductionRequest/1.1` 参数

正式入口需要以下字段，完整固定样例见
[`08-HaloCue-1.0/contracts/examples/production-request-1.1.json`](08-HaloCue-1.0/contracts/examples/production-request-1.1.json)：

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | string | 必须为 `1.1`；未知版本拒绝 |
| `request_id` | UUID | ProductionRequest 的稳定身份 |
| `production_display_name` | string | 面向用户的制作名称，不参与 ScriptRelease 身份哈希 |
| `script_release` | object | `version=1.1`、release ID、正文 URI、manifest URI 和 `content_hash` |
| `script_manifest_version` | string | 当前为 `1.1` |
| `asset_manifest` | object | AssetManifest ID、版本、URI 和内容哈希 |
| `production_policy` | object | 素材引用策略、占位符策略和制作目标 |
| `idempotency_key` | `sha256:` | 对去掉自身字段的请求 envelope 求哈希 |

`production_policy` 的正式参数为：

```json
{
  "asset_reference_mode": "whitelist_only",
  "allow_placeholders": false,
  "target": "storyforge_preview"
}
```

当前支持的正式目标由 `GET /api/v1/production-adapters` 的
`AdapterCapabilities/1.0` 动态声明，已验证的目标包括 `storyforge_preview` 和
`pc_aap`。客户端不能根据标题、数组位置或文件名推导稳定 ID。

### Draft、任务和交付物参数

- `PerformanceDraft/1.0`：稳定 Scene、node、line、choice group、branch ID；
  每条演出行保存完整 `cast_state`，并引用角色、背景、语音、音效等白名单素材。
- `expected_revision_id`：更新 Draft 的必填并发保护字段；过期时返回 409，
  不静默覆盖其他 Revision。
- `review_status`：只能通过审查门面改变；只有 `approved` Revision 能 render 或
  compile。
- `ProductionEvent/1.0`：每个事件带 `run_id`、`work_item_id`、`attempt_id`、
  单调 `sequence` 和时间戳；百分比只表示进度，不代表成功。
- `BuildBundle/1.0`：不可变交付清单，必须携带 target、producer、deliverables
  以及 ScriptRelease、PerformanceDraft、AssetManifest 三组输入哈希。

## 标准操作流程

```text
1. 登记 ScriptRelease/1.1 正文和 manifest Artifact
2. 登记 AssetManifest/1.0，并提交 ProductionRequest/1.1
3. 创建 ProductionRun，冻结上游身份和素材白名单
4. 创建或更新 PerformanceDraft/1.0
5. 执行 validate，处理 error/warning/info 诊断
6. 人工 approve
7. 根据 capabilities 执行 StoryForge render 或 AA compile
8. 查询 JobAttempt，读取已验证 BuildBundle/1.0
9. 如需 AA 安装，单独执行 install-check 和显式安装
```

主要 API 分组：

```text
GET  /api/v1/production-adapters
POST /api/v1/production-runs
GET  /api/v1/production-runs/{run_id}
POST /api/v1/production-runs/{run_id}/performance-drafts
PATCH /api/v1/production-runs/{run_id}/performance-drafts/{draft_id}
POST /api/v1/production-runs/{run_id}/performance-drafts/{draft_id}
POST /api/v1/production-runs/{run_id}/performance-drafts/{draft_id}/operations
GET  /api/v1/jobs/{job_id}
```

默认错误响应保持兼容的 `{ok:false,error:{...}}` wrapper；协商
`ApiError/1.0` 后才返回标准错误对象。错误响应不返回 API Key、完整请求正文、本机
绝对路径或真实素材物理路径。

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

本轮完整验证结果为：制作域 `268 passed`、写作域 `58 passed`、集成域
`6 passed`。八份正式合同的冻结规则见
`08-HaloCue-1.0/contracts/README.md`。

## 集成边界

- `10-HaloCue-1.0-Integrated` 是唯一公开入口；写作和制作服务只绑定本机临时 upstream 地址。
- Gateway 统一转发请求体限制、分块传输拒绝、upstream 不可用错误和 `ApiError/1.0` 显式协商。默认仍返回旧 `{ok:false,error:{...}}` wrapper。
- 写作 SQLite 使用 `PRAGMA user_version` 和 `writing_schema_migrations` 逐步升级；启动会拒绝更新版本数据库并报告损坏数据库。
- 组合层只负责路由、生命周期和边界映射，不修改 Work、Revision、ScriptRelease 或制作领域状态。
- StoryForge 只作为制作适配器和预览/渲染引擎，不拥有 Work、Revision 或 ScriptRelease。
- AA 保持 local-only；仓库不上传 AA 源码、数据库、素材或安装目录。

## 验收边界与后续决定

- AA 真实安装和编译验证需要用户本机已授权的 AA 环境，自动化测试使用 fixture，不依赖真实安装。
- StoryForge 视频导出器是可选能力；未配置时能力发现会明确返回缺失能力，不伪装成可用。
- 正式工作台已经接入 `PerformanceDraft`、审查、校验、预览/视频任务和 Job 轮询；后续 UI 视觉完善不改变后端合同。
- 写作侧原生 Canonical UUID 映射和初始 `AssetManifest` 的产品选择仍需单独决策；当前稳定 UUID 投影与空白白名单不阻塞本地闭环。
- `ApiError/1.0` 继续通过显式协商返回，默认兼容错误 wrapper 保持不变，待全部调用方迁移后再评估默认切换。

## 开发约束

- 现有兼容 HTTP API 与正式 1.0 合同并存，转换必须显式且可审计。
- 模型或 Agent 输出只能形成 Proposal，不能直接成为正式 Revision、PerformanceDraft 或 BuildBundle。
- SQLite 保存身份、状态和审计；正文与产物通过工作区 URI 和 SHA-256 引用。
- 安装与 Android 公共导出必须保持独立、明确的用户操作。
- 不提交本地数据、构建产物、密钥、绝对路径或私有素材。
