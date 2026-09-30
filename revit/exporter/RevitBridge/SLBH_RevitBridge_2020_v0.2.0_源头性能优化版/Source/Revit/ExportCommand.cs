using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.Serialization.Json;
using System.Text;
using System.Windows.Forms;

namespace SLBH.RevitBridge
{
    [Transaction(TransactionMode.Manual)]
    public sealed class ExportCommand : IExternalCommand
    {
        private const double FeetToMeters = 0.3048;

        public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
        {
            UIDocument uiDoc = commandData.Application.ActiveUIDocument;
            Document doc = uiDoc != null ? uiDoc.Document : null;
            View3D view = doc != null ? doc.ActiveView as View3D : null;

            if (doc == null || view == null || view.IsTemplate || view.IsPerspective)
            {
                TaskDialog.Show("SLBH Revit桥接", "请先激活一个非透视三维视图。当前视图的剖面框、隐藏和可见性将作为导出范围。");
                return Result.Cancelled;
            }

            ExportProfile profile;
            if (!TryChooseProfile(out profile))
                return Result.Cancelled;

            BridgeExportOptions exportOptions;
            if (!TryChooseExportOptions(doc, view, out exportOptions))
                return Result.Cancelled;

            List<BridgeFamilyRule> familyRules = ScanFamilyRules(doc, view, profile);
            if (!TryChooseFamilyRules(familyRules, profile))
                return Result.Cancelled;
            foreach (BridgeFamilyRule rule in familyRules)
                exportOptions.FamilyActions[rule.RuleKey] = NormalizeAction(rule.Action);

            string defaultBaseName = SanitizeFileName(Path.GetFileNameWithoutExtension(doc.Title));
            string defaultViewName = SanitizeFileName(view.Name);
            string defaultName = defaultBaseName + "_" + defaultViewName;

            using (var dialog = new SaveFileDialog())
            {
                dialog.Title = "命名并保存SLBH桥接包";
                dialog.Filter = "SLBH桥接包 (*.slbh)|*.slbh";
                dialog.AddExtension = true;
                dialog.DefaultExt = "slbh";
                dialog.FileName = defaultName + ".slbh";
                dialog.CheckFileExists = false;
                dialog.OverwritePrompt = false;
                dialog.ValidateNames = true;

                if (dialog.ShowDialog() != DialogResult.OK)
                    return Result.Cancelled;

                string requestedPath = dialog.FileName;
                string parentDir = Path.GetDirectoryName(requestedPath);
                string packageName = SanitizeFileName(Path.GetFileNameWithoutExtension(requestedPath));
                string packageDir = GetUniqueDirectory(Path.Combine(parentDir, packageName + ".slbh"));
                Directory.CreateDirectory(packageDir);

                string modelPath = Path.Combine(packageDir, "model.obj");
                string prototypesPath = Path.Combine(packageDir, "prototypes.obj");
                string jsonPath = Path.Combine(packageDir, "project.json");

                try
                {
                    var project = new BridgeProject
                    {
                        ProjectName = doc.Title,
                        SourceDocument = doc.PathName,
                        RevitVersion = commandData.Application.Application.VersionNumber,
                        ViewName = view.Name,
                        ViewDisplayStyle = view.DisplayStyle.ToString(),
                        ViewDetailLevel = view.DetailLevel.ToString(),
                        ExportProfile = ProfileCode(profile)
                    };

                    project.Warnings.Add("export_linked_models=" + exportOptions.ExportLinkedModels.ToString());
                    project.Warnings.Add("link_organization=" + exportOptions.LinkOrganization);
                    project.Warnings.Add("view_detail_level=" + view.DetailLevel.ToString());
                    project.Warnings.Add("link_detail_policy=" + exportOptions.LinkDetailPolicy);
                    project.Warnings.Add("family_rule_count=" + familyRules.Count.ToString());
                    project.FamilyRules.AddRange(familyRules);

                    using (var context = new ObjExportContext(doc, view, modelPath, prototypesPath, project, profile, exportOptions))
                    using (var exporter = new CustomExporter(doc, context))
                    {
                        exporter.IncludeGeometricObjects = true;
                        RevitApiCompat.ExportView(exporter, view);
                    }

                    WriteJson(jsonPath, project);
                    BridgePerformance perf = project.Performance;
                    TaskDialog.Show(
                        "SLBH Revit桥接",
                        "导出完成。\n\n" + packageDir +
                        "\n\n构件：" + project.Elements.Count +
                        "\n共享实例：" + perf.PrototypeInstances +
                        "\n唯一原型：" + perf.UniquePrototypes +
                        "\n参数化直圆管：" + perf.ParametricRound +
                        "\n避免重复面（估算）：" + perf.EstimatedRepeatedFacesAvoided);
                    return Result.Succeeded;
                }
                catch (Exception ex)
                {
                    // Leave a marker so an incomplete package is never mistaken for a valid export
                    // (project.json is only written after a successful export).
                    try
                    {
                        File.WriteAllText(
                            Path.Combine(packageDir, "EXPORT_FAILED.txt"),
                            DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + Environment.NewLine + ex.ToString(),
                            new UTF8Encoding(false));
                    }
                    catch { }
                    message = ex.Message;
                    TaskDialog.Show("SLBH Revit桥接", "导出失败：\n" + ex.Message + "\n\n详细信息已写入：\n" + Path.Combine(packageDir, "EXPORT_FAILED.txt"));
                    return Result.Failed;
                }
            }
        }

