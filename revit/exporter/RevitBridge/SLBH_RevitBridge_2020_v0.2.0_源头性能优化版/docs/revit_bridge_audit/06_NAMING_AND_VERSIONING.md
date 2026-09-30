# Naming and Versioning Audit

## 推荐正式命名

- 产品/Contract：`SLBH Revit Bridge`
- Revit：`SLBH Revit Bridge Add-in`
- Blender：`SLBH Tool > Revit Bridge` module
- 独立 Legacy package：`slbh_revit_bridge`，仅兼容期保留
- Tool module ID：`revit_bridge`
- Revit namespace：`SLBH.RevitBridge`（已符合）
- Ribbon：`SLBH > Revit Bridge`

## Rename Matrix

| Current | Target | 必须修改 | 风险 | 迁移方式 |
|---|---|---|---|---|
| Add-on 显示名 `SLBH Revit桥接` | Module `SLBH Revit Bridge` | YES | 低 | 集成 Tool 时改显示层 |
| package `slbh_revit_bridge` | `slbh_toolbox.modules.revit_bridge` | YES（正式态） | 高 | Legacy package 保留一版 adapter |
| Tool `import_bridge` owning JSON | `revit_bridge` owning lifecycle | YES | 中 | import_bridge 仅做 dispatch |
| N Tab `SLBH` | Tool 统一 `SLBH 工具箱` | YES | 中 | 移除独立 Panel，复用 Tool root |
| operators `slbh.*` | `slbh_toolbox.revit_bridge_*` | YES | 高 | 旧 operator 代理转发并警告 |
| Scene `slbh_bridge` | `slbh_toolbox_revit_bridge` 或 module state | YES | 中 | 读取旧值后迁移 |
| classes `SLBHBridge*` / `SLBH_*` | `SLBH_REVTBRIDGE_*` 或 Tool 规范 | 建议 | 低 | 分阶段，不影响数据 |
| custom props `slbh.*` | 新 identity model + compatibility keys | 部分 | 高 | 旧 keys 作为 evidence 只读 |
| Panel `SLBH_PT_revit_bridge` | Tool module UI | YES | 中 | Legacy Panel 兼容期可隐藏 |
| Revit namespace `SLBH.RevitBridge` | 相同 | NO | 低 | 保持 |
| Ribbon `SLBH工具 > Blender桥接` | `SLBH > Revit Bridge` | 建议 | 中 | 避免重复 tab/panel |
| schema `0.3.6` | 独立 Contract version | YES | 高 | compatibility adapter |

Blender Operator ID 只能包含一个 namespace 分隔点，因此推荐 `slbh_toolbox.revit_bridge_import`，不使用多级点号。

## 当前名称冲突程度

### 已存在的直接并存

- `slbh_revit_bridge` 独立 package。
- `slbh_toolbox` package。
- Tool 的 legacy registry 已列出 `slbh_revit_bridge`。
- Tool 通过 `bpy.ops.slbh.*` 调用旧插件。

### 当前尚未发生的冲突

- 两插件可在 Blender 4.5 同时注册/注销。
- AddonPreferences ID 分别是 package 名，不相同。
- Scene Property 分别为 `slbh_bridge` 与 `slbh_toolbox_*`，不相同。

### 长期风险

`slbh.*` 过于宽泛，未来任何旧 SLBH add-on 都可能注册同名 Operator；独立 N Tab 也会造成用户把 Bridge 与主 Tool 误认为两个平级产品。

## 版本分层规则

目标必须独立维护：

1. Revit Bridge App Version：Revit DLL/UI/installer 的发布版本。
2. Blender Revit Bridge Module Version：Tool module 的实现版本。
3. Bridge Schema Version：跨端数据 Contract。

兼容声明使用范围而不是强制同号：

```text
Revit App 0.x.y
  writes schema >= 0.1, <= 0.1

Blender Module 0.x.z
  reads schema >= 0.1, <= 0.1
```

`min_importer_version` 可保留为迁移期字段，正式 Contract 应改为 module/schema compatibility range。

## Legacy 规则

- `schema_version 0.1-0.3.x` 归类 Legacy Bridge Package。
- `slbh.*` 自定义属性归类 Legacy Identity Evidence。
- 兼容读取不意味着继续用旧字段作为新主键。
- Legacy operator/package 至少保留一个迁移周期，并明确 deprecation 日志。
