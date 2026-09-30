using Autodesk.Revit.DB;

namespace SLBH.RevitBridge
{
    internal static class RevitApiCompat
    {
        public static void ExportView(CustomExporter exporter, View3D view)
        {
#if REVIT2020
            exporter.Export((View)view);
#else
            exporter.Export(view);
#endif
        }
    }
}
