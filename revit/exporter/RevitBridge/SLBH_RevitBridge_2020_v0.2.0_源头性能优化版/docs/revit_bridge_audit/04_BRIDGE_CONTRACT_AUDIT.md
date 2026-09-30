# Bridge Contract Audit

## 当前 Contract 评价

当前格式已经能表达一次性导入所需的 geometry、metadata、material、link transform 和版本，但 `project.json` 同时承担 manifest、业务数据和统计，且没有 Revision、Scope、Coordinate Contract 和永久 Occurrence 身份。

| 领域 | 当前 | 关键缺失 |
|---|---|---|
| Geometry | `model.obj`、`prototypes.obj` | content hash、geometry registry、明确坐标空间 |
| Metadata | `elements[]` | Definition/Occurrence 分离 |
| Coordinate | unit、link transform | internal/shared/project、origin、anchor、axis contract |
| Identity | UniqueId、path hash、link ElementId | package namespace、link UniqueId、persistent occurrence ID |
| Version | 多个 `0.3.6` | App/Module/Schema 分层 |
| Revision | 无 | source revision、scope、parent revision |
| Update | 无 | ChangeSet、staging、commit、rollback |

## 新 Contract 草案

草案阶段建议 `schema_version = 0.1.0`；正式替代 Legacy 时升为 `1.0.0`。

```json
{
  "schema_name": "slbh.bridge",
  "schema_version": "0.1.0",
  "package": {
    "package_id": "uuid",
    "package_version": 1,
    "created_at": "ISO-8601",
    "project_key": "stable-project-key",
    "scope_id": "stable-export-scope-key"
  },
  "source_revision": {
    "revision_id": "uuid-or-source-token",
    "parent_revision_id": null,
    "source_fingerprint": "sha256",
    "scope_fingerprint": "sha256",
    "is_full_source_inventory": false
  },
  "exporter_info": {
    "app_version": "x.y.z",
    "revit_version": "2020",
    "adapter_id": "revit-2020",
    "min_blender_module_version": "x.y.z"
  },
  "coordinate_contract": {},
  "definitions": [],
  "occurrences": [],
  "materials": [],
  "geometry": [],
  "diagnostics": {}
}
```

## Package / SourceRevision

Package 必须区分：

- `package_id`：这一个传输包。
- `project_key`：同一 Revit 项目生命周期。
- `scope_id`：当前视图/过滤/分包范围。
- `revision_id`：该 scope 的源修订。
- `parent_revision_id`：允许比较的上一修订。
- `is_full_source_inventory`：决定是否有资格产生 tombstone。

同一 Revit 文件不同视图导出不能互相判定 Deleted。

## Definition / Occurrence

Definition 表示可共享的几何/类型定义：

- `definition_id`
- category/family/type
- geometry references
- material slot contract
- definition fingerprint
- source definition evidence

Occurrence 表示每个源构件放置：

- `occurrence_id`
- `definition_id`
- `source_document_id`
- `source_unique_id`
- `link_path[]`
- `link_instance_unique_id`
- local/world/package transform
- level/system/discipline
- identity evidence 与 confidence

动画 Actor 绑定 `occurrence_id`，不绑定 Blender Object.name。

## IdentityEvidence

Identity Evidence 应保留当前有价值字段，但标记来源和可信度：

- Revit UniqueId
- ElementId（仅诊断/显示）
- central/cloud/model GUID（如果 API 能可靠取得）
- normalized path hash（fallback evidence）
- Link Instance UniqueId 与完整 link path
- source family/type/category
- geometry/material fingerprints

`source_model_key + link_instance_id + unique_id` 可作为 Legacy candidate key，但不能直接宣称永久全球 ID。

## CoordinateContract

至少包括：

- `unit_scale_to_meter`
- `axis_convention`、handedness
- `source_coordinate_system`: INTERNAL / SHARED / PROJECT
- `internal_to_shared`
- `internal_to_project`
- `source_to_package`
- `package_to_blender`
- `export_origin`
- `floating_origin_anchor`：ID、位置、revision
- 每层 link transform 与累计 transform
- coordinate fingerprint

如果新 revision 的 coordinate fingerprint 改变，Update 必须先计算 anchor migration；不能复用旧 `world_matrix`。

## Material / Geometry

Material 应使用稳定 `material_id` 和 content fingerprint，分开保存：

- Revit base material
- Revit view/display color
- SLBH semantic/system presentation

Geometry 记录：

- geometry ID 与 content hash
- source coordinate space
- vertex/face counts
- material slot order
- primitive/prototype/mesh kind
- file reference 与 checksum

不同 source 只有 content hash、slot contract 和变换兼容时才能共享 geometry。

## ChangeSet Contract

ChangeSet 状态：

- `NEW`
- `CHANGED`
- `UNCHANGED`
- `DELETED_CONFIRMED`
- `ABSENT_FROM_SCOPE`
- `AMBIGUOUS`
- `SEMANTIC_PROPOSAL`

`AMBIGUOUS` 和 `SEMANTIC_PROPOSAL` 必须进入人工确认队列，不自动继承动画、工序或关键帧。

## Legacy Compatibility

```text
Legacy project.json 0.1-0.3.x
  -> compatibility parser
  -> IdentityEvidence
  -> synthetic SourcePackage / SourceRevision
  -> no confirmed deletion unless evidence is sufficient
```

旧 `slbh.*` properties 继续读取为 evidence；新系统不能继续依赖其 Object.name 或 ElementId 作为主键。
