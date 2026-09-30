# Target Architecture

## 总体架构

```text
                         SLBH BRIDGE CONTRACT
                                  |
                 +----------------+----------------+
                 |                                 |
       Revit Bridge Add-in                    SLBH Tool
                 |                                 |
       Revit 2020 Adapter                   Revit Bridge Module
                 |                                 |
              RVT/View                        SourcePackage Store
                 |                                 |
     Geometry/Metadata/Coordinate             Source Layer
                                                   |
                                              Model Clean
                                                   |
                                               Materials
                                                   |
                                         Presentation Layer
                                                   |
                                          Animation Adapter
                                                   |
                                            Animation Studio
```

## 层级责任

### Revit Bridge Add-in

- 保持独立安装。
- 读取 Revit View、Document、Link、geometry、metadata、coordinate evidence。
- 不修改源 RVT，不移动构件。
- 生成符合 Contract 的 SourcePackage。
- Revit API 版本差异留在 adapter 层。

### Bridge Contract

- 只定义数据，不共享 Revit/Blender 运行时代码。
- 分离 Package、Revision、Definition、Occurrence、Material、Geometry 和 Coordinate。
- 定义版本范围、checksum、diagnostics 和 legacy adapter。

### SLBH Tool Revit Bridge Module

- 拥有 package import、source mapping、revision、changeset、staging 和 rebind。
- 向 `import_bridge` 暴露薄适配入口。
- 向 Model Clean/Material 暴露稳定 Source Layer contract。
- 管理 presentation proxy，不修改 source identity。

### Model Clean / Material

- 只处理允许的显示名、几何显示和材质。
- 不删除或重写 occurrence identity。
- destructive action 必须显式、可预览、可回滚。

### Presentation Layer

- 合并、低模、Geometry Nodes、system proxy。
- 可随时由 Source Layer 重建。
- 不作为永久 BIM/Animation 身份。

### Animation Adapter

- Actor 绑定 SourceOccurrence。
- 消费 ChangeSet 和人工确认结果。
- 不直接依赖 Revit ElementId、Object.name 或 Collection.name。

## 多 Revit 版本结构

当前源码几乎全部直接依赖 Revit API，只有一个很小的 compat helper。目标拆分：

```text
RevitBridge.App
  Contract DTO / serialization core
  Export orchestration core
  Geometry/identity rules core
  Adapters/Revit2020
    API document/view/link/material access
    CustomExporter compatibility
```

本阶段只恢复并稳定 Revit 2020。不要在 Contract 稳定前扩展其他年份；2019 Legacy build 继续保留。

## 状态存储

- Package：外部只读输入。
- SourcePackage index：`.blend` 内项目状态或明确 sidecar，不放插件目录。
- Occurrence mapping：稳定 ID 表，随 `.blend` 保存。
- Presentation cache：可删除重建。
- Diagnostics：package/import/rebind transaction ID 可关联。
- User preferences：Tool Preferences，不混入项目映射。

## Diagnostics 层

建议统一事件：

- Revit.Export.Start/Complete/Failed
- Package.Validate
- Coordinate.Resolve
- Import.Stage/Commit/Rollback
- Mapping.Match/Ambiguous
- ChangeSet.Build
- Rebind.Propose/Commit

每条记录至少包含 package ID、revision ID、scope ID、数量、耗时、warning/error code。TaskDialog/Operator report 只显示摘要，完整诊断写结构化报告。

## 不采用的架构

- 不把 Revit DLL 放进 Blender Add-on。
- 不让 `import_bridge` 拥有全部 lifecycle。
- 不继续维护第二套独立 Blender updater。
- 不用移动 Revit 构件解决 Blender origin。
- 不用 Join Source Objects 作为默认优化。
- 不直接覆盖旧 Blender Objects 完成 update。