        private static bool TryChooseExportOptions(Document doc, View3D view, out BridgeExportOptions options)
        {
            options = new BridgeExportOptions();
            List<string> linkSummaries = GetVisibleLinkSummaries(doc, view);

            using (var form = new System.Windows.Forms.Form())
            using (var exportLinks = new CheckBox())
            using (var mergeMode = new RadioButton())
            using (var sourceMode = new RadioButton())
            using (var instanceMode = new RadioButton())
            using (var label = new Label())
            using (var detailLabel = new Label())
            using (var list = new ListBox())
            using (var ok = new Button())
            using (var cancel = new Button())
            {
                form.Text = "SLBH Revit Bridge - Linked Models";
                form.Width = 520;
                form.Height = 470;
                form.StartPosition = FormStartPosition.CenterScreen;
                form.FormBorderStyle = FormBorderStyle.FixedDialog;
                form.MaximizeBox = false;
                form.MinimizeBox = false;

                exportLinks.Text = "Export linked models";
                exportLinks.Checked = true;
                exportLinks.Left = 16;
                exportLinks.Top = 16;
                exportLinks.Width = 220;

                label.Text = "Linked instances visible in the current 3D view:";
                label.Left = 16;
                label.Top = 48;
                label.Width = 460;

                list.Left = 16;
                list.Top = 72;
                list.Width = 470;
                list.Height = 170;
                foreach (string item in linkSummaries)
                    list.Items.Add(item);
                if (list.Items.Count == 0)
                    list.Items.Add("No visible Revit link instances detected in this view.");

                mergeMode.Text = "Merge into discipline/floor";
                mergeMode.Left = 16;
                mergeMode.Top = 255;
                mergeMode.Width = 260;
                sourceMode.Text = "Group by source model";
                sourceMode.Left = 16;
                sourceMode.Top = 282;
                sourceMode.Width = 260;
                sourceMode.Checked = true;
                instanceMode.Text = "Group by link instance";
                instanceMode.Left = 16;
                instanceMode.Top = 309;
                instanceMode.Width = 260;

                detailLabel.Text = "Link detail follows the active 3D view and Revit Link Display Settings.";
                detailLabel.Left = 16;
                detailLabel.Top = 340;
                detailLabel.Width = 470;
                detailLabel.Height = 32;

                ok.Text = "OK";
                ok.DialogResult = DialogResult.OK;
                ok.Left = 310;
                ok.Top = 385;
                ok.Width = 80;
                cancel.Text = "Cancel";
                cancel.DialogResult = DialogResult.Cancel;
                cancel.Left = 405;
                cancel.Top = 385;
                cancel.Width = 80;

                form.Controls.Add(exportLinks);
                form.Controls.Add(label);
                form.Controls.Add(list);
                form.Controls.Add(mergeMode);
                form.Controls.Add(sourceMode);
                form.Controls.Add(instanceMode);
                form.Controls.Add(detailLabel);
                form.Controls.Add(ok);
                form.Controls.Add(cancel);
                form.AcceptButton = ok;
                form.CancelButton = cancel;

                if (form.ShowDialog() != DialogResult.OK)
                    return false;

                options.ExportLinkedModels = exportLinks.Checked;
                options.LinkOrganization = instanceMode.Checked ? "LINK_INSTANCE" : mergeMode.Checked ? "MERGED" : "SOURCE_MODEL";
                options.LinkDetailPolicy = "CURRENT_VIEW_AND_LINK_DISPLAY";
                return true;
            }
        }

