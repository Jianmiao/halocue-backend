# HaloCue Backend

HaloCue 1.0 的本地优先后端仓库。当前采用模块化单体，包含写作域、制作域和单入口组合层；正式跨域入口是版本化且带哈希的 `ScriptRelease`。

## 仓库范围

- `07-正式版产品设计/`：领域模型与制作适配器合同。
- `08-HaloCue-1.0/`：制作域、AA 防腐层、正式合同样例与测试。
- `09-HaloCue-1.0-Writing/`：写作域和不可变发布流程。
- `10-HaloCue-1.0-Integrated/`：本地单入口组合层。
- `B0_CONTRACT_AUDIT.md`：B0 合同审计、实现矩阵和测试基线。

## 外部仓库与本地输入

HaloCue 后端 GitHub 仓库为 [Jianmiao/halocue-backend](https://github.com/Jianmiao/halocue-backend.git)。B3 与 R1-R4 已在 PR #3 合并到 `main`；当前 B4 集成工作基于该合并提交，在 `codex/halocue-b4` 分支推进。`codex/halocue-b2` 保留为上一阶段开发分支。

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

B4 当前结果为制作域 `257 passed`、写作域 `58 passed`、集成域 `5 passed`。八份正式合同的冻结规则见 `08-HaloCue-1.0/contracts/README.md`。

## B4 集成边界

- `10-HaloCue-1.0-Integrated` 是唯一公开入口；写作和制作服务只绑定本机临时 upstream 地址。
- Gateway 统一转发请求体限制、分块传输拒绝、upstream 不可用错误和 `ApiError/1.0` 显式协商。默认仍返回旧 `{ok:false,error:{...}}` wrapper。
- 写作 SQLite 使用 `PRAGMA user_version` 和 `writing_schema_migrations` 逐步升级；启动会拒绝更新版本数据库并报告损坏数据库。
- 组合层只负责路由、生命周期和边界映射，不修改 Work、Revision、ScriptRelease 或制作领域状态。

## 开发约束

- 现有兼容 HTTP API 与正式 1.0 合同并存，转换必须显式且可审计。
- 模型或 Agent 输出只能形成 Proposal，不能直接成为正式 Revision、PerformanceDraft 或 BuildBundle。
- SQLite 保存身份、状态和审计；正文与产物通过工作区 URI 和 SHA-256 引用。
- 安装与 Android 公共导出必须保持独立、明确的用户操作。
- 不提交本地数据、构建产物、密钥、绝对路径或私有素材。
