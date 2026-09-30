# Blender Bridge Audit

## 当前真实身份

| 项 | 当前值 |
|---|---|
| Add-on 显示名 | `SLBH Revit桥接` |
| Python package | `slbh_revit_bridge` |
| 版本 | `0.3.6` |
| Blender 目标 | `4.5.0+` |
| N Panel 类别 | `SLBH` |
| Operator namespace | `slbh.*` |
| Scene Property | `Scene.slbh_bridge` |
| AddonPreferences ID | `__name__`，安装时为 `slbh_revit_bridge` |

因此，“插件本体只叫 SLBH”不完全符合当前源码：产品显示名和 package 已经是 Revit Bridge；真正仍过于宽泛的是 N Panel 类别、Operator prefix、class/property 命名和大量 `slbh.*` custom property。

## 当前结构

```text
slbh_revit_bridge
  __init__.py        registration / UI / operators
  importer.py        package + OBJ parser / object creation
  semantics.py       discipline/system enrichment
  materials.py       material resolver and color modes
  organizer.py       collection hierarchy
  selection.py       BIM property selection
  optimization.py    mesh sharing / merge proxy / legacy proxy replacement
  display_proxy.py   Source and Presentation layers
```

## 导入行为

1. 读取 `project.json` 并检查 `schema_version` / `min_importer_version`。
2. 解析 `model.obj` 和 `prototypes.obj`。
3. 每个 source occurrence 创建独立 Blender Object。
4. prototype occurrence 可共享同一 Mesh datablock。
5. 参数化圆管/圆柱在 Blender 中重建低边数圆柱。
6. 每次导入创建独立 root collection，避免同一 Revit 文件的多个包互相覆盖。
7. 对象进入 `00_BIM源模型`；整体代理进入 `10_整体展示代理`。

## Metadata 和 Identity

对象保存 `element_id`、`unique_id`、`stable_element_key`、source document、source model、link instance、family/type/level/system/material 等 IDProperties。

当前 `stable_element_key` 是有价值的 Identity Evidence，但不是 Animation 永久 ID：

- Host key 为 `HOST|UniqueId`，缺少 project/package namespace。
- Linked key 依赖 `source_model_key + link_instance_id + UniqueId`。
- `source_document_guid` 实际是中心路径/文件路径/标题的 SHA256 截断，不是 Revit Document GUID。
- Link instance 只保存整数 ElementId，没有 Link Instance UniqueId。
- Save As、路径变化、重建链接实例都可能改变当前身份链。

## Coordinate

- 唯一网格以 Revit exporter 给出的宿主坐标写入 OBJ，单位转为米。
- prototype occurrence 使用 4x4 transform。
- linked MEP/round primitives 在 Revit 端将 Link Transform 应用于端点。
- 没有 Internal/Shared/Project coordinate system、export origin 或 floating anchor 声明。
- Blender root 没有可版本化的 CoordinateContract。

链接参数化构件还有一个静态风险：剖面框判断在应用 Link Transform 之前用 link-local 端点与 host section box 比较，可能错误回退或错误参数化。链接显示色也把 link ElementId 传给 host view 的 element override 查询，可能与宿主同值 ElementId 混淆。

## Source Layer 与代理

这是当前最应保留的设计：

- `00_BIM源模型` 保留每个 BIM occurrence 和完整 custom properties。
- `10_整体展示代理` 合并显示几何，不删除源对象。
- Level/type merge proxy 保存成员 ElementId、UniqueId 和 stable key 数组。
- Overall proxy 只保存抽样 stable keys，不承担逐面/逐构件身份。

默认架构已经开始把 Source Layer 与 Presentation Layer 解耦，但尚未形成对 Animation Layer 的正式接口。

## Legacy/破坏性路径

- `replace_with_bbox_proxies` 是不可逆低模替换，虽然 Object 和 custom properties 仍在，但原 Mesh 被替换。
- Model Clean 的对象重命名不会删除 custom properties，但任何外部 Object.name 绑定会失效。
- 当前桥接自身没有用 Join 删除 source objects；合并代理是额外对象。
- 选择、整理和代理代码仍广泛以 `element_id` 是否存在判断 BIM object，缺少统一 Identity API。

## Blender 4.5 审查

自动检查结果：

- 40 个 Bridge/Tool Python 文件全部通过 AST 解析。
- Blender `4.5.10 LTS --factory-startup` 中，`slbh_revit_bridge` 与 `slbh_toolbox` 可同时 enable。
- 两者可按逆序 disable，均无 Traceback。
- 未发现 handlers、timers 或长期全局回调污染。

仍需真实 UI/场景验证：大型包导入、材质节点、代理重建、保存/重开以及复杂链接坐标。

## 与 SLBH Tool 的冲突

当前双插件可以技术上共存，但长期边界不成立：

1. Tool 的 JSON importer 运行时依赖旧 add-on 和 `bpy.ops.slbh.import_revit_bridge`。
2. Revit Bridge 自己注册 N Panel、Preferences、Scene Property 和版本。
3. Tool 又把 `slbh_revit_bridge` 标记为 Legacy module。
4. `slbh.*` Operator namespace 过于宽泛，未来与 SLBH Tool 其他模块冲突概率高。
5. 两套独立安装和更新来源无法形成一个 Module Registry 状态。

结论：当前 Add-on 可作为 Compatibility Runtime 保留，但不应继续作为正式顶级产品长期独立演化。
