using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Runtime.Serialization.Json;
using System.Text;
using System.Windows.Forms;

namespace SLBH.RevitBridge
{
    /// <summary>
    /// Exports the active 3D view for MEP coordination: the regular bridge package
    /// (project.json, model.obj) without prototype instancing, plus manifest.json and mep.json.
    /// Read-only: the model is never modified.
    /// </summary>
    [Transaction(TransactionMode.Manual)]
    public sealed class MepExportCommand : IExternalCommand
    {
        public const string ExporterVersion = "0.4.1";

        public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
        {
            UIDocument uiDoc = commandData.Application.ActiveUIDocument;
            Document doc = uiDoc != null ? uiDoc.Document : null;
            View3D view = doc != null ? doc.ActiveView as View3D : null;

            if (doc == null || view == null || view.IsTemplate || view.IsPerspective)
            {
                TaskDialog.Show("SLBH管综导出", "请先激活一个非透视三维视图。建议用剖面框框选要处理的区域。");
                return Result.Cancelled;
            }

            bool includeLinks;
            if (!TryChooseLinkOption(view, out includeLinks))
                return Result.Cancelled;

            string packageDir;
            if (!TryChoosePackageDir(doc, view, out packageDir))
                return Result.Cancelled;

            try
            {
                Directory.CreateDirectory(packageDir);
                MepManifest manifest = Export(commandData, doc, view, includeLinks, packageDir);

                MepCounts counts = manifest.Counts;
                TaskDialog.Show(
                    "SLBH管综导出",
                    "导出完成。\n\n" + packageDir +
                    "\n\n管线：" + counts.Curves +
                    "\n管件/附件/设备/支架：" + counts.FamilyInstances +
                    "\n连接件：" + counts.Connectors + "，连接：" + counts.Connections +
                    "\n带保温：" + counts.WithInsulation +
                    "\n疑似支吊架：" + counts.SupportCandidates +
                    "\n跨越范围边界：" + counts.CrossingScopeBoundary +
                    "\n标高 / 轴网 / 房间：" + counts.Levels + " / " + counts.Grids + " / " + counts.Rooms +
                    "\n诊断信息：" + counts.Diagnostics +
                    (manifest.Scope.SectionBoxActive ? "" : "\n\n注意：当前视图未启用剖面框，导出的是整个视图范围。"));
                return Result.Succeeded;
            }
            catch (Exception ex)
            {
                try
                {
                    File.WriteAllText(
                        Path.Combine(packageDir, "EXPORT_FAILED.txt"),
                        DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture) + Environment.NewLine + ex.ToString(),
                        new UTF8Encoding(false));
                }
                catch { }
                message = ex.Message;
                TaskDialog.Show("SLBH管综导出", "导出失败：\n" + ex.Message + "\n\n详细信息已写入：\n" + Path.Combine(packageDir, "EXPORT_FAILED.txt"));
                return Result.Failed;
            }
        }

        private static MepManifest Export(ExternalCommandData commandData, Document doc, View3D view, bool includeLinks, string packageDir)
        {
            var manifest = new MepManifest
            {
                CreatedAtUtc = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture),
                RevitVersion = commandData.Application.Application.VersionNumber,
                ProjectName = doc.Title,
                SourceDocument = doc.PathName,
                ViewName = view.Name
            };
            var data = new MepData();
            var watch = Stopwatch.StartNew();

            // 1. Geometry: the regular bridge exporter, one mesh per element, no dialogs.
            var project = new BridgeProject
            {
                ProjectName = doc.Title,
                SourceDocument = doc.PathName,
                RevitVersion = manifest.RevitVersion,
                ViewName = view.Name,
                ViewDisplayStyle = view.DisplayStyle.ToString(),
                ViewDetailLevel = view.DetailLevel.ToString(),
                ExportProfile = "MEP"
            };
            var options = new BridgeExportOptions
            {
                ExportLinkedModels = includeLinks,
                DisablePrototypes = true
            };
            project.Warnings.Add("export_linked_models=" + includeLinks.ToString());
            project.Warnings.Add("disable_prototypes=True");