        private static List<string> GetVisibleLinkSummaries(Document doc, View3D view)
        {
            var result = new List<string>();
            try
            {
                var collector = new FilteredElementCollector(doc, view.Id).OfClass(typeof(RevitLinkInstance));
                foreach (RevitLinkInstance instance in collector.Cast<RevitLinkInstance>())
                {
                    string status = "loaded";
                    string documentName = "";
                    try
                    {
                        Document linkDoc = instance.GetLinkDocument();
                        if (linkDoc == null)
                            status = "unloaded";
                        else
                            documentName = linkDoc.Title;
                    }
                    catch
                    {
                        status = "unreadable";
                    }

                    string typeName = "";
                    try
                    {
                        Element type = doc.GetElement(instance.GetTypeId());
                        typeName = type != null ? type.Name : "";
                    }
                    catch { }

                    result.Add(instance.Name + " / " + typeName + " / " + status + (string.IsNullOrWhiteSpace(documentName) ? "" : " / " + documentName));
                }
            }
            catch { }
            return result;
        }

        private static List<BridgeFamilyRule> ScanFamilyRules(Document doc, View3D view, ExportProfile profile)
        {
            var rules = new Dictionary<string, BridgeFamilyRule>();
            try
            {
                var collector = new FilteredElementCollector(doc, view.Id)
                    .WhereElementIsNotElementType()
                    .OfClass(typeof(FamilyInstance));
                foreach (FamilyInstance instance in collector.Cast<FamilyInstance>())
                    AddFamilyRuleSample(doc, view, profile, rules, instance);
            }
            catch { }

            List<BridgeFamilyRule> result = rules.Values
                .OrderByDescending(x => x.EstimatedTrianglesTotal)
                .ThenBy(x => x.Category)
                .ThenBy(x => x.Family)
                .ThenBy(x => x.Type)
                .ToList();
            return result;
        }

        private static void AddFamilyRuleSample(Document doc, View3D view, ExportProfile profile, Dictionary<string, BridgeFamilyRule> rules, FamilyInstance instance)
        {
            string category = instance.Category != null ? instance.Category.Name : "Uncategorized";
            string family = "";
            string type = "";
            try
            {
                family = instance.Symbol != null && instance.Symbol.Family != null ? instance.Symbol.Family.Name : "";
                type = instance.Symbol != null ? instance.Symbol.Name : "";
            }
            catch { }
            if (string.IsNullOrWhiteSpace(family))
                family = GetElementTypeName(doc, instance);
            if (string.IsNullOrWhiteSpace(type))
                type = GetElementTypeName(doc, instance);

            string key = FamilyRuleKey(category, family, type);
            BridgeFamilyRule rule;
            if (!rules.TryGetValue(key, out rule))
            {
                rule = new BridgeFamilyRule
                {
                    RuleKey = key,
                    Category = category,
                    Family = family,
                    Type = type,
                    EstimatedTrianglesEach = EstimateTrianglesEach(category, family, type),
                    MaxSizeMeters = 0.0
                };
                rules.Add(key, rule);
            }

            rule.Count++;
            rule.MaxSizeMeters = Math.Max(rule.MaxSizeMeters, EstimateMaxSizeMeters(instance, view));
            rule.EstimatedTrianglesTotal = (long)rule.EstimatedTrianglesEach * rule.Count;
            rule.IsInstanceCandidate = IsLikelyRepeatable(category, family, type);
            rule.IsHighBurden = rule.Count >= 20 || rule.EstimatedTrianglesTotal >= 50000 || (rule.Count >= 8 && rule.EstimatedTrianglesEach >= 2000);
            rule.RecommendedAction = RecommendAction(profile, rule);
            rule.Action = rule.RecommendedAction;
            rule.Reason = BuildRuleReason(rule);
        }

