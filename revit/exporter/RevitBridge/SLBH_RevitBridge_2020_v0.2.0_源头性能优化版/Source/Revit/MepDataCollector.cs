using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Architecture;
using Autodesk.Revit.DB.Mechanical;
using Autodesk.Revit.DB.Plumbing;
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;

namespace SLBH.RevitBridge
{
    /// <summary>
    /// Collects the parametric MEP data needed for coordination (sizes, insulation, connectors,
    /// topology, fittings) plus the drawing context (levels, grids, rooms, coordinates).
    /// Element keys match the stable keys written to project.json by ObjExportContext.
    /// </summary>
    internal sealed class MepDataCollector
    {
        private const double FeetToMeters = 0.3048;
        private const int MaxDiagnostics = 5000;

        private static readonly BuiltInCategory[] CurveCategories =
        {
            BuiltInCategory.OST_PipeCurves,
            BuiltInCategory.OST_DuctCurves,
            BuiltInCategory.OST_CableTray,
            BuiltInCategory.OST_Conduit,
            BuiltInCategory.OST_FlexPipeCurves,
            BuiltInCategory.OST_FlexDuctCurves
        };

        private static readonly BuiltInCategory[] FittingCategories =
        {
            BuiltInCategory.OST_PipeFitting,
            BuiltInCategory.OST_DuctFitting,
            BuiltInCategory.OST_CableTrayFitting,
            BuiltInCategory.OST_ConduitFitting
        };

        private static readonly BuiltInCategory[] AccessoryCategories =
        {
            BuiltInCategory.OST_PipeAccessory,
            BuiltInCategory.OST_DuctAccessory
        };

        private static readonly BuiltInCategory[] EquipmentCategories =
        {
            BuiltInCategory.OST_MechanicalEquipment,
            BuiltInCategory.OST_ElectricalEquipment,
            BuiltInCategory.OST_PlumbingFixtures
        };

        private static readonly BuiltInCategory[] TerminalCategories =
        {
            BuiltInCategory.OST_DuctTerminal,
            BuiltInCategory.OST_Sprinklers,
            BuiltInCategory.OST_LightingFixtures,
            BuiltInCategory.OST_ElectricalFixtures
        };

        private static readonly BuiltInCategory[] SupportCategories =
        {
            BuiltInCategory.OST_GenericModel,
            BuiltInCategory.OST_FabricationHangers,
            BuiltInCategory.OST_StructuralFraming
        };

        private readonly Document _hostDoc;
        private readonly View3D _view;
        private readonly bool _includeLinks;
        private readonly MepManifest _manifest;
        private readonly MepData _data;
        private readonly HashSet<string> _seenKeys = new HashSet<string>();

        private bool _boxActive;
        private BoundingBoxXYZ _box;
        private Transform _boxInverse;

        private sealed class Source
        {
            public Document Document;
            public Transform Transform;
            public string ModelKey;
            public int LinkInstanceId;
            public bool IsHost;
        }

        public MepDataCollector(Document hostDoc, View3D view, bool includeLinks, MepManifest manifest, MepData data)
        {
            _hostDoc = hostDoc;
            _view = view;
            _includeLinks = includeLinks;
            _manifest = manifest;
            _data = data;
        }

        public void CollectElements()
        {
            ResolveScope();

            var host = new Source { Document = _hostDoc, Transform = Transform.Identity, ModelKey = "HOST", LinkInstanceId = -1, IsHost = true };
            CollectFromSource(host);

            if (!_includeLinks)
                return;

            foreach (Source link in VisibleLinkSources())
                CollectFromSource(link);
        }

        public void CollectContext()
        {
            CollectCoordinates();

            var sources = new List<Source>();
            sources.Add(new Source { Document = _hostDoc, Transform = Transform.Identity, ModelKey = "HOST", LinkInstanceId = -1, IsHost = true });
            if (_includeLinks)
                sources.AddRange(VisibleLinkSources(false));

            foreach (Source source in sources)
            {
                CollectLevels(source);
                CollectGrids(source);
                CollectRooms(source);
            }

            MepCounts counts = _manifest.Counts;
            counts.Levels = _manifest.Levels.Count;
            counts.Grids = _manifest.Grids.Count;
            counts.Rooms = _manifest.Rooms.Count;
        }

        // ------------------------------------------------------------------ scope

        private void ResolveScope()
        {
            MepScope scope = _manifest.Scope;
            scope.IncludeLinkedModels = _includeLinks;
            scope.HostRule = "visible_in_view_and_intersecting_section_box";
            scope.LinkRule = "all_elements_intersecting_section_box";

            try { _boxActive = _view.IsSectionBoxActive; }
            catch { _boxActive = false; }
            if (!_boxActive)
                return;

            try
            {
                _box = _view.GetSectionBox();
                Transform t = _box.Transform ?? Transform.Identity;
                _boxInverse = t.Inverse;
                scope.SectionBoxActive = true;
                scope.SectionBoxMin = Point(_box.Min);
                scope.SectionBoxMax = Point(_box.Max);
                scope.SectionBoxTransform = TransformArray(t);
            }
            catch (Exception ex)
            {
                _boxActive = false;
                Diag("VIEW", "section_box", ex.Message);
            }
        }

        // Tests a model bounding box (in source coordinates) against the section box.
        private bool IntersectsScope(BoundingBoxXYZ bb, Transform sourceTransform, out bool fullyInside)
        {
            fullyInside = true;
            if (!_boxActive)
                return true;
            if (bb == null)
            {
                fullyInside = false;
                return false;
            }

            Transform bbTransform = bb.Transform ?? Transform.Identity;
            double minX = double.MaxValue, minY = double.MaxValue, minZ = double.MaxValue;
            double maxX = double.MinValue, maxY = double.MinValue, maxZ = double.MinValue;
            foreach (XYZ corner in Corners(bb.Min, bb.Max))
            {
                XYZ host = sourceTransform.OfPoint(bbTransform.OfPoint(corner));
                XYZ local = _boxInverse.OfPoint(host);
                minX = Math.Min(minX, local.X); minY = Math.Min(minY, local.Y); minZ = Math.Min(minZ, local.Z);
                maxX = Math.Max(maxX, local.X); maxY = Math.Max(maxY, local.Y); maxZ = Math.Max(maxZ, local.Z);
            }

            XYZ bmin = _box.Min;
            XYZ bmax = _box.Max;
            bool intersects = maxX >= bmin.X && minX <= bmax.X && maxY >= bmin.Y && minY <= bmax.Y && maxZ >= bmin.Z && minZ <= bmax.Z;
            fullyInside = minX >= bmin.X && maxX <= bmax.X && minY >= bmin.Y && maxY <= bmax.Y && minZ >= bmin.Z && maxZ <= bmax.Z;
            return intersects;
        }

