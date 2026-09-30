# Revit 2020 Plugin Load Audit

## 结论

当前 Revit 2020 看不到 SLBH Revit Bridge 的直接原因是加载入口 manifest 缺失。DLL 仍完整存在，但 Revit 不会扫描 `%LOCALAPPDATA%` 中的 DLL；没有 `.addin`，加载链不会进入 Assembly、FullClassName 或 Ribbon 阶段。

## 确定性检测信号

2026-08-09 的只读检测结果：

```text
ManifestExists: false
DllExists: true
ExpectedManifest:
C:\Users\24155\AppData\Roaming\Autodesk\Revit\Addins\2020\SLBH.RevitBridge.2020.addin
ExpectedDll:
C:\Users\24155\AppData\Local\SLBH\RevitBridge2020\SLBH.RevitBridge.2020.dll
```

该检测可以稳定复现用户的“Ribbon 完全不出现”症状。

## 加载链审查

| 阶段 | 证据 | 判断 |
|---|---|---|
| Addins 目录 | 用户级与系统级目录均存在 | PASS |
| `.addin` | 两个目录均无 `SLBH.RevitBridge.2020.addin` | FAIL / 根因 |
| Assembly 路径 | 预期 DLL 路径存在 | PASS |
| DLL 内容 | 版本 `0.3.6.0`，与当前 Build SHA256 相同 | PASS |
| FullClassName | manifest 为 `SLBH.RevitBridge.App`，源码一致 | PASS（静态） |
| Application 类型 | `App : IExternalApplication` | PASS（静态） |
| Command 类型 | `ExportCommand : IExternalCommand` | PASS（静态） |
| Ribbon | `SLBH工具 > Blender桥接` | 未到达 |

## CONFIRMED

1. `%APPDATA%\Autodesk\Revit\Addins\2020` 中没有当前 manifest。
2. `%ProgramData%\Autodesk\Revit\Addins\2020` 中也没有当前 manifest。
3. 安装目标 DLL 仍在，FileVersion 为 `0.3.6.0`。
4. 安装 DLL、工程根 DLL、Build DLL 的 SHA256 完全相同。
5. 本机 Revit 2020 的 API DLL 与编译使用的 `lib/Revit2020` 文件完全相同。
6. 现存 manifest 中没有重复当前 AddInId `8D5A706F-352B-4B86-91E0-573A47772020`。
7. 最新 Revit journals 中没有当前 Bridge 名称或 AddInId，符合“未发现 manifest，未尝试加载”。

## LIKELY

1. 当前部署把“注册”完全依赖于用户级单个 `.addin` 文件；该文件一旦未恢复，DLL 即成为孤立文件。
2. Revit 重装之后没有执行 Bridge 的 Repair/Install 流程，因此注册入口没有自愈。
3. 工程根 manifest 和发布包 manifest 包含当前用户绝对路径；如果脱离安装脚本手工复制到其他用户，Assembly 路径会失效。

上述第 2 点说明机制，不说明删除者。仅凭时间顺序不能证明是 Revit 卸载器删除了 manifest。

## UNKNOWN

1. manifest 由 Revit 重装、旧卸载脚本、清理工具还是手工操作删除：UNKNOWN。
2. manifest 恢复后 Revit 是否会出现其他运行时加载错误：尚未在本轮恢复/启动验证，UNKNOWN。
3. Ribbon 创建后是否与其他 SLBH Add-in 存在面板级冲突：UNKNOWN。

## 编译环境

- 没有 `.csproj`，直接使用 Framework64 `csc.exe`。
- Revit 2020 引用版本为 `20.0.0.377`，与本机运行时完全一致。
- 2019/2020 共用源码，仅 `CustomExporter.Export` 调用通过条件编译适配。
- 未显式设置 `/platform`、构建配置、PDB 或 Copy Local；不存在传统 csproj 中可审查的对应设置。
- 第三方依赖为零；仅 Revit API 与 .NET Framework。

## 部署缺陷

1. 安装状态分散在 `%LOCALAPPDATA%` DLL 与 `%APPDATA%` manifest，没有统一安装清单。
2. `check_install_revit2020.ps1` 只报告 FOUND/MISSING，返回码不会因缺失而失败。
3. 没有 Repair 模式，也没有 Revit 启动前的自检入口。
4. 旧安装日志显示历史文件名 `SLBH.RevitBridge.dll` / `SLBH.RevitBridge.addin`，当前改为带年份名称，存在历史残留认知成本。
5. 包内 manifest 是按打包机器用户路径生成，不应作为跨机器静态注册文件使用。

## 推荐部署方向（本轮不实施）

- 下一 Task 先恢复 2020 manifest，并执行 Revit 真实启动验证。
- 后续引入可重复执行的 Install / Repair / Upgrade / Uninstall 四态安装器。
- manifest 应由安装时生成，或使用稳定机器级目录；不得发布打包机用户名绝对路径。
- 检查命令应以非零返回码报告缺失和哈希不一致。
- 安装清单至少记录产品版本、Revit 年份、DLL、manifest、安装范围和最后修复时间。