        private static bool TryChooseFamilyRules(List<BridgeFamilyRule> rules, ExportProfile profile)
        {
            if (rules == null || rules.Count == 0)
                return true;

            using (var form = new System.Windows.Forms.Form())
            using (var label = new Label())
            using (var grid = new DataGridView())
            using (var ok = new Button())
            using (var cancel = new Button())
            {
                form.Text = "SLBH Revit Bridge - Family Type Export Rules";
                form.Width = 980;
                form.Height = 620;
                form.StartPosition = FormStartPosition.CenterScreen;
                form.FormBorderStyle = FormBorderStyle.Sizable;
                form.MinimizeBox = false;

                label.Text = "Review visible family/type load estimates. Actions: FULL keeps unique mesh, PROTOTYPE allows safe source instancing, SKIP does not export that family/type.";
                label.Left = 12;
                label.Top = 12;
                label.Width = 940;
                label.Height = 32;

                grid.Left = 12;
                grid.Top = 50;
                grid.Width = 940;
                grid.Height = 480;
                grid.Anchor = AnchorStyles.Left | AnchorStyles.Top | AnchorStyles.Right | AnchorStyles.Bottom;
                grid.AllowUserToAddRows = false;
                grid.AllowUserToDeleteRows = false;
                grid.MultiSelect = false;
                grid.RowHeadersVisible = false;
                grid.SelectionMode = DataGridViewSelectionMode.FullRowSelect;
                grid.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill;

                grid.Columns.Add(ReadOnlyColumn("Category", "Category", 115));
                grid.Columns.Add(ReadOnlyColumn("Family", "Family", 190));
                grid.Columns.Add(ReadOnlyColumn("Type", "Type", 190));
                grid.Columns.Add(ReadOnlyColumn("Count", "Count", 60));
                grid.Columns.Add(ReadOnlyColumn("Each", "Tri/Each Est.", 85));
                grid.Columns.Add(ReadOnlyColumn("Total", "Tri/Total Est.", 95));
                grid.Columns.Add(ReadOnlyColumn("Size", "Max m", 70));
                grid.Columns.Add(ReadOnlyColumn("Candidate", "Instance", 70));
                var action = new DataGridViewComboBoxColumn();
                action.Name = "Action";
                action.HeaderText = "Action";
                action.Items.AddRange(new object[] { "FULL", "PROTOTYPE", "SKIP" });
                action.Width = 95;
                grid.Columns.Add(action);
                grid.Columns.Add(ReadOnlyColumn("Reason", "Reason", 230));

                foreach (BridgeFamilyRule rule in rules)
                {
                    int row = grid.Rows.Add(
                        rule.Category,
                        rule.Family,
                        rule.Type,
                        rule.Count.ToString(),
                        rule.EstimatedTrianglesEach.ToString(),
                        rule.EstimatedTrianglesTotal.ToString(),
                        rule.MaxSizeMeters.ToString("0.###"),
                        rule.IsInstanceCandidate ? "yes" : "no",
                        NormalizeAction(rule.Action),
                        rule.Reason);
                    grid.Rows[row].Tag = rule;
                }

                ok.Text = "OK";
                ok.DialogResult = DialogResult.OK;
                ok.Left = 762;
                ok.Top = 540;
                ok.Width = 80;
                ok.Anchor = AnchorStyles.Right | AnchorStyles.Bottom;
                cancel.Text = "Cancel";
                cancel.DialogResult = DialogResult.Cancel;
                cancel.Left = 872;
                cancel.Top = 540;
                cancel.Width = 80;
                cancel.Anchor = AnchorStyles.Right | AnchorStyles.Bottom;

                form.Controls.Add(label);
                form.Controls.Add(grid);
                form.Controls.Add(ok);
                form.Controls.Add(cancel);
                form.AcceptButton = ok;
                form.CancelButton = cancel;

                if (form.ShowDialog() != DialogResult.OK)
                    return false;

                foreach (DataGridViewRow row in grid.Rows)
                {
                    BridgeFamilyRule rule = row.Tag as BridgeFamilyRule;
                    if (rule == null)
                        continue;
                    rule.Action = NormalizeAction(Convert.ToString(row.Cells["Action"].Value));
                    rule.Reason = BuildRuleReason(rule);
                }
                return true;
            }
        }