        private bool PointInScope(XYZ hostPoint)
        {
            if (!_boxActive)
                return true;
            XYZ local = _boxInverse.OfPoint(hostPoint);
            const double tol = 1e-6;
            return local.X >= _box.Min.X - tol && local.X <= _box.Max.X + tol
                && local.Y >= _box.Min.Y - tol && local.Y <= _box.Max.Y + tol
                && local.Z >= _box.Min.Z - tol && local.Z <= _box.Max.Z + tol;
        }

        private static IEnumerable<XYZ> Corners(XYZ min, XYZ max)
        {
            yield return new XYZ(min.X, min.Y, min.Z);
            yield return new XYZ(max.X, min.Y, min.Z);
            yield return new XYZ(min.X, max.Y, min.Z);
            yield return new XYZ(max.X, max.Y, min.Z);
            yield return new XYZ(min.X, min.Y, max.Z);
            yield return new XYZ(max.X, min.Y, max.Z);
            yield return new XYZ(min.X, max.Y, max.Z);
            yield return new XYZ(max.X, max.Y, max.Z);
        }

        // ------------------------------------------------------------------ sources

        private List<Source> VisibleLinkSources()
        {
            return VisibleLinkSources(true);
        }

        private List<Source> VisibleLinkSources(bool recordLinks)
        {
            var result = new List<Source>();
            IList<RevitLinkInstance> instances;
            try
            {
                instances = new FilteredElementCollector(_hostDoc, _view.Id)
                    .OfClass(typeof(RevitLinkInstance))
                    .Cast<RevitLinkInstance>()
                    .ToList();
            }
            catch (Exception ex)
            {
                Diag("VIEW", "links", ex.Message);
                return result;
            }

            foreach (RevitLinkInstance instance in instances)
            {
                Document linkDoc = null;
                try { linkDoc = instance.GetLinkDocument(); }
                catch { linkDoc = null; }

                Transform transform = Transform.Identity;
                try { transform = instance.GetTotalTransform() ?? Transform.Identity; }
                catch { transform = Transform.Identity; }

                int instanceId = instance.Id.IntegerValue;
                string modelKey = linkDoc != null
                    ? "LINK_" + ObjExportContext.SafeObjToken(ObjExportContext.StableDocumentId(linkDoc)) + "_I" + instanceId.ToString(CultureInfo.InvariantCulture)
                    : "";

                if (recordLinks)
                {
                    _manifest.Links.Add(new MepLink
                    {
                        SourceModelKey = modelKey,
                        DocumentName = linkDoc != null ? Safe(() => linkDoc.Title) : Safe(() => instance.Name),
                        LinkInstanceId = instanceId,
                        LinkInstanceUniqueId = Safe(() => instance.UniqueId),
                        Transform = TransformArray(transform),
                        Status = linkDoc != null ? "loaded" : "unloaded"
                    });
                }

                if (linkDoc == null)
                    continue;

                result.Add(new Source
                {
                    Document = linkDoc,
                    Transform = transform,
                    ModelKey = modelKey,
                    LinkInstanceId = instanceId,
                    IsHost = false
                });
            }
            return result;
        }

        private FilteredElementCollector Collector(Source source)
        {
            return source.IsHost
                ? new FilteredElementCollector(source.Document, _view.Id)
                : new FilteredElementCollector(source.Document);
        }

        private string KeyOf(Element element, Source source)
        {
            string uniqueId = Safe(() => element.UniqueId);
            if (string.IsNullOrWhiteSpace(uniqueId))
                uniqueId = element.Id.IntegerValue.ToString(CultureInfo.InvariantCulture);
            if (source.IsHost)
                return "HOST|" + uniqueId;
            return source.ModelKey + "|" + source.LinkInstanceId.ToString(CultureInfo.InvariantCulture) + "|" + uniqueId;
        }

        // ------------------------------------------------------------------ elements

        private void CollectFromSource(Source source)
        {
            List<Element> curves;
            List<Element> instances;
            try
            {
                curves = Collector(source)
                    .WhereElementIsNotElementType()
                    .WherePasses(new ElementMulticategoryFilter(CurveCategories))
                    .ToList();

                var familyCategories = new List<BuiltInCategory>();
                familyCategories.AddRange(FittingCategories);
                familyCategories.AddRange(AccessoryCategories);
                familyCategories.AddRange(EquipmentCategories);
                familyCategories.AddRange(TerminalCategories);
                familyCategories.AddRange(SupportCategories);
                instances = Collector(source)
                    .WhereElementIsNotElementType()
                    .OfClass(typeof(FamilyInstance))
                    .WherePasses(new ElementMulticategoryFilter(familyCategories))
                    .ToList();
            }
            catch (Exception ex)
            {
                Diag(source.ModelKey, "collector", ex.Message);
                return;
            }

            foreach (Element element in curves)
            {
                MEPCurve curve = element as MEPCurve;
                if (curve == null)
                    continue;
                try { CollectCurve(curve, source); }
                catch (Exception ex) { Diag(KeyOf(element, source), "curve", ex.Message); }
            }

            foreach (Element element in instances)
            {
                FamilyInstance instance = element as FamilyInstance;
                if (instance == null)
                    continue;
                try { CollectFamilyInstance(instance, source); }
                catch (Exception ex) { Diag(KeyOf(element, source), "family_instance", ex.Message); }
            }
        }

