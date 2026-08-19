# B4 集成阶段报告

## 1. 目标与完成范围

本阶段将写作域、制作域和本地单入口组合层的运行边界补齐，保持 PR #3 已冻结的 B3、R1-R4 行为不变。

已完成：

- Gateway 请求体大小、负长度和分块传输校验；upstream 不可用时统一返回稳定错误。
- 兼容错误 wrapper 默认行为保持不变；写作域和 Gateway 支持显式 `ApiError/1.0` 协商。
- 写作 SQLite 增加 `PRAGMA user_version`、`writing_schema_migrations` 和 1->2 迁移链，并增加新建、升级、重启、更新版本拒绝和损坏处理。
- IntegratedRuntime 增加可重复调用的启动、前台运行和幂等关闭；关闭未启动实例不会死锁。
- 组合层不再向诊断响应暴露内部制作端口，公开地址固定为 `/production`。

未做：正式 `ProductionRequest/1.1` 尚未替换现有写作兼容交接；它仍由制作域的正式版本化入口单独承载，避免在共享 ArtifactStore 和正式 UUID 身份尚未统一时伪装成正式跨域链路。

## 2. 文件变化

- `09-HaloCue-1.0-Writing/src/halocue_writing/{app,errors,repository,service}.py`
- `09-HaloCue-1.0-Writing/tests/test_http_api.py`
- `10-HaloCue-1.0-Integrated/src/halocue_integrated/{gateway,server}.py`
- `10-HaloCue-1.0-Integrated/tests/test_gateway.py`
- 根 README 与集成 README

## 3. 兼容性影响

写作数据库旧 `user_version=0` 会先建立基线再执行领域迁移；版本高于当前程序时返回 `writing_database_version_unsupported`，不会覆盖数据。HTTP 默认响应格式、现有路径和旧写作交接 payload 保持兼容。显式协商只改变错误响应视图，不改变状态码或领域错误码语义。

## 4. 测试结果

- 制作域：`257 passed`
- 写作域：`58 passed`
- 集成域：`5 passed`

新增覆盖：写作 `ApiError/1.0` 协商、SQLite 版本迁移/重启/损坏；集成运行时生命周期、内部端点脱敏和 Gateway upstream 错误协商。

## 5. 尚未解决的风险与产品决定

- 写作兼容交接仍使用 `WritingHandoff/1.0`；正式 `ScriptRelease/1.1 -> ProductionRequest/1.1` 组合需要共享 ArtifactStore 和 UI 迁移后再接入。
- ApiError 默认切换继续等待前端和全部调用方迁移，当前不需要产品新增决定。
- GitHub 网络同步本轮仍被连接重置阻断；本地 B4 分支基于 PR #3 已知 head，推送需在网络恢复后完成。

## 6. 下一步建议

推送 `codex/halocue-b4` 并创建独立 PR；合并前在干净环境各运行一次三域测试，再评估正式跨域请求是否具备共享 ArtifactStore、canonical UUID 和前端迁移条件。
