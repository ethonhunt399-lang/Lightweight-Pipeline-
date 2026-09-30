# Animation Readiness Audit

## 范围说明

未在 `G:\SLBH_DEV` 当前源码中定位到 Animation Studio 的实现或正式 Contract，因此对 Studio 内部对象模型的结论为 UNKNOWN。本报告只评估 Bridge 提供给未来 Animation Studio 的准备度。

## Readiness Matrix

| 能力 | 当前 | Animation 要求 | Gap |
|---|---|---|---|
| 构件身份 | UniqueId + Legacy stable key | 跨 revision 的 occurrence ID | 缺 project/scope namespace 与持久映射 |
| Link 身份 | link ElementId/name/transform | Link Instance UniqueId + nested link path | 当前整数 ID 可重建，嵌套链接跳过 |
| SourceRevision | 无 | revision lineage | 完全缺失 |
| Definition | prototype 近似定义 | 独立 Definition registry | 缺正式抽象 |
| Occurrence | 每构件 Blender Object | 与 Object 解耦的 SourceOccurrence | 缺独立存储 |
| 坐标 | OBJ + transform | CoordinateContract + fingerprint | 缺失 |
| Anchor | 无 | 可版本化 floating anchor | 缺失 |
| ChangeSet | 无 | NEW/CHANGED/... | 缺失 |
| Staging | 无 | preview/confirm/commit | 缺失 |
| Rebind | 无 | Actor -> SourceOccurrence | 缺失 |
| Legacy Migration | importer warning | compatibility adapter | 只有读取，无迁移模型 |
| Transaction Recovery | 无 | staging snapshot/rollback | 缺失 |

## 当前可复用基础

1. Revit UniqueId、source model、link instance、category/family/type/level/system 已导出。
2. prototype 与 occurrence transform 已初步分离。
3. Blender 已有 Source Layer 和 Presentation Proxy Layer。
4. 多次导入会创建独立 root，不会直接覆盖旧对象。
5. 代理生成不删除 Source Layer。

## 关键风险

### Object 不是永久身份

当前选择/整理/Model Clean 多处使用 Object.name 定位。对象可重命名、删除、复制；Animation Actor 不得直接保存 Object.name 或 pointer。

### ElementId 不是全球身份

ElementId 只在一个 Document 当前生命周期内有意义；宿主和链接可重复，链接实例重建也会改变整数 ID。

### 当前 stable key 仍是 evidence

Host key 缺 project namespace；linked key 依赖路径 hash 和 link ElementId。可以用于 Legacy 匹配候选，不能自动承诺永久映射。

### Coordinate 改变

当前 importer 没有 update，只会新增 root。未来若直接按 stable key 覆盖对象而忽略 coordinate fingerprint，Shared Coordinate、Project Location 或 anchor 改变会让动画对象整体跳位。

### Deleted 语义

当前没有删除比较逻辑，这是好事。未来不能把“本次包中未出现”直接视为 Deleted；当前视图、隐藏、filter、workset、phase、link display 和 package scope 都会造成缺席。

## 推荐更新流程

```text
Read new SourcePackage
  -> validate Schema and CoordinateContract
  -> resolve SourceRevision lineage
  -> compute ChangeSet
  -> classify ambiguous/semantic proposals
  -> preview in Staging
  -> user confirmation
  -> atomic Commit
  -> Rebind actors to SourceOccurrence
  -> retain rollback snapshot
```

## Source / Presentation / Animation 分层

- Source Layer：完整 occurrence、identity、metadata；禁止 Join 破坏身份。
- Presentation Layer：合并网格、低模、Geometry Nodes；可重建。
- Animation Layer：Actor、关键帧、工序、状态；引用 occurrence ID，不拥有 BIM 几何。

代理对象只可保存 source query/rule 和成员映射，不作为 Animation 的唯一 source of truth。

## Rebind 规则

1. Exact occurrence ID：自动保持绑定。
2. Strong identity evidence：可自动提议，需记录证据。
3. Geometry/semantic similarity：只能产生 `SEMANTIC_PROPOSAL`。
4. 多个候选：`AMBIGUOUS`，人工选择。
5. 无候选且 tombstone confirmed：标记 source deleted，但不自动删除 Animation Actor。

## 当前准备度结论

当前 Bridge 是有效的一次性 BIM importer 和展示代理工具，但不是 revision-aware bridge。距离 Animation Studio 的核心缺口不是继续增加几何格式，而是 SourcePackage、Occurrence、CoordinateContract、ChangeSet、Staging 和 Rebind 六层状态模型。
