# SLBH Tool Integration Audit

## 当前关系

```text
SLBH Tool / import_bridge
  -> detect Scene.slbh_bridge
  -> detect bpy.ops.slbh.import_revit_bridge
  -> call independent slbh_revit_bridge add-on
```

`import_bridge` 目前只是旧 Bridge 的调用适配器。没有复制 importer service，也没有共享 module lifecycle、配置、版本或 diagnostics。

## 两个目标选项

| 评估项 | Option A：`revit_bridge` 一级模块 | Option B：`import_bridge/adapters/revit` |
|---|---|---|
| 初次导入 | 适合 | 适合 |
| SourcePackage 生命周期 | 清晰 | 容易被 import 语义限制 |
| Metadata/Coordinate | 可拥有专属 service | 易与通用 importer 混杂 |
| Update/ChangeSet | 适合 | 边界不自然 |
| Rebind/Animation | 适合 | 容易形成反向依赖 |
| 独立模块版本 | 适合 | 需要挂在 import_bridge 版本下 |
| UI 与状态 | 可独立管理 | 容易挤入通用导入页 |
| 日后维护 | 职责明确 | 短期文件少，长期耦合高 |

## 推荐

推荐 Option A：`revit_bridge` 成为 SLBH Tool 一级功能模块。

`import_bridge` 仍保留为通用格式入口，但只负责识别 `.slbh`/`project.json` 并调用 `revit_bridge.services.import_package()`。它不拥有 SourceRevision、mapping、ChangeSet 或 Rebind。

建议边界：

```text
slbh_toolbox/modules/revit_bridge/
  module_info.py
  contract/
  compatibility/
  services/
  source_layer/
  presentation/
  diagnostics/
  ui.py
  operators.py

slbh_toolbox/modules/import_bridge/
  adapters/revit.py   # thin dispatch adapter only
```

## 更新体系

当前 Tool 有 `SLBH_MODULE_INFO`，但只描述整个 toolbox；`core/registry.py` 主要是页签和 Legacy 列表，不是完整模块更新 registry。

目标应增加本地可查询的模块描述，不新增在线 updater：

```text
module_id: revit_bridge
display_name: SLBH Revit Bridge
module_version: x.y.z
schema_min/schema_max
core_min_version
status: installed / incompatible / disabled
```

发布遵循现有原则：Core 稳定、模块可独立替换、阶段性整合包。Revit Add-in 仍独立安装，只共享 Contract 版本范围。

## 配置与状态边界

- 用户偏好：SLBH Tool Preferences / module preferences。
- 项目映射：保存在 `.blend` 的稳定 SourcePackage/Mapping 数据，不放在插件目录。
- 临时 cache：单独 cache 目录，可删除重建。
- Legacy compatibility：独立 adapter，不污染新 contract。
- 插件更新不得覆盖 `.blend` 内 SourceOccurrence 与 Animation binding。

## Model Clean / Material 接口

当前 Model Clean 没有主动删除 `slbh.*` custom properties；BIM ghost 删除已有 property 保护。主要风险是：

1. 重命名对象会破坏任何 Object.name 绑定。
2. downstream 代码用对象名集合重新定位对象。
3. material 操作会替换/清空材质槽，但不应改变 source identity。

目标接口要求：

- downstream 通过 `source_occurrence_id` 查询，不通过 Object.name。
- 重命名仅改变显示名。
- Join/Proxy 只能发生在 Presentation Layer。
- Source Layer 的 identity properties 只由 Revit Bridge identity service 管理。

## Roadmap Gate

SLBH Tool 项目内工作流说明当前批准阶段仍在 `materials`。正式执行 Bridge 一级模块集成前，需要用户明确更新 roadmap；本 Task 只完成设计，不越级修改。
