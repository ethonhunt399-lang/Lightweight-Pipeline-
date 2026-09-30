# SLBH Revit Bridge Current State Audit

审查日期：2026-08-09  
审查性质：只读审查；除本目录文档外未修改正式代码、插件、manifest、版本号或安装状态。

## 1. 实际路径

| 系统 | 实际路径 | 状态 |
|---|---|---|
| Revit Bridge 源码与发布物 | `G:\SLBH_DEV\RevitBridge\SLBH_RevitBridge_2020_v0.2.0_源头性能优化版` | 已定位 |
| Revit API 依赖 | `G:\SLBH_DEV\RevitBridge\lib\Revit2019`、`G:\SLBH_DEV\RevitBridge\lib\Revit2020` | 已定位 |
| Blender Bridge 安装目录 | `%APPDATA%\Blender Foundation\Blender\4.5\scripts\addons\slbh_revit_bridge` | 已安装，内容与源码一致 |
| SLBH Tool 源码 | `G:\SLBH_DEV\SLBH_Toolbox_v0_3_refactor\slbh_toolbox` | 已定位，Git 仓库 |
| SLBH Tool Blender 开发链接 | `%APPDATA%\Blender Foundation\Blender\4.5\scripts\addons\slbh_toolbox` | Junction 指向上述源码 |
| Animation Studio 当前代码/设计 | 未在 `G:\SLBH_DEV` 的非归档源码中定位到 | UNKNOWN |

目录名中的 `v0.2.0` 是历史名称，不代表当前正式版本。

## 2. 真实版本

| 组件 | 证据 | 真实版本 |
|---|---|---|
| Revit Add-in | `AssemblyInfo.cs`、DLL FileVersion、发布说明 | `0.3.6.0` / 产品版本 `0.3.6` |
| Blender Bridge | `bl_info`、`IMPORTER_VERSION`、安装目录文件哈希 | `0.3.6` |
| 当前 Bridge Schema | `BridgeProject.SchemaVersion` | `0.3.6` |
| SLBH Tool | `slbh_toolbox/__init__.py`、`bl_info.py` | `0.3.1` |
| Blender | 后台进程输出 | `4.5.10 LTS` |

当前 Revit App、Blender Module 和 Schema 共用 `0.3.6`，版本职责尚未分层。

## 3. 入口与主要模块

### Revit

- `Source/Revit/App.cs`：`IExternalApplication`，创建 `SLBH工具 > Blender桥接` Ribbon。
- `Source/Revit/ExportCommand.cs`：`IExternalCommand`，选择视图、精度、链接选项、族规则和输出目录。
- `Source/Revit/ObjExportContext.cs`：`IExportContext`，接收宿主、实例和链接节点，写 OBJ 与元数据。
- `Source/Revit/BridgeModels.cs`：当前 JSON DataContract。
- `Source/Revit/RevitApiCompat.cs`：2019/2020 的最小 API 分支。

### Blender Bridge

- `Source/Blender/__init__.py`：Add-on 注册、面板、Operator 与 Scene Property。
- `importer.py`：解析 `project.json`、`model.obj`、`prototypes.obj`，创建 Source Layer。
- `materials.py`、`semantics.py`、`organizer.py`、`selection.py`：材质、语义、集合和选择。
- `optimization.py`、`display_proxy.py`：共享 Mesh、合并代理、BIM Source / Presentation 分层。

### SLBH Tool

- `slbh_toolbox/__init__.py`：统一注册入口，版本 `0.3.1`。
- `modules/import_bridge`：通用导入分发；JSON 当前只调用旧 Bridge Operator。
- `modules/model_clean`、`modules/materials`、`modules/pipeline`：后处理链。
- `core/registry.py`：页签和 Legacy 清单；尚不是完整的独立模块安装/版本 registry。

## 4. Build、安装与运行

Revit 端没有 `.csproj`。`build_revit.ps1` 直接调用 64 位 .NET Framework 4 编译器：

`%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe`

同一套 C# 源码分别引用 `lib/Revit2019` 或 `lib/Revit2020`，并定义 `REVIT2019` / `REVIT2020`。未显式指定 PlatformTarget，编译器默认行为等同 AnyCPU；没有 NuGet 或第三方运行时 DLL。

安装采用 CMD 调用 PowerShell：

- DLL：`%LOCALAPPDATA%\SLBH\RevitBridge2020\SLBH.RevitBridge.2020.dll`
- manifest：`%APPDATA%\Autodesk\Revit\Addins\2020\SLBH.RevitBridge.2020.addin`

Blender Bridge 仍以独立 Add-on 文件夹安装；SLBH Tool 使用 Junction 直接读取 Git 工作区。

