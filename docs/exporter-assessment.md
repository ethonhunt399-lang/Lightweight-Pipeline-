# 现有导出工具评估（SLBH RevitBridge v0.3.7）

2026-09-30 · 评估对象：`revit/exporter/RevitBridge/SLBH_RevitBridge_2020_v0.2.0_源头性能优化版`（目录名是历史名称，实际版本 0.3.7）

## 1. 工具现状

- Revit 2019 / 2020 插件，C#，无 `.csproj`，由 `build_revit.ps1` 调用 .NET Framework `csc.exe` 编译，依赖 `../lib/Revit2020` 下的 RevitAPI.dll（未入库，正确）。
- 入口：`App.cs` 建 Ribbon“SLBH工具 > Blender桥接”；`ExportCommand.cs` 选择预设、链接选项、族规则、输出目录；`ObjExportContext.cs` 实现 `IExportContext`，遍历当前三维视图。
- 输出 `<name>.slbh/` 目录：`project.json`（构件属性、系统、材质、链接、原型）、`model.obj`（唯一网格）、`prototypes.obj`（重复族原型网格）。单位米，Revit 内部坐标。
- Blender 端（`Source/Blender`）与本项目无关，不需要改动。

## 2. 可直接复用的部分

| 能力 | 位置 | 对本项目的价值 |
| --- | --- | --- |
| 视图驱动的导出范围（剖面框、隐藏、过滤器） | `ExportCommand.Execute`、`CustomExporter` | 用剖面框框选走廊区域即可导出，范围控制现成 |
| 链接模型遍历、链接变换、未加载链接报告 | `OnLinkBegin/End`、`CreateLinkContext` | 结构、建筑链接模型的梁板可以直接作为障碍物 |
| 构件标识：UniqueId + 链接实例组成的稳定键 | `BuildStableElementKey` | 可直接作为方案与回写的构件主键 |
| 系统信息（名称、类型、分类、缩写，多级回退） | `GetSystemInfo`、`ClassifySystemCode` | 系统映射的原始数据齐全；分类规则由 Python 侧按项目标准重做 |
| 直圆管、圆风管参数化：起终点、外径、坡度 | `TryPopulateStraightRoundCurve` | 圆管的求解参数已具备一半 |
| 楼层、工作集、专业猜测、族与类型 | `CreateElementRecord` | 直接沿用 |
| 三角网格（梁、板、柱、管件、设备） | `OnPolymesh`、OBJ 输出 | 作为碰撞检测的障碍物与管件外形；HTML 查看器可直接显示 |
| 导出失败标记 `EXPORT_FAILED.txt` | `ExportCommand` | 沿用 |

## 3. 缺口（按管综需要）

| # | 缺口 | 现状 | 需要补的内容 |
| --- | --- | --- | --- |
| G1 | 矩形风管、桥架、线管无参数 | `TryPopulateStraightRoundCurve` 对非圆截面返回 false，只有网格 | 宽、高、截面朝向（端部连接件坐标系的 X/Y 轴），桥架与线管同样处理 |
| G2 | 保温层 | 未读取 | 通过 `InsulationLiningBase.GetInsulationIds` 取保温厚度（管道、风管），风管内衬厚度 |
| G3 | 连接关系（拓扑） | 仅 `has_mep_connector` 布尔值 | 每个连接件的位置、方向、尺寸、形状、所连构件的 UniqueId |
| G4 | 管件、附件语义 | 只有网格 | 管件类型（弯头、三通、四通、变径等 `PartType`）、角度、所属系统 |
| G5 | 剖面框裁切 | 跨剖面框的圆管退回为裁切后的网格 | 管综需要完整参数，并标记“跨越范围边界” |
| G6 | 坐标体系 | 只有内部坐标与链接变换 | 项目基点、测量点、`ActiveProjectLocation` 变换，写入 manifest |
| G7 | 轴网、标高、房间 | 未导出（CustomExporter 不输出轴网） | 用 `FilteredElementCollector` 单独收集轴网线、标高高程、房间名称与边界 |
| G8 | 构件基线指纹 | 无 | 按参数计算的指纹（端点、尺寸、偏移、系统），供回写前 expect 校验 |
| G9 | 标高偏移、参照标高 | 部分 | MEP 曲线的参照标高、起终点偏移，便于回写时按 Revit 语义设置 |
| G10 | 诊断信息 | 大量 `catch { }` 静默吞掉异常 | 按构件记录读取失败的字段，写入诊断列表 |
| G11 | 已有支吊架识别 | 仅在族名中匹配 hanger/支吊架 | 输出类别、族、类型，由 Python 侧判断，便于体检报告列出 |

## 4. 改造方案

**原则：不改动现有 Blender 导出行为和 `project.json` 结构。**

1. 在同一插件内新增 Ribbon 按钮“导出管综数据”，复用现有的遍历、链接、标识、系统代码。
2. 遍历时对 `MEPCurve`（管道、风管、桥架、线管）和带连接件的 `FamilyInstance`（管件、附件、设备）额外收集参数，写入包内新文件 `mep.json`（G1–G5、G8–G11）。
3. 导出结束后单独收集坐标体系、轴网、标高、房间，写入 `manifest.json`（G6、G7），包含 schema 版本和导出参数。
4. 网格继续输出到 `model.obj` / `prototypes.obj`，作为障碍物与显示几何。管综导出时关闭原型实例化，简化 Python 侧读取。
5. Python 工具箱读取 `manifest.json` + `mep.json` + OBJ，转换为毫米、项目坐标，生成体检报告。

预计新增 C# 代码 600–900 行，集中在新文件 `MepDataCollector.cs`、`MepModels.cs`、`MepExportCommand.cs`，对现有文件只做挂接点的少量改动。

## 5. 验证方式

开发环境无法运行 Revit，按以下方式闭环：

1. 开发代理编写代码并提交。
2. 工程师在本机执行 `build_revit.ps1 -Year 2020` 编译、安装，在样例模型中用剖面框框选一段走廊导出。
3. 导出包压缩后按 GitHub Release 方式上传；开发代理用 Python 读取并出体检报告，逐项核对字段。
4. 由工程师用 Revit 抽查若干构件的尺寸、保温、连接关系，与体检报告对照。

## 6. 待确认

- 改造后的导出工具以本仓库为唯一源码，还是改动合并回 `G:\SLBH_DEV` 的原工作区？建议以本仓库 `revit/exporter/` 为准，避免两份源码分叉。
