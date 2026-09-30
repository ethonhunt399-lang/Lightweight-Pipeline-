# Task 1 — Revit 2020 Add-in 恢复与稳定基线

- 执行日期：2026-08-09
- 范围：仅恢复并验证现有 Revit Bridge 0.3.6 能力；未改动 SLBH Tool，未做命名、Contract、Schema 或架构重构。
- 结论：**PASS with WARNINGS**

## 1. 问题与根因

Revit Bridge 源码与 0.3.6 DLL 仍存在，但用户级 Revit 2020 Add-in 注册文件
`%APPDATA%\Autodesk\Revit\Addins\2020\SLBH.RevitBridge.2020.addin` 缺失，导致 Revit 2020 不会发现并启动该 `IExternalApplication`。

仓库已有 `SLBH.RevitBridge.2020.addin` 和 `install_revit2020.ps1`，所以未新建 AddInId、类名或部署系统；直接复用历史正式值恢复用户级注册。

## 2. Git 基线

- 仓库根：`G:\SLBH_DEV\RevitBridge`
- 分支：`master`
- 基线提交：`cc042319059682055603ce16651cad83a3a07101`
- 提交说明：`Baseline before Revit 2020 add-in recovery`
- 基线提交后 working tree clean。
- `.gitignore` 排除 `.vs/`、`bin/`、`obj/`、构建/发布物、journal、临时测试输出和 `.slbh`；构建必需的 `lib/Revit2019` 与 `lib/Revit2020` 依赖 DLL 已保留在 Git。

## 3. Manifest 与部署