        private void CollectCurve(MEPCurve curve, Source source)
        {
            string key = KeyOf(curve, source);
            BoundingBoxXYZ bb = null;
            try { bb = curve.get_BoundingBox(null); }
            catch { bb = null; }
            bool fullyInside;
            if (!IntersectsScope(bb, source.Transform, out fullyInside))
                return;
            if (!_seenKeys.Add(key))
                return;

            var record = new MepCurveRecord();
            FillCommon(record, curve, source, key, bb);
            record.Kind = CurveKind(curve);

            // Centerline
            LocationCurve location = curve.Location as LocationCurve;
            Curve line = location != null ? location.Curve : null;
            if (line != null)
            {
                XYZ start = source.Transform.OfPoint(line.GetEndPoint(0));
                XYZ end = source.Transform.OfPoint(line.GetEndPoint(1));
                record.Start = Point(start);
                record.End = Point(end);
                record.LengthMeters = line.Length * FeetToMeters;
                record.CurveKind = line is Line ? "line" : line.GetType().Name.ToLowerInvariant();
                record.CrossesScopeBoundary = !PointInScope(start) || !PointInScope(end);
            }
            else
            {
                record.CurveKind = "none";
                Diag(key, "location", "MEP curve has no location curve.");
            }

            IList<XYZ> flexPoints = FlexPoints(curve);
            if (flexPoints != null && flexPoints.Count > 0)
                record.Points = flexPoints.Select(p => Point(source.Transform.OfPoint(p))).ToList();

            // Section size
            ReadCurveSize(curve, record, key);
            record.Slope = Param(curve, BuiltInParameter.RBS_PIPE_SLOPE, BuiltInParameter.RBS_DUCT_SLOPE);

            // Reference level and offsets (Revit semantics, used for write-back)
            try
            {
                Level level = curve.ReferenceLevel;
                if (level != null)
                {
                    record.ReferenceLevel = level.Name;
                    record.ReferenceLevelElevationMeters = level.Elevation * FeetToMeters;
                }
            }
            catch (Exception ex) { Diag(key, "reference_level", ex.Message); }
            record.StartOffsetMeters = Param(curve, BuiltInParameter.RBS_START_OFFSET_PARAM) * FeetToMeters;
            record.EndOffsetMeters = Param(curve, BuiltInParameter.RBS_END_OFFSET_PARAM) * FeetToMeters;

            // Connectors and section axes
            ConnectorManager manager = null;
            try { manager = curve.ConnectorManager; }
            catch { manager = null; }
            ReadConnectors(manager, record, source, key, curve.Id);
            SetSectionAxes(manager, record, source);

            record.Fingerprint = CurveFingerprint(record);
            _data.Curves.Add(record);
            _manifest.Counts.Curves++;
            if (record.CrossesScopeBoundary)
                _manifest.Counts.CrossingScopeBoundary++;
            if (record.InsulationThicknessMeters > 0)
                _manifest.Counts.WithInsulation++;
        }

        private void CollectFamilyInstance(FamilyInstance instance, Source source)
        {
            string key = KeyOf(instance, source);
            ConnectorManager manager = null;
            try { manager = instance.MEPModel != null ? instance.MEPModel.ConnectorManager : null; }
            catch { manager = null; }
            bool hasConnectors = manager != null && manager.Connectors != null && manager.Connectors.Size > 0;

            string category = instance.Category != null ? instance.Category.Name : "";
            string family = FamilyName(instance, source.Document);
            string type = TypeName(instance, source.Document);
            bool supportCandidate = IsSupportCandidate(instance, category, family, type);
            int categoryId = instance.Category != null ? instance.Category.Id.IntegerValue : 0;

            // Generic models / framing are only relevant when they look like supports or carry MEP connectors.
            if (IsOneOf(categoryId, SupportCategories) && !supportCandidate && !hasConnectors)
                return;

            BoundingBoxXYZ bb = null;
            try { bb = instance.get_BoundingBox(null); }
            catch { bb = null; }
            bool fullyInside;
            if (!IntersectsScope(bb, source.Transform, out fullyInside))
                return;
            if (!_seenKeys.Add(key))
                return;

            var record = new MepFamilyRecord();
            FillCommon(record, instance, source, key, bb);
            record.Kind = FamilyKind(categoryId, supportCandidate);
            record.IsSupportCandidate = supportCandidate;
            record.CrossesScopeBoundary = !fullyInside;

            try
            {
                Transform t = instance.GetTransform() ?? Transform.Identity;
                record.Transform = TransformArray(source.Transform.Multiply(t));
            }
            catch (Exception ex) { Diag(key, "transform", ex.Message); }

            try
            {
                MechanicalFitting fitting = instance.MEPModel as MechanicalFitting;
                if (fitting != null)
                    record.PartType = fitting.PartType.ToString();
            }
            catch (Exception ex) { Diag(key, "part_type", ex.Message); }
            if (string.IsNullOrEmpty(record.PartType))
                record.PartType = FamilyPartType(instance);

            record.AngleRadians = ParamByName(instance, "Angle", "角度");

            try
            {
                Element hostElement = instance.Host;
                if (hostElement != null && hostElement.Document.Equals(source.Document))
                    record.HostKey = KeyOf(hostElement, source);
            }
            catch { }

            ReadConnectors(manager, record, source, key, instance.Id);
            record.Fingerprint = FamilyFingerprint(record);

            _data.FamilyInstances.Add(record);
            _manifest.Counts.FamilyInstances++;
            if (supportCandidate)
                _manifest.Counts.SupportCandidates++;
            if (record.CrossesScopeBoundary)
                _manifest.Counts.CrossingScopeBoundary++;
            if (record.InsulationThicknessMeters > 0)
                _manifest.Counts.WithInsulation++;
        }

