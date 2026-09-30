# Migration Plan

原则：小步迁移；每阶段可独立测试、可回滚；Legacy package 和旧 `.blend` 不一次性删除。

## Phase 0：恢复 Revit 2020 可加载性

目标：恢复 manifest，确认 Ribbon 和一次真实导出。

测试：Install/Repair/Check、Revit journal、Ribbon、最小 host/link 模型导出。

退出条件：2020 能稳定加载；安装路径和版本可查询。不得同时改 Blender 架构。

## Phase 1：命名与版本边界

目标：确定正式产品名、module ID、operator/property migration 和三层版本规则。

测试：Legacy 与新命名并存；无 duplicate registration；旧 `.blend` 可打开。

退出条件：Rename Matrix 获批。不得直接批量替换 `slbh.*`。

## Phase 2：Bridge 纳入 SLBH Tool

目标：建立一级 `revit_bridge` module，将现有 importer 迁为 service；`import_bridge` 只做 dispatch。

测试：Tool 单独启用即可导入 Legacy 0.3.6 包；disable/enable/reload；旧独立插件 compatibility 路径仍可用。

退出条件：不再要求两个 Add-on 同时启用。该阶段需先获得 SLBH Tool roadmap 更新授权。

## Phase 3：Bridge Contract 0.1

目标：实现新 Package/Definition/Occurrence/Coordinate DTO 与 legacy reader。

测试：schema validation、checksum、旧包迁移、未知字段兼容、完全不兼容时停止。

退出条件：Revit 与 Blender 对固定 fixtures 得到一致 Contract。

## Phase 4：Identity / Occurrence

目标：引入 project/scope/source document/link occurrence 的稳定身份和 mapping store。

测试：宿主+链接、多次放置、Save As、link instance recreate、Object rename 后仍可定位。

退出条件：下游不依赖 Object.name 或单独 ElementId。

## Phase 5：Coordinate Contract

目标：显式记录 Internal/Shared/Project、link path、origin 和 floating anchor。

测试：共享坐标、旋转/镜像链接、anchor 改变、跨包同基准导入。

退出条件：coordinate fingerprint 改变会阻止静默复用旧 world matrices。

## Phase 6：Update / ChangeSet

目标：实现 revision compare、NEW/CHANGED/UNCHANGED/ABSENT/AMBIGUOUS、staging 和 rollback。

测试：视图过滤变化不能误判 Deleted；失败 commit 自动恢复；重复导入幂等。

退出条件：所有变更先 preview，再 commit。

## Phase 7：Animation Rebind / Legacy Migration

目标：Actor 绑定 occurrence；支持 exact rebind、ambiguous 和 semantic proposal；迁移旧属性。

测试：对象重命名、source geometry 更新、source 删除、链接变化后动画仍可审查恢复。

退出条件：不自动继承不确定映射；Legacy compatibility 有明确 sunset policy。

## 保留内容

- CustomExporter 当前视图导出。
- 宿主/链接文档上下文与累计 transform。
- Revit metadata/system/material 读取规则。
- prototype + occurrence、参数化规则构件。
- Blender Source / Presentation Layer 分离。
- 旧包版本提示和兼容读取。

## 候选废弃内容

- 独立顶级 Blender Panel 与独立长期更新体系。
- 通用 `slbh.*` Operator namespace。
- Object.name/Collection.name 作为映射键。
- path hash 冒充 Document GUID。
- Link ElementId 作为稳定 link identity。
- App/Module/Schema 同号。
- 不可逆低模替换作为默认 Source 优化。

当前 Revit 源码未发现 MoveElement、ElementTransformUtils.MoveElement、批量复制/成组移动或修改源模型位置；无需恢复这类历史逻辑。
