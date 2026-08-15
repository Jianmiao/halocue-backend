# HaloCue 1.0 Writing

独立的 HaloCue 写作域纵切。它持有作品、创意简报、故事方向、章节、场景、正文修订、候选方案与不可变的剧本发布版本；只在发布后通过 HTTP 把固定文本交给制作后端。

## 运行

```powershell
cd 09-HaloCue-1.0-Writing
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m halocue_writing.server --port 8899
```

打开 `http://127.0.0.1:8899/`。默认数据目录为 `./data`，可通过 `HALOCUE_WRITING_DATA_DIR` 修改。制作后端地址默认为 `http://127.0.0.1:8892`，可通过 `HALOCUE_PRODUCTION_URL` 修改。

当前模型 Provider 是明确标记的 `fake`：它用于验证可替换模型边界与完整审查链，不声称执行了真实模型调用。正式 BA 写作步骤缺少已校准运行时人物卡时会在上下文中报告未就绪。

## 正文修订

场景正文使用 `scene-blocks/1.0`：每个动作或对白块都有稳定 ID。工作台手工保存调用：

```text
POST /api/v1/works/{work_id}/scenes/{scene_id}/manuscript
```

请求必须携带 `expected_version`、`expected_base_revision_id` 与完整 `blocks` 列表。保存总是创建新的不可变 Revision；作品版本或基准正文已变化时会返回冲突。用户手工保存不会调用 Agent，并会替代基于旧正文的待决定 Proposal。`ScriptRelease` 仍导出每个 Revision 的规范纯文本 `text`，制作后端无需理解 SceneBlock。

## 测试

```powershell
python -m pytest -q
```