        private void FillCommon(MepElementRecord record, Element element, Source source, string key, BoundingBoxXYZ bb)
        {
            Document doc = source.Document;
            record.Key = key;
            record.ElementId = element.Id.IntegerValue;
            record.UniqueId = Safe(() => element.UniqueId);
            record.SourceModelKey = source.ModelKey;
            record.Category = element.Category != null ? element.Category.Name : "";
            record.BuiltInCategory = element.Category != null ? ((BuiltInCategory)element.Category.Id.IntegerValue).ToString() : "";
            record.Family = FamilyName(element, doc);
            record.Type = TypeName(element, doc);
            record.Level = LevelName(element, doc);
            record.Workset = WorksetName(element, doc);
            try { record.Pinned = element.Pinned; }
            catch { record.Pinned = false; }

            ReadSystem(element, doc, record);
            record.ServiceType = Text(element, BuiltInParameter.RBS_CTC_SERVICE_TYPE);
            record.SizeText = Text(element, BuiltInParameter.RBS_CALCULATED_SIZE);
            ReadInsulation(element, doc, record, key);

            if (bb != null)
            {
                XYZ min, max;
                TransformedBounds(bb, source.Transform, out min, out max);
                record.BoundingBoxMin = Point(min);
                record.BoundingBoxMax = Point(max);
            }
        }

        // ------------------------------------------------------------------ element details

        private static string CurveKind(MEPCurve curve)
        {
            if (curve is Pipe) return "pipe";
            if (curve is Duct) return "duct";
            if (curve is FlexPipe) return "flex_pipe";
            if (curve is FlexDuct) return "flex_duct";
            if (curve is Autodesk.Revit.DB.Electrical.CableTray) return "cable_tray";
            if (curve is Autodesk.Revit.DB.Electrical.Conduit) return "conduit";
            return "mep_curve";
        }

        private static string FamilyKind(int categoryId, bool supportCandidate)
        {
            if (supportCandidate) return "support";
            if (IsOneOf(categoryId, FittingCategories)) return "fitting";
            if (IsOneOf(categoryId, AccessoryCategories)) return "accessory";
            if (IsOneOf(categoryId, EquipmentCategories)) return "equipment";
            if (IsOneOf(categoryId, TerminalCategories)) return "terminal";
            return "other";
        }

        private static bool IsOneOf(int categoryId, BuiltInCategory[] categories)
        {
            foreach (BuiltInCategory category in categories)
            {
                if ((int)category == categoryId)
                    return true;
            }
            return false;
        }

        private static bool IsSupportCandidate(FamilyInstance instance, string category, string family, string type)
        {
            if (instance.Category != null && instance.Category.Id.IntegerValue == (int)BuiltInCategory.OST_FabricationHangers)
                return true;
            string text = (category + " " + family + " " + type).ToLowerInvariant();
            return ContainsAny(text, "hanger", "support", "支吊架", "吊架", "支架", "托架", "抗震");
        }

        // Cable tray / conduit fittings and accessories do not expose MechanicalFitting;
        // the part type is stored on the family.
        private static string FamilyPartType(FamilyInstance instance)
        {
            try
            {
                Family family = instance.Symbol != null ? instance.Symbol.Family : null;
                Parameter parameter = family != null ? family.get_Parameter(BuiltInParameter.FAMILY_CONTENT_PART_TYPE) : null;
                if (parameter == null || !parameter.HasValue || parameter.StorageType != StorageType.Integer)
                    return "";
                PartType partType = (PartType)parameter.AsInteger();
                return partType == PartType.Undefined || partType == PartType.Normal ? "" : partType.ToString();
            }
            catch { return ""; }
        }

        private static IList<XYZ> FlexPoints(MEPCurve curve)
        {
            try
            {
                FlexPipe flexPipe = curve as FlexPipe;
                if (flexPipe != null)
                    return flexPipe.Points;
                FlexDuct flexDuct = curve as FlexDuct;
                if (flexDuct != null)
                    return flexDuct.Points;
            }
            catch { }
            return null;
        }

        private void ReadCurveSize(MEPCurve curve, MepCurveRecord record, string key)
        {
            string shape = EndConnectorShape(curve);

            if (curve is Pipe || curve is FlexPipe)
            {
                record.Shape = "round";
                record.NominalDiameterMeters = Param(curve, BuiltInParameter.RBS_PIPE_DIAMETER_PARAM) * FeetToMeters;
                record.OuterDiameterMeters = Param(curve, BuiltInParameter.RBS_PIPE_OUTER_DIAMETER) * FeetToMeters;
                record.InnerDiameterMeters = Param(curve, BuiltInParameter.RBS_PIPE_INNER_DIAM_PARAM) * FeetToMeters;
                if (record.OuterDiameterMeters <= 0)
                    record.OuterDiameterMeters = SafeDouble(() => curve.Diameter) * FeetToMeters;
            }
            else if (curve is Autodesk.Revit.DB.Electrical.Conduit)
            {
                record.Shape = "round";
                record.NominalDiameterMeters = Param(curve, BuiltInParameter.RBS_CONDUIT_DIAMETER_PARAM) * FeetToMeters;
                record.OuterDiameterMeters = Param(curve, BuiltInParameter.RBS_CONDUIT_OUTER_DIAM_PARAM) * FeetToMeters;
                if (record.OuterDiameterMeters <= 0)
                    record.OuterDiameterMeters = SafeDouble(() => curve.Diameter) * FeetToMeters;
            }
            else if (curve is Autodesk.Revit.DB.Electrical.CableTray)
            {
                record.Shape = "rectangular";
                record.WidthMeters = Param(curve, BuiltInParameter.RBS_CABLETRAY_WIDTH_PARAM) * FeetToMeters;
                record.HeightMeters = Param(curve, BuiltInParameter.RBS_CABLETRAY_HEIGHT_PARAM) * FeetToMeters;
            }
            else
            {
                // Ducts (rigid and flex): shape from the end connector.
                record.Shape = string.IsNullOrEmpty(shape) ? "unknown" : shape;
                if (record.Shape == "round")
                {
                    record.OuterDiameterMeters = Param(curve, BuiltInParameter.RBS_CURVE_DIAMETER_PARAM) * FeetToMeters;
                    if (record.OuterDiameterMeters <= 0)
                        record.OuterDiameterMeters = SafeDouble(() => curve.Diameter) * FeetToMeters;
                }
                else
                {
                    record.WidthMeters = Param(curve, BuiltInParameter.RBS_CURVE_WIDTH_PARAM) * FeetToMeters;
                    record.HeightMeters = Param(curve, BuiltInParameter.RBS_CURVE_HEIGHT_PARAM) * FeetToMeters;
                    if (record.WidthMeters <= 0)
                        record.WidthMeters = SafeDouble(() => curve.Width) * FeetToMeters;
                    if (record.HeightMeters <= 0)
                        record.HeightMeters = SafeDouble(() => curve.Height) * FeetToMeters;
                }
            }

            bool hasSize = record.OuterDiameterMeters > 0 || (record.WidthMeters > 0 && record.HeightMeters > 0);
            if (!hasSize)
                Diag(key, "size", "No usable section size found.");
        }