        private static DataGridViewTextBoxColumn ReadOnlyColumn(string name, string header, int width)
        {
            return new DataGridViewTextBoxColumn
            {
                Name = name,
                HeaderText = header,
                ReadOnly = true,
                Width = width
            };
        }

        private static string RecommendAction(ExportProfile profile, BridgeFamilyRule rule)
        {
            string text = (rule.Category + " " + rule.Family + " " + rule.Type).ToLowerInvariant();
            if (profile == ExportProfile.Fast && ContainsAny(text, "bolt", "fastener", "hanger", "support", "螺栓", "紧固", "支吊架"))
                return "SKIP";
            if (rule.IsInstanceCandidate && rule.Count >= 2)
                return "PROTOTYPE";
            if (profile == ExportProfile.Fast && rule.IsHighBurden)
                return "PROTOTYPE";
            return "FULL";
        }

        private static string BuildRuleReason(BridgeFamilyRule rule)
        {
            if (rule.Action == "SKIP")
                return "User rule: skipped before geometry export.";
            if (rule.Action == "FULL")
                return "User rule: unique mesh; no source instancing.";
            if (rule.IsHighBurden)
                return "High repeated load estimate; source instancing recommended.";
            if (rule.IsInstanceCandidate)
                return "Repeatable family category; safe instancing when geometry/material hash matches.";
            return "Conservative unique mesh recommended.";
        }

        private static string NormalizeAction(string action)
        {
            string value = (action ?? "").Trim().ToUpperInvariant();
            if (value == "SKIP" || value == "FULL" || value == "PROTOTYPE")
                return value;
            return "PROTOTYPE";
        }

        private static int EstimateTrianglesEach(string category, string family, string type)
        {
            string text = (category + " " + family + " " + type).ToLowerInvariant();
            if (ContainsAny(text, "sprinkler", "喷头", "diffuser", "air terminal", "风口", "散流器")) return 900;
            if (ContainsAny(text, "valve", "damper", "阀", "风阀")) return 1800;
            if (ContainsAny(text, "hanger", "support", "支吊架", "bolt", "螺栓", "fastener", "紧固")) return 650;
            if (ContainsAny(text, "lighting", "light", "灯具", "furniture", "家具", "plumbing fixture", "卫生器具")) return 1200;
            if (ContainsAny(text, "mechanical equipment", "机械设备", "air conditioner", "空调", "equipment", "设备")) return 2600;
            if (ContainsAny(text, "door", "window", "门", "窗")) return 1500;
            return 750;
        }

        private static bool IsLikelyRepeatable(string category, string family, string type)
        {
            string text = (category + " " + family + " " + type).ToLowerInvariant();
            return ContainsAny(text,
                "sprinkler", "喷头",
                "diffuser", "air terminal", "风口", "散流器",
                "valve", "damper", "阀", "风阀",
                "hanger", "support", "支吊架",
                "lighting", "light", "灯具",
                "furniture", "家具",
                "door", "window", "门", "窗",
                "plumbing fixture", "卫生器具",
                "mechanical equipment", "机械设备", "空调", "equipment", "设备");
        }

