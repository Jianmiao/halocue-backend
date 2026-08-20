# B5 正式纵向链路报告

## 1. 目标与完成范围

已完成集成组合层的正式链路：

```text
Writing ScriptRelease
  -> stable UUID projection
  -> ScriptRelease/1.1 + AssetManifest/1.0 artifacts
  -> ProductionRequest/1.1
  -> persistent ProductionRun
```

写作域仍拥有原始 Work、Revision、Gate 和 Release。制作域只读取共享本地
ArtifactStore 中的冻结副本，不回写写作数据库。没有共享 ArtifactStore 的独立
写作服务继续保留 `WritingHandoff/1.0` 兼容路径。

## 2. 新建/修改文件

- `09-HaloCue-1.0-Writing/src/halocue_writing/service.py`
  - 增加正式 handoff 组装、稳定 UUID 投影、请求幂等键和安全错误映射。
- `10-HaloCue-1.0-Integrated/src/halocue_integrated/server.py`
  - 将制作 ArtifactStore 以字节发布器注入写作服务。
- `10-HaloCue-1.0-Integrated/tests/test_gateway.py`
  - 真实纵向链路改为校验 `ProductionRequest/1.1`、正式 UUID 和重复提交幂等。
- `08-HaloCue-1.0/contracts/README.md`
- `10-HaloCue-1.0-Integrated/README.md`

## 3. 兼容性影响

- 现有写作服务构造参数保持兼容；新增发布器参数为可选。
- 现有无发布器的 `handoff_release()` 行为不变。
- 集成运行时的 handoff 响应新增正式合同标识、正式 release/request ID；旧的
  写作 Release ID 仍在写作域响应中保留。
- 未修改数据库 schema；正式 ID 映射由稳定 UUIDv5 规则重算，重启后不变。

## 4. 测试结果

- 制作域：257 passed
- 写作域：58 passed
- 集成域：5 passed
- `git diff --check`：通过

集成测试覆盖首次正式提交、ArtifactStore 冻结副本反读、`ProductionRequest/1.1`
合同校验、重复 handoff 幂等和 canonical `ProductionRun` 关联。

## 5. 风险与产品决定

- 初始 `AssetManifest/1.0` 为空白白名单；素材选择仍需后续制作侧显式选择并生成
  manifest successor，未绕过 B2 白名单。
- 没有写作 WorkCanon 修订的旧工作区会得到稳定的边界 `canon-snapshot` UUID；它
  不是伪造的正文 Revision，正式源仍由场景 Revision 列表固定。后续正式 UUID
  迁移时应将该投影替换为真实 Canon Revision 映射。
- `ApiError/1.0` 仍仅在显式 Accept 协商时返回。
- AA 继续 local-only；本次没有读取、上传或提交 AA 源码、数据库、素材或安装目录。

## 6. 下一阶段建议

在进入 B6 前，产品负责人需要决定正式 ID 映射何时从 UUID 投影迁移为写作域原生
canonical UUID，并确定写作 UI 如何在 handoff 前选择初始 `AssetManifest`。