        private static string EndConnectorShape(MEPCurve curve)
        {
            try
            {
                foreach (Connector connector in curve.ConnectorManager.Connectors)
                {
                    if (connector.ConnectorType == ConnectorType.End)
                        return ShapeName(connector.Shape);
                }
            }
            catch { }
            return "";
        }

        private static string ShapeName(ConnectorProfileType shape)
        {
            switch (shape)
            {
                case ConnectorProfileType.Round: return "round";
                case ConnectorProfileType.Rectangular: return "rectangular";
                case ConnectorProfileType.Oval: return "oval";
                default: return shape.ToString().ToLowerInvariant();
            }
        }

        private void ReadInsulation(Element element, Document doc, MepElementRecord record, string key)
        {
            record.InsulationThicknessMeters = Param(element, BuiltInParameter.RBS_REFERENCE_INSULATION_THICKNESS) * FeetToMeters;
            record.InsulationType = Text(element, BuiltInParameter.RBS_REFERENCE_INSULATION_TYPE);
            record.LiningThicknessMeters = Param(element, BuiltInParameter.RBS_REFERENCE_LINING_THICKNESS) * FeetToMeters;

            if (record.InsulationThicknessMeters > 0 || !CanHostInsulation(element))
                return;

            // Fallback: read the insulation elements hosted by this element.
            try
            {
                foreach (ElementId id in InsulationLiningBase.GetInsulationIds(doc, element.Id))
                {
                    InsulationLiningBase insulation = doc.GetElement(id) as InsulationLiningBase;
                    if (insulation == null)
                        continue;
                    double thickness = insulation.Thickness * FeetToMeters;
                    if (thickness > record.InsulationThicknessMeters)
                    {
                        record.InsulationThicknessMeters = thickness;
                        Element insulationType = doc.GetElement(insulation.GetTypeId());
                        if (insulationType != null && string.IsNullOrWhiteSpace(record.InsulationType))
                            record.InsulationType = insulationType.Name;
                    }
                }
            }
            catch (ArgumentException) { } // not a valid insulation host
            catch (Exception ex) { Diag(key, "insulation", ex.Message); }
        }

        private static bool CanHostInsulation(Element element)
        {
            if (element is Pipe || element is Duct || element is FlexPipe || element is FlexDuct)
                return true;
            int categoryId = element.Category != null ? element.Category.Id.IntegerValue : 0;
            return categoryId == (int)BuiltInCategory.OST_PipeFitting
                || categoryId == (int)BuiltInCategory.OST_DuctFitting
                || categoryId == (int)BuiltInCategory.OST_PipeAccessory
                || categoryId == (int)BuiltInCategory.OST_DuctAccessory;
        }

        private void ReadSystem(Element element, Document doc, MepElementRecord record)
        {
            MEPSystem system = null;
            MEPCurve curve = element as MEPCurve;
            if (curve != null)
            {
                try { system = curve.MEPSystem; }
                catch { system = null; }
            }
            if (system == null)
            {
                FamilyInstance instance = element as FamilyInstance;
                try
                {
                    ConnectorManager manager = instance != null && instance.MEPModel != null ? instance.MEPModel.ConnectorManager : null;
                    if (manager != null)
                    {
                        foreach (Connector connector in manager.Connectors)
                        {
                            if (connector != null && connector.MEPSystem != null)
                            {
                                system = connector.MEPSystem;
                                break;
                            }
                        }
                    }
                }
                catch { }
            }

            MEPSystemType systemType = null;
            if (system != null)
            {
                record.SystemName = Safe(() => system.Name);
                try { systemType = doc.GetElement(system.GetTypeId()) as MEPSystemType; }
                catch { systemType = null; }
            }
            if (systemType == null)
                systemType = SystemTypeFromParameters(element, doc);

            if (systemType != null)
            {
                record.SystemTypeName = Safe(() => systemType.Name);
                record.SystemClassification = Safe(() => systemType.SystemClassification.ToString());
                record.SystemAbbreviation = Safe(() => systemType.Abbreviation);
            }

            if (string.IsNullOrWhiteSpace(record.SystemName))
                record.SystemName = Text(element, BuiltInParameter.RBS_SYSTEM_NAME_PARAM);
            if (string.IsNullOrWhiteSpace(record.SystemClassification))
                record.SystemClassification = Text(element, BuiltInParameter.RBS_SYSTEM_CLASSIFICATION_PARAM);
            if (string.IsNullOrWhiteSpace(record.SystemAbbreviation))
                record.SystemAbbreviation = Text(element, BuiltInParameter.RBS_SYSTEM_ABBREVIATION_PARAM);

            record.SystemCode = ObjExportContext.ClassifySystemCode(record.SystemName, record.SystemTypeName, record.SystemAbbreviation, record.SystemClassification);
        }

        private static MEPSystemType SystemTypeFromParameters(Element element, Document doc)
        {
            BuiltInParameter[] parameters = { BuiltInParameter.RBS_DUCT_SYSTEM_TYPE_PARAM, BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM };
            foreach (BuiltInParameter bip in parameters)
            {
                try
                {
                    Parameter parameter = element.get_Parameter(bip);
                    if (parameter == null || parameter.StorageType != StorageType.ElementId)
                        continue;
                    MEPSystemType result = doc.GetElement(parameter.AsElementId()) as MEPSystemType;
                    if (result != null)
                        return result;
                }
                catch { }
            }
            return null;
        }

