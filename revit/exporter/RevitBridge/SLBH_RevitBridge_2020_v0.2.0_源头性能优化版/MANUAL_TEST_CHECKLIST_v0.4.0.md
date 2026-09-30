# SLBH Revit Bridge v0.4.0 手工测试清单

## 编译与安装
- 关闭 Revit，执行 `.\build_revit.ps1 -Year 2020`，无编译错误。
- 执行 `INSTALL_REVIT2020.cmd`，打开 Revit 2020，“SLBH工具 > Blender桥接”面板出现两个按钮：“导出到Blender”“导出管综数据”。

## 回归
- “导出到Blender”对同一视图的导出结果与 v0.3.7 一致（构件数、原型数）。

## 管综导出
1. 打开样例模型，新建或激活一个三维视图，启用剖面框，框选一段走廊（约 20–40 米）。
2. 点击“导出管综数据”，选择“包含链接模型”，保存为 `走廊A.slbh`。
3. 完成提示中记录：管线、管件、连接、带保温、疑似支吊架、诊断信息的数量。截图发回。
4. 导出目录应包含：manifest.json、mep.json、project.json、model.obj、prototypes.obj。
5. 在 Revit 中抽查，并与体检报告对照：
   - 一根带保温的圆管：外径、保温厚度、系统名称；
   - 一根矩形风管：宽、高；
   - 一段桥架：宽、高；
   - 一个弯头：所连两根管线；
   - 一根有坡度的排水管：坡度。
6. 如果导出失败，目录中会有 EXPORT_FAILED.txt，把它发回。

## 上传
- 把整个 `走廊A.slbh` 目录压缩为 zip，按 GitHub Release 附件方式上传，告知标签名。
