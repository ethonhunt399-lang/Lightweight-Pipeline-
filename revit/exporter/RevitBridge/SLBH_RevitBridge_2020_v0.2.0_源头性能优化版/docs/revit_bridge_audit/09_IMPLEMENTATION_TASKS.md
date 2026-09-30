# Implementation Tasks

## Task 1：恢复 Revit 2020 Add-in

目标：恢复 manifest 并确认完整加载链。

修改范围：安装/修复脚本、manifest 生成与 check；必要时仅修复启动诊断。

禁止范围：不改 schema、不改 Blender、不做架构迁移。

测试：manifest/DLL/hash 检查；Revit 2020 启动；Ribbon；journal 无加载错误；最小导出。

完成条件：Install、Repair、Check 可重复执行，Revit 真实可见。

## Task 2：部署四态与 Diagnostics

目标：定义 Install/Repair/Upgrade/Uninstall 和结构化安装记录。

修改范围：Revit installer tooling、诊断日志。

禁止范围：不引入在线 updater。

测试：manifest 缺失、DLL 缺失、版本旧、哈希错、Revit 运行中等场景。

完成条件：每种状态有确定返回码和用户可读路径。

## Task 3：Blender Bridge 正式模块骨架

目标：在 SLBH Tool 建一级 `revit_bridge` module，并迁入 importer service。

修改范围：Tool module、registry、thin import adapter、Legacy operator forwarding。

禁止范围：不删除旧 add-on，不同时实现 ChangeSet。

测试：Blender 4.5 register/unregister/reload；Tool 单独导入 0.3.6 fixture；双插件兼容。

完成条件：正式路径不再依赖 `Scene.slbh_bridge` 或 `bpy.ops.slbh.*`。

## Task 4：SourcePackage Contract 0.1

目标：实现 Package、Revision、Definition、Occurrence、Material、Geometry DTO 和 validation。

修改范围：两端 Contract 与 fixture；legacy adapter。

禁止范围：不实现增量覆盖。

测试：round-trip、schema range、checksum、unknown field、legacy migration。

完成条件：Revit/Blender 对同一 fixture 解释一致。

## Task 5：Identity Layer

目标：建立 project/scope/document/link/occurrence identity 与 evidence confidence。

修改范围：export metadata、Blender mapping store、identity API。

禁止范围：不自动进行 semantic rebind。

测试：宿主/链接 ID 冲突、同一链接多次放置、Object rename、Save As。

完成条件：查询不依赖 Object.name 和单独 ElementId。

## Task 6：Coordinate Contract

目标：表达 Internal/Shared/Project、origin、anchor、link path 与 fingerprint。

修改范围：Revit coordinate adapter、Contract、Blender coordinate resolver。

禁止范围：不得移动源 Revit 构件。

测试：共享坐标、旋转/镜像链接、section box、anchor 变化和跨包对齐。

完成条件：坐标变化可检测、可预览，不静默错位。

## Task 7：ChangeSet Preview

目标：revision compare、staging、confirm、commit、rollback。

修改范围：Blender module state 与 transaction service。

禁止范围：未导出不得直接等于 Deleted。

测试：view/filter/scope 变化、故障注入、重复 commit、rollback。

完成条件：变更分类可解释，失败不破坏旧 Source Layer。

## Task 8：Animation Rebind Adapter

目标：Animation Actor 绑定 SourceOccurrence，并处理 exact/ambiguous/proposal。

修改范围：Bridge 到 Animation Studio 的 adapter Contract。

禁止范围：不让 geometry similarity 自动继承动画。

测试：构件修改、删除、重建、链接替换、多个候选和用户拒绝 proposal。

完成条件：关键帧/工序不会因对象重命名或重新导入静默丢失。

## 下一项建议

下一项真正应执行 Task 1。当前故障在 `.addin` 注册层，先恢复 Revit 2020 的可加载性并取得真实启动证据，再开始命名或 Blender 集成。这样可把部署故障与架构迁移分开验证。