        private void ReadConnectors(ConnectorManager manager, MepElementRecord record, Source source, string key, ElementId ownerId)
        {
            if (manager == null)
                return;

            ConnectorSet connectors;
            try { connectors = manager.Connectors; }
            catch (Exception ex)
            {
                Diag(key, "connectors", ex.Message);
                return;
            }

            foreach (Connector connector in connectors)
            {
                if (connector == null)
                    continue;
                ConnectorType connectorType;
                try { connectorType = connector.ConnectorType; }
                catch { continue; }
                if (connectorType == ConnectorType.Logical)
                    continue;

                var item = new MepConnectorRecord();
                item.Id = SafeInt(() => connector.Id);
                item.ConnectorType = connectorType.ToString();
                item.Domain = Safe(() => connector.Domain.ToString());

                ConnectorProfileType shape = ConnectorProfileType.Invalid;
                try { shape = connector.Shape; }
                catch { }
                item.Shape = ShapeName(shape);

                try { item.Origin = Point(source.Transform.OfPoint(connector.Origin)); }
                catch (Exception ex) { Diag(key, "connector_origin", ex.Message); }
                try
                {
                    Transform cs = connector.CoordinateSystem;
                    item.Direction = Vector(source.Transform.OfVector(cs.BasisZ));
                    item.XAxis = Vector(source.Transform.OfVector(cs.BasisX));
                }
                catch { }

                if (shape == ConnectorProfileType.Round)
                    item.DiameterMeters = SafeDouble(() => connector.Radius) * 2.0 * FeetToMeters;
                else if (shape == ConnectorProfileType.Rectangular || shape == ConnectorProfileType.Oval)
                {
                    item.WidthMeters = SafeDouble(() => connector.Width) * FeetToMeters;
                    item.HeightMeters = SafeDouble(() => connector.Height) * FeetToMeters;
                }

                try
                {
                    foreach (Connector other in connector.AllRefs)
                    {
                        if (other == null || other.Owner == null)
                            continue;
                        if (other.Owner.Id == ownerId)
                            continue;
                        if (other.Owner is MEPSystem)
                            continue;
                        ConnectorType otherType;
                        try { otherType = other.ConnectorType; }
                        catch { continue; }
                        if (otherType == ConnectorType.Logical)
                            continue;
                        item.Connected.Add(new MepConnectionRef
                        {
                            Key = KeyOf(other.Owner, source),
                            ConnectorId = SafeInt(() => other.Id)
                        });
                        _manifest.Counts.Connections++;
                    }
                }
                catch (Exception ex) { Diag(key, "connector_refs", ex.Message); }

                record.Connectors.Add(item);
                _manifest.Counts.Connectors++;
            }
        }

        private static void SetSectionAxes(ConnectorManager manager, MepCurveRecord record, Source source)
        {
            if (manager == null)
                return;
            try
            {
                foreach (Connector connector in manager.Connectors)
                {
                    if (connector.ConnectorType != ConnectorType.End)
                        continue;
                    Transform cs = connector.CoordinateSystem;
                    record.SectionXAxis = Vector(source.Transform.OfVector(cs.BasisX));
                    record.SectionYAxis = Vector(source.Transform.OfVector(cs.BasisY));
                    return;
                }
            }
            catch { }
        }

        // ------------------------------------------------------------------ context

        private void CollectCoordinates()
        {
            try
            {
                ProjectLocation location = _hostDoc.ActiveProjectLocation;
                if (location != null)
                {
                    ProjectPosition position = location.GetProjectPosition(XYZ.Zero);
                    var result = new MepProjectLocation
                    {
                        Name = Safe(() => location.Name),
                        EastWestMeters = position.EastWest * FeetToMeters,
                        NorthSouthMeters = position.NorthSouth * FeetToMeters,
                        ElevationMeters = position.Elevation * FeetToMeters,
                        AngleRadians = position.Angle
                    };
                    try { result.InternalToShared = TransformArray(location.GetTotalTransform()); }
                    catch (Exception ex) { Diag("HOST", "internal_to_shared", ex.Message); }
                    _manifest.ProjectLocation = result;
                }
            }
            catch (Exception ex) { Diag("HOST", "project_location", ex.Message); }

            try
            {
                foreach (BasePoint point in new FilteredElementCollector(_hostDoc).OfClass(typeof(BasePoint)).Cast<BasePoint>())
                {
                    var record = new MepBasePoint
                    {
                        Kind = point.IsShared ? "survey" : "project",
                        EastWestMeters = Param(point, BuiltInParameter.BASEPOINT_EASTWEST_PARAM) * FeetToMeters,
                        NorthSouthMeters = Param(point, BuiltInParameter.BASEPOINT_NORTHSOUTH_PARAM) * FeetToMeters,
                        ElevationMeters = Param(point, BuiltInParameter.BASEPOINT_ELEVATION_PARAM) * FeetToMeters,
                        AngleToTrueNorthRadians = Param(point, BuiltInParameter.BASEPOINT_ANGLETON_PARAM)
                    };
                    try { record.Position = Point(point.Position); }
                    catch (Exception ex) { Diag("HOST", "base_point_position", ex.Message); }
                    _manifest.BasePoints.Add(record);
                }
            }
            catch (Exception ex) { Diag("HOST", "base_points", ex.Message); }
        }