通过仓库已有的 `install_revit2020.ps1` 恢复，因当前 PowerShell execution policy 拦截直接运行，本次以 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File` 执行同一脚本。安装时间为 2026-08-09 13:47:24。

| 字段 | 实际值 |
|---|---|
| Type | `Application` |
| Name | `SLBH Revit Bridge 2020` |
| Assembly | `C:\Users\24155\AppData\Local\SLBH\RevitBridge2020\SLBH.RevitBridge.2020.dll` |
| AddInId | `8D5A706F-352B-4B86-91E0-573A47772020` |
| FullClassName | `SLBH.RevitBridge.App` |
| VendorId | `SLBH` |
| VendorDescription | `SLBH BIM Tools` |

- 实际 manifest：`C:\Users\24155\AppData\Roaming\Autodesk\Revit\Addins\2020\SLBH.RevitBridge.2020.addin`
- 未在 `%PROGRAMDATA%` 再部署一份；跨 `%APPDATA%` 和 `%PROGRAMDATA%` 扫描到该 AddInId 共 1 份。
- Assembly 使用了用户本地绝对路径：**TEMPORARY / DEPLOYMENT DEBT**。正式安装器与可迁移部署不在 Task 1 范围。

## 4. DLL 与 Build 判断

| 项目 | 结果 |
|---|---|
| 仓库 DLL | `G:\SLBH_DEV\RevitBridge\SLBH_RevitBridge_2020_v0.2.0_源头性能优化版\SLBH.RevitBridge.2020.dll` |
| 部署 DLL | `C:\Users\24155\AppData\Local\SLBH\RevitBridge2020\SLBH.RevitBridge.2020.dll` |
| AssemblyVersion / FileVersion | `0.3.6.0` / `0.3.6.0` |
| 仓库 DLL 时间 | `2026-06-19 08:53:51` |
| SHA-256（仓库与部署两处） | `8AACC92C7A5073CF2933FC82D8F293D69CBB1E06E32C962DF9964BEC37ED41C6` |
| Revit 2020 API | `20.0.0.377`，与安装的 Revit 2020 API DLL 匹配 |

Task 0 已确认当前源码的 Build 产物与仓库 DLL 一致，本次再次确认仓库 DLL、`Build`、`Dist` 及部署 DLL 哈希一致。真实 Revit 加载与执行也成功，因此 **Build not required**，未为了“保险”重新编译。

## 5. Revit 2020 真实加载与 Ribbon

- 应用：`C:\Program Files\Autodesk\Revit 2020\Revit.exe`，FileVersion `20.0.0.377`。
- 以 Autodesk 自带小型示例 `rst_basic_sample_project.rvt` 真实启动 Revit 2020；对本次确认的 Bridge DLL 选择“总是加载”。
- journal 记录 `API_SUCCESS { Starting External Application: SLBH Revit Bridge 2020 ... }`。
- Ribbon 实际显示：Tab `SLBH工具` → Panel `Blender桥接` → Button `导出到 Blender`。
- Button 执行的命令：`SLBH.RevitBridge.ExportCommand`。
- 测试使用非透视 `{3D}` 视图。测试中插入的 Link 只在内存中；退出时选择“否”，没有保存或改写 Autodesk 示例文件。

## 6. Journal 检查

- journal：`C:\Users\24155\AppData\Local\Autodesk\Revit\Autodesk Revit 2020\Journals\journal.0046.txt`
- 最终写入：2026-08-09 14:27:13。
- **PASS**：Bridge 启动是 `API_SUCCESS`，两次 Ribbon command 与两个输出路径均在 journal 中。
- **PASS**：针对 `RevitBridge` / `SLBH` 与 `API_ERROR|Exception|Could not load|FileNotFound|FileLoad|TypeLoad|BadImageFormat` 的组合扫描命中数为 0。
- **WARNING（非 Bridge）**：journal 中有 Autodesk `Model Review Events` 启动 `API_ERROR`，以及应用 GUID `876e89af-ab49-4c54-a0f3-80529a1b3c4e` 的 `UIRibbon` 在视图/文档事件中抛出 `NullReferenceException`。其注册应用、类名和 DLL 均不是 SLBH Revit Bridge，且不阻断本次两次导出，但应作为当前 Revit 环境债务单独跟踪。

## 7. Host 最小导出

来源为 Autodesk `rst_basic_sample_project.rvt`，使用 Standard preset，输出到被 `.gitignore` 排除的 `_task1_test\host_standard.slbh`。

| 输出 | 大小 |
|---|---:|
| `project.json` | 1,359,467 bytes |
| `model.obj` | 8,038,525 bytes |
| `prototypes.obj` | 1,682,156 bytes |

- Revit 结果：构件 882，共享实例 96，唯一原型 5，参数化直圆管 34。
- `project.json` 以 UTF-8 JSON 成功解析；`schema_version=0.3.6`、`bridge_version=0.3.6`、`elements=882`。
- 宿主元素的 `source_model_key` 均为 `HOST`。抽查记录包含 `unique_id`、`element_id`、`category`、`family`、`type`、`level`、`transform`、`prototype_id`等现有 Schema 字段。

## 8. Revit Link 导出

在同一示例中临时以 Origin-to-Origin 插入 Autodesk `Arch Link Model.rvt`，勾选“导出链接模型”，使用 Quick preset 输出 `_task1_test\linked_quick.slbh`。

| 输出 | 大小 |
|---|---:|
| `project.json` | 11,295,004 bytes |
| `model.obj` | 51,100,418 bytes |
| `prototypes.obj` | 9,514,164 bytes |

- Revit 结果：总构件 6,613，共享实例 727，唯一原型 137，参数化直圆管 202。
- JSON 结果：6,613 个 elements，其中 5,730 个来自 Link；`links` 共 1 条。
- 发现的链接文档为 `Arch Link Model`，链接实例为 `Arch Link Model.rvt : 6 : 位置 <未共享>`，状态 `loaded`，`link_depth=1`。
- 链接 source key 为 `LINK_b9c1ebf74ba8e1e11905dfb9_I1474097`，与 `HOST` 明确分组。`link_transform` 为 4×4 单位矩阵，与 Origin-to-Origin 插入一致；未见明显坐标异常或重复 Link occurrence。

## 9. Blender 4.5 LTS smoke test

- 使用 `C:\Program Files\Blender Foundation\Blender 4.5\blender.exe`（Blender 4.5.10 LTS）在 `--background --factory-startup` 独立进程中临时启用已安装的 `slbh_revit_bridge`。
- 输入为 Revit 0.3.6 生成的 `host_standard.slbh`，通过现有 `bpy.ops.slbh.import_revit_bridge` 只读导入。
- Operator 结果：`{'FINISHED'}`。
- Blender 结果：454 个对象，96 个源头实例，5 个共享原型，117 个唯一网格，来源为 `HOST`；Source/Presentation 分层由现有 importer 正常建立。
- 未修改 Blender Bridge，未保存偏好。用户原有的未保存 Blender GUI 进程保持未动。

## 10. 修改文件与提交

仓库内本轮新增/修改：

- `G:\SLBH_DEV\RevitBridge\.gitignore`（纳入基线提交）
- `docs/revit_bridge_audit/10_TASK1_REVIT2020_RECOVERY.md`

仓库外部署副本：

- `C:\Users\24155\AppData\Roaming\Autodesk\Revit\Addins\2020\SLBH.RevitBridge.2020.addin`
- `C:\Users\24155\AppData\Local\SLBH\RevitBridge2020\SLBH.RevitBridge.2020.dll`

源码、AssemblyVersion、Bridge Schema、Blender Bridge 和 SLBH Tool 均未修改。SLBH Tool 仍为 `fix/alpha-safety` / `321f925`。

Git 提交：

1. `cc042319059682055603ce16651cad83a3a07101` — `Baseline before Revit 2020 add-in recovery`
2. 本文档将连同恢复验证结果使用 `fix: restore Revit 2020 add-in registration` 提交。

## 11. 验收与剩余问题

- [x] RevitBridge Git 基线已建立，可正常回退
- [x] Revit 2020 用户级 `.addin` 已恢复，无重复 AddInId
- [x] Revit 2020 真实加载成功，Ribbon/command 可见可执行
- [x] Bridge 无关键 Add-in load error
- [x] Host RVT 和 Revit Link 导出成功，JSON/OBJ/prototype 可读
- [x] Blender Bridge smoke import 通过
- [x] SLBH Tool 未改动

尚未解决：

1. Assembly 仍指向 `%LOCALAPPDATA%` 的绝对路径，属于 **TEMPORARY / DEPLOYMENT DEBT**；正式安装器、多版本部署与升级机制留待后续 Deployment Task。
2. `SHA256.txt` 是历史 0.2.1 发布记录，与当前 0.3.6 DLL 不一致；应在后续发布流程中明确生成和更新责任。
3. 空白方式启动 Revit 时曾停留在 Autodesk 许可 splash；通过打开官方示例文件后完成全部真机验证。这是当前主机启动/许可环境的 WARNING，无证据指向 Bridge。
4. Revit journal 中存在非 Bridge 的 `Model Review Events` 与 `UIRibbon` 异常，应与 Bridge 恢复分开处理。