        private static double EstimateMaxSizeMeters(Element element, View3D view)
        {
            try
            {
                BoundingBoxXYZ box = element.get_BoundingBox(view);
                if (box == null)
                    return 0.0;
                double dx = Math.Abs(box.Max.X - box.Min.X);
                double dy = Math.Abs(box.Max.Y - box.Min.Y);
                double dz = Math.Abs(box.Max.Z - box.Min.Z);
                return Math.Max(Math.Max(dx, dy), dz) * FeetToMeters;
            }
            catch { return 0.0; }
        }

        private static string GetElementTypeName(Document doc, Element element)
        {
            try
            {
                Element type = doc.GetElement(element.GetTypeId());
                return type != null ? type.Name : "";
            }
            catch { return ""; }
        }

        internal static string FamilyRuleKey(string category, string family, string type)
        {
            return NormalizeRulePart(category) + "|" + NormalizeRulePart(family) + "|" + NormalizeRulePart(type);
        }

        private static string NormalizeRulePart(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
                return "";
            return value.Trim().ToUpperInvariant();
        }

        private static bool ContainsAny(string source, params string[] values)
        {
            if (source == null)
                return false;
            return values.Any(value => source.IndexOf(value, StringComparison.OrdinalIgnoreCase) >= 0);
        }

        private static bool TryChooseProfile(out ExportProfile profile)
        {
            var dialog = new TaskDialog("SLBH导出精度");
            dialog.MainInstruction = "选择导出预设";
            dialog.MainContent = "所有预设都会启用重复族原型共享。差别主要用于Blender端圆管精度和小构件轻量提示。";
            dialog.CommonButtons = TaskDialogCommonButtons.Cancel;
            dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink1, "快速", "适合全楼、鸟瞰和超大机电模型");
            dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink2, "标准（推荐）", "兼顾管理、渲染质量和性能");
            dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink3, "精细", "适合局部机房和近景特写");
            TaskDialogResult result = dialog.Show();
            if (result == TaskDialogResult.CommandLink1) { profile = ExportProfile.Fast; return true; }
            if (result == TaskDialogResult.CommandLink2) { profile = ExportProfile.Standard; return true; }
            if (result == TaskDialogResult.CommandLink3) { profile = ExportProfile.Fine; return true; }
            profile = ExportProfile.Standard;
            return false;
        }

        private static string ProfileCode(ExportProfile profile)
        {
            switch (profile)
            {
                case ExportProfile.Fast: return "FAST";
                case ExportProfile.Fine: return "FINE";
                default: return "STANDARD";
            }
        }

        private static string GetUniqueDirectory(string requested)
        {
            if (!Directory.Exists(requested) && !File.Exists(requested))
                return requested;

            string parent = Path.GetDirectoryName(requested);
            string name = Path.GetFileNameWithoutExtension(requested);
            for (int index = 1; index < 10000; index++)
            {
                string candidate = Path.Combine(parent, name + "_" + index.ToString("000") + ".slbh");
                if (!Directory.Exists(candidate) && !File.Exists(candidate))
                    return candidate;
            }
            return Path.Combine(parent, name + "_" + DateTime.Now.ToString("yyyyMMdd_HHmmss") + ".slbh");
        }

        private static void WriteJson(string path, BridgeProject project)
        {
            var serializer = new DataContractJsonSerializer(typeof(BridgeProject));
            using (var stream = File.Create(path))
                serializer.WriteObject(stream, project);
        }

        private static string SanitizeFileName(string input)
        {
            if (string.IsNullOrWhiteSpace(input)) return "RevitProject";
            var invalid = Path.GetInvalidFileNameChars();
            var builder = new StringBuilder(input.Length);
            foreach (char c in input)
                builder.Append(Array.IndexOf(invalid, c) >= 0 ? '_' : c);
            string result = builder.ToString().Trim().TrimEnd('.');
            return string.IsNullOrWhiteSpace(result) ? "RevitProject" : result;
        }
    }
}
