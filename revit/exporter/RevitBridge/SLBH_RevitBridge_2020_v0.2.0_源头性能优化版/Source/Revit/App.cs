using Autodesk.Revit.UI;
using System;
using System.Reflection;

namespace SLBH.RevitBridge
{
    public sealed class App : IExternalApplication
    {
        private const string TabName = "SLBH工具";
        private const string PanelName = "Blender桥接";

        public Result OnStartup(UIControlledApplication application)
        {
            try
            {
                try
                {
                    application.CreateRibbonTab(TabName);
                }
                catch
                {
                    // The tab may already exist when other SLBH add-ins are installed.
                }

                RibbonPanel panel = null;
                foreach (RibbonPanel item in application.GetRibbonPanels(TabName))
                {
                    if (string.Equals(item.Name, PanelName, StringComparison.Ordinal))
                    {
                        panel = item;
                        break;
                    }
                }

                if (panel == null)
                    panel = application.CreateRibbonPanel(TabName, PanelName);

                string assemblyPath = Assembly.GetExecutingAssembly().Location;
                PushButtonData data = new PushButtonData(
                    "SLBH_RevitBridge_Export",
                    "导出到\nBlender",
                    assemblyPath,
                    "SLBH.RevitBridge.ExportCommand");
                data.ToolTip = "将当前非透视三维视图以重复族原型共享方式导出为SLBH桥接包，供Blender导入和管理。";
                panel.AddItem(data);

                PushButtonData mepData = new PushButtonData(
                    "SLBH_RevitBridge_MepExport",
                    "导出\n管综数据",
                    assemblyPath,
                    "SLBH.RevitBridge.MepExportCommand");
                mepData.ToolTip = "导出当前三维视图的管线参数、保温、连接关系、管件、标高、轴网、房间和坐标信息，供管综排布工具使用。模型不会被修改。";
                panel.AddItem(mepData);

                return Result.Succeeded;
            }
            catch (Exception ex)
            {
                TaskDialog.Show("SLBH Revit桥接", "插件界面加载失败：\n" + ex.Message);
                return Result.Failed;
            }
        }

        public Result OnShutdown(UIControlledApplication application)
        {
            return Result.Succeeded;
        }
    }
}