        private void CollectLevels(Source source)
        {
            try
            {
                foreach (Level level in new FilteredElementCollector(source.Document).OfClass(typeof(Level)).Cast<Level>())
                {
                    // ProjectElevation is measured from the internal origin and matches the geometry.
                    // Elevation is relative to the level type's elevation base (project base point or
                    // survey point) and is only the value displayed in Revit.
                    double internalElevation = level.ProjectElevation;
                    // Levels are horizontal planes: only the Z of the link transform applies.
                    double hostElevation = source.Transform.OfPoint(new XYZ(0, 0, internalElevation)).Z;
                    _manifest.Levels.Add(new MepLevel
                    {
                        SourceModelKey = source.ModelKey,
                        UniqueId = Safe(() => level.UniqueId),
                        Name = Safe(() => level.Name),
                        ElevationMeters = Round(hostElevation * FeetToMeters),
                        DisplayElevationMeters = Round(SafeDouble(() => level.Elevation) * FeetToMeters)
                    });
                }
            }
            catch (Exception ex) { Diag(source.ModelKey, "levels", ex.Message); }
        }

        private void CollectGrids(Source source)
        {
            try
            {
                foreach (Grid grid in new FilteredElementCollector(source.Document).OfClass(typeof(Grid)).Cast<Grid>())
                {
                    Curve curve = grid.Curve;
                    if (curve == null)
                        continue;
                    var record = new MepGrid
                    {
                        SourceModelKey = source.ModelKey,
                        UniqueId = Safe(() => grid.UniqueId),
                        Name = Safe(() => grid.Name),
                        Start = Point(source.Transform.OfPoint(curve.GetEndPoint(0))),
                        End = Point(source.Transform.OfPoint(curve.GetEndPoint(1)))
                    };
                    Arc arc = curve as Arc;
                    if (curve is Line)
                        record.CurveKind = "line";
                    else if (arc != null)
                    {
                        record.CurveKind = "arc";
                        record.Center = Point(source.Transform.OfPoint(arc.Center));
                        record.RadiusMeters = arc.Radius * FeetToMeters;
                    }
                    else
                        record.CurveKind = curve.GetType().Name.ToLowerInvariant();
                    _manifest.Grids.Add(record);
                }
            }
            catch (Exception ex) { Diag(source.ModelKey, "grids", ex.Message); }
        }

        private void CollectRooms(Source source)
        {
            IList<Element> rooms;
            try
            {
                rooms = new FilteredElementCollector(source.Document)
                    .OfCategory(BuiltInCategory.OST_Rooms)
                    .WhereElementIsNotElementType()
                    .ToElements();
            }
            catch (Exception ex)
            {
                Diag(source.ModelKey, "rooms", ex.Message);
                return;
            }

            var options = new SpatialElementBoundaryOptions();
            foreach (Element element in rooms)
            {
                Room room = element as Room;
                if (room == null)
                    continue;
                try
                {
                    if (room.Area <= 0)
                        continue; // unplaced or not enclosed
                    BoundingBoxXYZ bb = room.get_BoundingBox(null);
                    bool fullyInside;
                    if (!IntersectsScope(bb, source.Transform, out fullyInside))
                        continue;

                    var record = new MepRoom
                    {
                        SourceModelKey = source.ModelKey,
                        UniqueId = Safe(() => room.UniqueId),
                        Name = Text(room, BuiltInParameter.ROOM_NAME),
                        Number = Safe(() => room.Number),
                        Level = LevelName(room, source.Document)
                    };
                    if (bb != null)
                    {
                        XYZ min, max;
                        TransformedBounds(bb, source.Transform, out min, out max);
                        record.BoundingBoxMin = Point(min);
                        record.BoundingBoxMax = Point(max);
                    }

                    IList<IList<BoundarySegment>> loops = room.GetBoundarySegments(options);
                    if (loops != null)
                    {
                        foreach (IList<BoundarySegment> loop in loops)
                        {
                            var points = new List<double[]>();
                            foreach (BoundarySegment segment in loop)
                            {
                                IList<XYZ> tessellated = segment.GetCurve().Tessellate();
                                // Skip the last point of each segment: it is the first point of the next.
                                for (int i = 0; i < tessellated.Count - 1; i++)
                                    points.Add(Point(source.Transform.OfPoint(tessellated[i])));
                            }
                            if (points.Count >= 3)
                                record.Boundary.Add(points);
                        }
                    }
                    _manifest.Rooms.Add(record);
                }
                catch (Exception ex) { Diag(KeyOf(room, source), "room", ex.Message); }
            }
        }

        // ------------------------------------------------------------------ fingerprints

        private static string CurveFingerprint(MepCurveRecord r)
        {
            var b = new StringBuilder();
            b.Append(r.Kind).Append('|').Append(r.Type).Append('|').Append(r.SystemTypeName).Append('|');
            AppendPoint(b, r.Start); AppendPoint(b, r.End);
            AppendMm(b, r.OuterDiameterMeters); AppendMm(b, r.WidthMeters); AppendMm(b, r.HeightMeters);
            AppendMm(b, r.InsulationThicknessMeters);
            b.Append(Math.Round(r.Slope, 5).ToString("R", CultureInfo.InvariantCulture));
            return ObjExportContext.ShortHash(b.ToString());
        }

        private static string FamilyFingerprint(MepFamilyRecord r)
        {
            var b = new StringBuilder();
            b.Append(r.Family).Append('|').Append(r.Type).Append('|').Append(r.SystemTypeName).Append('|');
            if (r.Transform != null)
            {
                foreach (double value in r.Transform)
                    b.Append(Math.Round(value, 3).ToString("R", CultureInfo.InvariantCulture)).Append(',');
            }
            AppendMm(b, r.InsulationThicknessMeters);
            return ObjExportContext.ShortHash(b.ToString());
        }

        private static void AppendPoint(StringBuilder b, double[] p)
        {
            if (p == null) { b.Append("null;"); return; }
            foreach (double value in p) AppendMm(b, value);
            b.Append(';');
        }

        // Rounded to 1 mm so that numerical noise does not change the fingerprint.
        private static void AppendMm(StringBuilder b, double meters)
        {
            b.Append(Math.Round(meters * 1000.0).ToString("R", CultureInfo.InvariantCulture)).Append(',');
        }

        // ------------------------------------------------------------------ helpers

        private void Diag(string key, string field, string message)
        {
            if (_manifest.Diagnostics.Count >= MaxDiagnostics)
            {
                _manifest.Counts.DiagnosticsDropped++;
                return;
            }
            _manifest.Diagnostics.Add(new MepDiagnostic { Key = key, Field = field, Message = message });
            _manifest.Counts.Diagnostics = _manifest.Diagnostics.Count;
        }