## 5. 依赖

| 依赖 | 版本/来源 | 用途 |
|---|---|---|
| RevitAPI.dll 2019 | `19.0.0.405` | Revit 2019 DB API |
| RevitAPIUI.dll 2019 | `19.0.0.405` | Revit 2019 UI API |
| RevitAPI.dll 2020 | `20.0.0.377` | Revit 2020 DB API |
| RevitAPIUI.dll 2020 | `20.0.0.377` | Revit 2020 UI API |
| System / System.Core | .NET Framework | 基础运行时 |
| System.Windows.Forms | .NET Framework | 导出 UI |
| System.Runtime.Serialization | .NET Framework | JSON |
| bpy / bmesh / mathutils | Blender 4.5 | Blender Runtime |

工程中的 Revit 2020 API DLL 与本机 Revit 2020 安装目录中的 DLL 版本、长度和 SHA256 完全一致。

## 6. 当前交换格式

当前 `.slbh` 实际是带 `.slbh` 后缀的目录，不是 ZIP。目录内容为：

```text
<name>.slbh/
  project.json
  model.obj
  prototypes.obj
```

- `model.obj`：唯一网格，顶点已从英尺换算为米。
- `prototypes.obj`：原型局部网格。
- `project.json`：项目、视图、构件、材质、链接、原型、性能和版本。
- 没有独立 `manifest.json`、纹理目录、SourceRevision 或 ChangeSet。

## 7. 真实数据流

```text
Active non-perspective View3D
  -> CustomExporter(host Document)
  -> ObjExportContext
     -> host/link document context
     -> model.obj + prototypes.obj + project.json
  -> Blender independent add-on
  -> one Blender Object per source occurrence
  -> 00_BIM source layer
  -> optional presentation proxies
```

SLBH Tool 当前数据流并非直接实现上述 importer，而是：

```text
SLBH Tool import_bridge
  -> detect legacy slbh_revit_bridge add-on
  -> bpy.ops.slbh.import_revit_bridge
  -> legacy importer runtime
```

## 8. 能力矩阵

| 能力 | 存在 | 实现位置 | 状态 |
|---|---|---|---|
| 当前 3D 视图可见几何 | YES | `CustomExporter` | 已实现 |
| Element 元数据/UniqueId | YES | `BridgeElement` | 已实现 |
| 链接 Document 上下文 | YES | `OnLinkBegin/End` | 已实现，嵌套链接明确跳过 |
| Link Instance Transform | YES | `LinkNode.GetTransform` | 已实现；缺少显式坐标 Contract |
| 未加载链接报告 | YES | `skipped_links` | 已实现 |
| 材质与显示色 | YES | Revit context + Blender resolver | 已实现；链接视图覆盖存在风险 |
| 原型 Mesh + occurrence | YES | OBJ prototype + transform | 已实现 |
| 参数化圆管/圆柱 | YES | `straight_round_curve` | 已实现，链接剖面框判断有坐标风险 |
| OBJ | YES | 两个 OBJ | 已实现 |
| project.json | YES | DataContract JSON | 已实现 |
| FBX | NO | 无 | 不存在 |
| Export Host 创建/链接源 RVT | NO | 无 | 不存在 |
| 修改源 RVT | NO | 无 | 不存在，且当前代码无 MoveElement/CopyElement |
| Shared/Project/Internal 坐标声明 | NO | 无 | Schema 缺失 |
| SourceRevision/ChangeSet/Rebind | NO | 无 | 不存在 |
| 增量更新 | NO | 无 | 每次创建新的 import root |

## 9. 配置、状态与日志

- Blender Bridge 用户配置仅见 AddonPreferences `material_library`。
- 导入项目状态主要存放在 Blender Collection/Object IDProperties 与 Text datablock。
- SLBH Tool 状态主要存放于 Scene PropertyGroup，pipeline 报告为 JSON 字符串/文本。
- Revit 端只有 TaskDialog 与安装脚本日志；没有结构化导出日志、诊断 ID 或可关联的 package log。
- 未发现 Bridge 自有 INI/YAML/SQLite/cache 状态库。

## 10. Git 状态

RevitBridge 目录未 Git 化。SLBH Tool 是 Git 仓库：

- Branch：`fix/alpha-safety`
- HEAD：`321f9257722312331e828b69cd4af76067a562d6`
- 最近提交：`321f925 chore: establish SLBH git development workflow`
- Tags：无
- Worktree：dirty，用户已有修改：`model_clean/alpha_fix.py`、`pipeline/runner.py`

本次审查未修改这些文件。