            string modelPath = Path.Combine(packageDir, manifest.Files.ModelObj);
            string prototypesPath = Path.Combine(packageDir, manifest.Files.PrototypesObj);
            using (var context = new ObjExportContext(doc, view, modelPath, prototypesPath, project, ExportProfile.Standard, options))
            using (var exporter = new CustomExporter(doc, context))
            {
                exporter.IncludeGeometricObjects = true;
                RevitApiCompat.ExportView(exporter, view);
            }
            manifest.Timings.GeometryExport = watch.ElapsedMilliseconds;

            // 2. Parametric MEP data and drawing context.
            watch.Restart();
            var collector = new MepDataCollector(doc, view, includeLinks, manifest, data);
            collector.CollectElements();
            manifest.Timings.MepCollect = watch.ElapsedMilliseconds;

            watch.Restart();
            collector.CollectContext();
            manifest.Timings.ContextCollect = watch.ElapsedMilliseconds;

            // 3. Write. manifest.json is written last so that its presence marks a complete package.
            watch.Restart();
            WriteJson(Path.Combine(packageDir, manifest.Files.Project), project);
            WriteJson(Path.Combine(packageDir, manifest.Files.Mep), data);
            manifest.Timings.Write = watch.ElapsedMilliseconds;
            WriteJson(Path.Combine(packageDir, "manifest.json"), manifest);
            return manifest;
        }

        private static bool TryChooseLinkOption(View3D view, out bool includeLinks)
        {
            bool boxActive = false;
            try { boxActive = view.IsSectionBoxActive; }
            catch { }

            var dialog = new TaskDialog("SLBH管综导出");
            dialog.MainInstruction = "导出管综数据";
            dialog.MainContent =
                "导出当前三维视图中的管线参数、保温、连接关系、管件，以及标高、轴网、房间和坐标信息。模型不会被修改。" +
                (boxActive ? "" : "\n\n当前视图未启用剖面框，将导出整个视图范围，数据量可能较大。");
            dialog.CommonButtons = TaskDialogCommonButtons.Cancel;
            dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink1, "包含链接模型（推荐）", "结构、建筑链接中的梁板柱作为障碍物，轴网与房间一并读取");
            dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink2, "仅当前模型", "只导出当前文件中的构件");
            TaskDialogResult result = dialog.Show();
            includeLinks = result == TaskDialogResult.CommandLink1;
            return result == TaskDialogResult.CommandLink1 || result == TaskDialogResult.CommandLink2;
        }

        private static bool TryChoosePackageDir(Document doc, View3D view, out string packageDir)
        {
            packageDir = null;
            string defaultName = ExportCommand.SanitizeFileName(Path.GetFileNameWithoutExtension(doc.Title))
                + "_" + ExportCommand.SanitizeFileName(view.Name) + "_管综";

            using (var dialog = new SaveFileDialog())
            {
                dialog.Title = "命名并保存管综导出包";
                dialog.Filter = "SLBH桥接包 (*.slbh)|*.slbh";
                dialog.AddExtension = true;
                dialog.DefaultExt = "slbh";
                dialog.FileName = defaultName + ".slbh";
                dialog.CheckFileExists = false;
                dialog.OverwritePrompt = false;
                dialog.ValidateNames = true;
                if (dialog.ShowDialog() != DialogResult.OK)
                    return false;

                string parentDir = Path.GetDirectoryName(dialog.FileName);
                string packageName = ExportCommand.SanitizeFileName(Path.GetFileNameWithoutExtension(dialog.FileName));
                packageDir = ExportCommand.GetUniqueDirectory(Path.Combine(parentDir, packageName + ".slbh"));
                return true;
            }
        }

        private static void WriteJson<T>(string path, T value)
        {
            var serializer = new DataContractJsonSerializer(typeof(T));
            using (var stream = File.Create(path))
                serializer.WriteObject(stream, value);
        }
    }
}