        private static string FamilyName(Element element, Document doc)
        {
            try
            {
                FamilyInstance instance = element as FamilyInstance;
                if (instance != null && instance.Symbol != null && instance.Symbol.Family != null)
                    return instance.Symbol.Family.Name;
                Element type = doc.GetElement(element.GetTypeId());
                if (type == null)
                    return "";
                Parameter parameter = type.get_Parameter(BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM);
                string name = parameter != null ? parameter.AsString() : null;
                return string.IsNullOrWhiteSpace(name) ? type.Name ?? "" : name;
            }
            catch { return ""; }
        }

        private static string TypeName(Element element, Document doc)
        {
            try
            {
                Element type = doc.GetElement(element.GetTypeId());
                return type != null ? type.Name ?? "" : element.Name ?? "";
            }
            catch { return ""; }
        }

        private static string LevelName(Element element, Document doc)
        {
            try
            {
                ElementId levelId = element.LevelId;
                if (levelId != null && levelId != ElementId.InvalidElementId)
                {
                    Element level = doc.GetElement(levelId);
                    if (level != null)
                        return level.Name;
                }
                BuiltInParameter[] candidates =
                {
                    BuiltInParameter.RBS_START_LEVEL_PARAM,
                    BuiltInParameter.FAMILY_LEVEL_PARAM,
                    BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM,
                    BuiltInParameter.SCHEDULE_LEVEL_PARAM
                };
                foreach (BuiltInParameter bip in candidates)
                {
                    Parameter parameter = element.get_Parameter(bip);
                    if (parameter != null && parameter.StorageType == StorageType.ElementId)
                    {
                        Element level = doc.GetElement(parameter.AsElementId());
                        if (level != null)
                            return level.Name;
                    }
                }
            }
            catch { }
            return "";
        }

        private static string WorksetName(Element element, Document doc)
        {
            try
            {
                if (!doc.IsWorkshared)
                    return "";
                Workset workset = doc.GetWorksetTable().GetWorkset(element.WorksetId);
                return workset != null ? workset.Name : "";
            }
            catch { return ""; }
        }

        private static double Param(Element element, params BuiltInParameter[] ids)
        {
            foreach (BuiltInParameter id in ids)
            {
                try
                {
                    Parameter parameter = element.get_Parameter(id);
                    if (parameter != null && parameter.HasValue && parameter.StorageType == StorageType.Double)
                        return parameter.AsDouble();
                }
                catch { }
            }
            return 0.0;
        }

        private static double ParamByName(Element element, params string[] names)
        {
            foreach (string name in names)
            {
                try
                {
                    Parameter parameter = element.LookupParameter(name);
                    if (parameter != null && parameter.HasValue && parameter.StorageType == StorageType.Double)
                        return parameter.AsDouble();
                }
                catch { }
            }
            return 0.0;
        }

        private static string Text(Element element, BuiltInParameter id)
        {
            try
            {
                Parameter parameter = element.get_Parameter(id);
                if (parameter == null || !parameter.HasValue)
                    return "";
                string value = parameter.StorageType == StorageType.String ? parameter.AsString() : parameter.AsValueString();
                return value ?? "";
            }
            catch { return ""; }
        }

        private static void TransformedBounds(BoundingBoxXYZ bb, Transform sourceTransform, out XYZ min, out XYZ max)
        {
            Transform bbTransform = bb.Transform ?? Transform.Identity;
            double minX = double.MaxValue, minY = double.MaxValue, minZ = double.MaxValue;
            double maxX = double.MinValue, maxY = double.MinValue, maxZ = double.MinValue;
            foreach (XYZ corner in Corners(bb.Min, bb.Max))
            {
                XYZ p = sourceTransform.OfPoint(bbTransform.OfPoint(corner));
                minX = Math.Min(minX, p.X); minY = Math.Min(minY, p.Y); minZ = Math.Min(minZ, p.Z);
                maxX = Math.Max(maxX, p.X); maxY = Math.Max(maxY, p.Y); maxZ = Math.Max(maxZ, p.Z);
            }
            min = new XYZ(minX, minY, minZ);
            max = new XYZ(maxX, maxY, maxZ);
        }

        private static double[] Point(XYZ p)
        {
            if (p == null)
                return null;
            return new[] { Round(p.X * FeetToMeters), Round(p.Y * FeetToMeters), Round(p.Z * FeetToMeters) };
        }

        private static double[] Vector(XYZ v)
        {
            if (v == null)
                return null;
            return new[] { Math.Round(v.X, 9), Math.Round(v.Y, 9), Math.Round(v.Z, 9) };
        }

        // 1 micron is well below modelling tolerance and keeps mep.json compact.
        private static double Round(double meters)
        {
            return Math.Round(meters, 6);
        }

        private static double[] TransformArray(Transform t)
        {
            Transform x = t ?? Transform.Identity;
            return new[]
            {
                x.BasisX.X, x.BasisY.X, x.BasisZ.X, Round(x.Origin.X * FeetToMeters),
                x.BasisX.Y, x.BasisY.Y, x.BasisZ.Y, Round(x.Origin.Y * FeetToMeters),
                x.BasisX.Z, x.BasisY.Z, x.BasisZ.Z, Round(x.Origin.Z * FeetToMeters),
                0.0, 0.0, 0.0, 1.0
            };
        }

        private static bool ContainsAny(string source, params string[] values)
        {
            if (source == null)
                return false;
            return values.Any(value => source.IndexOf(value, StringComparison.OrdinalIgnoreCase) >= 0);
        }

        private static string Safe(Func<string> getter)
        {
            try { return getter() ?? ""; }
            catch { return ""; }
        }

        private static double SafeDouble(Func<double> getter)
        {
            try { return getter(); }
            catch { return 0.0; }
        }

        private static int SafeInt(Func<int> getter)
        {
            try { return getter(); }
            catch { return -1; }
        }
    }
}
