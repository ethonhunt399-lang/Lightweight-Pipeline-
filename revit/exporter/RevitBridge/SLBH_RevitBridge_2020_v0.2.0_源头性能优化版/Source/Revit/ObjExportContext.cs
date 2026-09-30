using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Mechanical;
using Autodesk.Revit.DB.Plumbing;
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;

namespace SLBH.RevitBridge
{
    /// <summary>
    /// Revit 2019/2020 optimized exporter.
    /// Unique BIM geometry is written to model.obj. Repeated FamilyInstance geometry is normalized
    /// to local coordinates and written once to prototypes.obj; each instance keeps its own transform
    /// and BIM metadata in project.json.
    /// </summary>
    public sealed class ObjExportContext : IExportContext, IDisposable
    {
        private const double FeetToMeters = 0.3048;
        private readonly Document _doc;
        private readonly View3D _view;
        private readonly StreamWriter _modelWriter;
        private readonly StreamWriter _prototypeWriter;
        private readonly BridgeProject _project;
        private readonly ExportProfile _profile;
        private readonly BridgeExportOptions _options;
        private readonly Stack<Transform> _transforms = new Stack<Transform>();
        private readonly Stack<LinkExportContext> _linkContexts = new Stack<LinkExportContext>();
        private readonly HashSet<string> _seenElements = new HashSet<string>();
        private readonly HashSet<string> _seenMaterials = new HashSet<string>();
        private readonly Dictionary<string, BridgePrototype> _prototypes = new Dictionary<string, BridgePrototype>();
        private readonly Dictionary<string, RevitLinkInstance> _resolvedLinkInstances = new Dictionary<string, RevitLinkInstance>();

        private Element _currentElement;
        private BridgeElement _currentRecord;
        private ElementMeshBuffer _buffer;
        private bool _skipCurrentGeometry;
        private bool _prototypeCandidate;
        private Transform _elementTransform;
        private Transform _elementTransformInverse;
        private Document _currentDoc;
        private int _modelVertexOffset;
        private int _prototypeVertexOffset;
        private string _currentMaterial = "SLBH_MAT_DEFAULT";
        private SystemInfo _currentSystem;
        private List<ElementId> _viewFilters;

        // Revit calls the export context on a single thread, so one hasher instance can be reused
        // instead of allocating a new SHA256 for every element / prototype.
        private static readonly SHA256 Hasher = SHA256.Create();

        private sealed class LinkExportContext
        {
            public Document Document;
            public RevitLinkInstance Instance;
            public BridgeLinkInstance Record;
            public string SourceModelKey;
            public string SourceDocumentName;
            public string SourceDocumentGuid;
            public int LinkInstanceId;
            public string LinkInstanceName;
            public string LinkTypeName;
            public Transform LinkTransform;
            public int Depth;
        }

        private sealed class BufferedFace
        {
            public int A;
            public int B;
            public int C;
            public string Material;
        }

        private sealed class ElementMeshBuffer
        {
            public readonly List<XYZ> Vertices = new List<XYZ>();
            public readonly List<BufferedFace> Faces = new List<BufferedFace>();
        }

        public ObjExportContext(Document doc, View3D view, string modelPath, string prototypesPath, BridgeProject project, ExportProfile profile, BridgeExportOptions options)
        {
            if (doc == null) throw new ArgumentNullException("doc");
            if (view == null) throw new ArgumentNullException("view");
            if (project == null) throw new ArgumentNullException("project");
            _doc = doc;
            _view = view;
            _project = project;
            _profile = profile;
            _options = options ?? new BridgeExportOptions();
            _currentDoc = doc;
            _elementTransform = Transform.Identity;
            _elementTransformInverse = Transform.Identity;
            _modelWriter = new StreamWriter(modelPath, false, new UTF8Encoding(false));
            _prototypeWriter = new StreamWriter(prototypesPath, false, new UTF8Encoding(false));
            _modelWriter.WriteLine("# SLBH Revit Bridge v0.4.0 unique geometry");
            _modelWriter.WriteLine("# Units: meter");
            _prototypeWriter.WriteLine("# SLBH Revit Bridge v0.4.0 prototypes");
            _prototypeWriter.WriteLine("# Units: meter; prototype local coordinates");
            _transforms.Push(Transform.Identity);
            EnsureDefaultMaterial();
            RecordUnavailableVisibleLinks();
        }

        public bool Start()
        {
            return true;
        }
        public void Finish()
        {
            _project.Performance.UniquePrototypes = _prototypes.Count;
            _project.Performance.LinkFiles = _project.Links
                .Select(x => x.SourceDocumentGuid)
                .Where(x => !string.IsNullOrWhiteSpace(x))
                .Distinct()
                .Count();
            _modelWriter.Flush();
            _prototypeWriter.Flush();
        }
        public bool IsCanceled()
        {
            return false;
        }

        public RenderNodeAction OnViewBegin(ViewNode node)
        {
            return RenderNodeAction.Proceed;
        }
        public void OnViewEnd(ElementId elementId) { }

        public RenderNodeAction OnElementBegin(ElementId elementId)
        {
            _currentDoc = CurrentDocument;
            _currentElement = _currentDoc.GetElement(elementId);
            _currentRecord = null;
            _currentSystem = null;
            _buffer = null;
            _skipCurrentGeometry = false;
            _prototypeCandidate = false;
            _currentMaterial = "SLBH_MAT_DEFAULT";
            _elementTransform = Transform.Identity;
            _elementTransformInverse = Transform.Identity;

            if (_currentElement == null)
            {
                _project.Performance.SkippedElements++;
                return RenderNodeAction.Skip;
            }

            string stableKey = BuildStableElementKey(_currentElement);
            if (!_seenElements.Add(stableKey))
                return RenderNodeAction.Skip;

            string objName = "SLBH_E_" + SafeObjToken(stableKey);
            _currentRecord = CreateElementRecord(_currentElement, objName);
            ApplyFamilyRule(_currentRecord);
            _project.Elements.Add(_currentRecord);
            _project.Performance.SourceElements++;
            if (IsLinkedElement)
            {
                _project.Performance.LinkedElements++;
                LinkExportContext link = CurrentLinkContext;
                if (link != null && link.Record != null)
                    link.Record.ElementCount++;
            }
            else
            {
                _project.Performance.HostElements++;
            }
            PopulateMaterialKeysFromElement(_currentElement, _currentRecord);

            if (string.Equals(_currentRecord.ExportAction, "SKIP", StringComparison.Ordinal))
            {
                _currentRecord.GeometryMode = "skipped_by_family_rule";
                _project.Performance.SkippedElements++;
                _project.Performance.RuleSkippedElements++;
                _skipCurrentGeometry = true;
                return RenderNodeAction.Skip;
            }

            if (TryPopulateStraightRoundCurve(_currentElement, _currentRecord))
            {
                _project.Performance.ParametricRound++;
                _skipCurrentGeometry = true;
                return RenderNodeAction.Skip;
            }

            if (TryPopulateRoundStructuralMember(_currentElement, _currentRecord))
            {
                _project.Performance.ParametricRound++;
                _skipCurrentGeometry = true;
                return RenderNodeAction.Skip;
            }

            FamilyInstance instance = _currentElement as FamilyInstance;
            if (instance != null && !string.Equals(_currentRecord.ExportAction, "FULL", StringComparison.Ordinal) && CanUsePrototype(instance))
            {
                try
                {
                    Transform instanceTransform = instance.GetTransform() ?? Transform.Identity;
                    _elementTransform = CurrentLinkTransform.Multiply(instanceTransform);
                    if (IsUsableTransform(_elementTransform))
                    {
                        _elementTransformInverse = _elementTransform.Inverse;
                        _prototypeCandidate = true;
                        if (string.Equals(_currentRecord.ExportAction, "PROTOTYPE", StringComparison.Ordinal))
                            _project.Performance.ForcedPrototypeCandidates++;
                    }
                }
                catch
                {
                    _elementTransform = Transform.Identity;
                    _elementTransformInverse = Transform.Identity;
                    _prototypeCandidate = false;
                }
            }
            else if (string.Equals(_currentRecord.ExportAction, "FULL", StringComparison.Ordinal))
            {
                _project.Performance.ForcedFullMesh++;
            }

            _buffer = new ElementMeshBuffer();
            return RenderNodeAction.Proceed;
        }

        public void OnElementEnd(ElementId elementId)
        {
            try
            {
                FlushCurrentElement();
            }
            finally
            {
                _currentElement = null;
                _currentRecord = null;
                _buffer = null;
                _skipCurrentGeometry = false;
                _prototypeCandidate = false;
            }
        }

        public RenderNodeAction OnInstanceBegin(InstanceNode node)
        {
            PushTransform(node.GetTransform());
            return RenderNodeAction.Proceed;
        }
        public void OnInstanceEnd(InstanceNode node)
        {
            PopTransform();
        }
        public RenderNodeAction OnLinkBegin(LinkNode node)
        {
            if (!_options.ExportLinkedModels)
                return RenderNodeAction.Skip;

            if (_linkContexts.Count > 0)
            {
                RecordSkippedLink("nested-link", -1, "skipped", "Nested Revit links are skipped in this exporter version.", _linkContexts.Count + 1);
                return RenderNodeAction.Skip;
            }

            Document linkDoc = null;
            Transform linkTransform = Transform.Identity;
            try { linkDoc = node.GetDocument(); }
            catch { linkDoc = null; }
            try { linkTransform = node.GetTransform() ?? Transform.Identity; }
            catch { linkTransform = Transform.Identity; }

            if (linkDoc == null)
            {
                RecordSkippedLink("unloaded-link", -1, "unloaded", "Link document is not loaded or cannot be read.", _linkContexts.Count + 1);
                return RenderNodeAction.Skip;
            }

            RevitLinkInstance instance = ResolveLinkInstance(linkDoc, linkTransform);
            LinkExportContext context = CreateLinkContext(linkDoc, instance, linkTransform);
            _linkContexts.Push(context);
            _currentDoc = linkDoc;
            _project.Links.Add(context.Record);
            _project.Performance.LinkInstances++;
            PushTransform(linkTransform);
            return RenderNodeAction.Proceed;
        }
        public void OnLinkEnd(LinkNode node)
        {
            PopTransform();
            if (_linkContexts.Count > 0)
                _linkContexts.Pop();
            _currentDoc = CurrentDocument;
        }

        public void OnMaterial(MaterialNode node)
        {
            ElementId nodeMaterialId = node.MaterialId;
            if (nodeMaterialId == null || nodeMaterialId == ElementId.InvalidElementId)
                _currentMaterial = "SLBH_MAT_DEFAULT";
            else
            {
                int materialId = nodeMaterialId.IntegerValue;
                _currentMaterial = MaterialKey(CurrentSourceModelKey, materialId);
                EnsureMaterialRecord(nodeMaterialId);
            }
            AddMaterialKey(_currentRecord, _currentMaterial);
        }

        public void OnPolymesh(PolymeshTopology node)
        {
            if (_skipCurrentGeometry || _buffer == null)
                return;

            Transform transform = _transforms.Peek();
            IList<XYZ> points = node.GetPoints();
            IList<PolymeshFacet> facets = node.GetFacets();
            int baseIndex = _buffer.Vertices.Count;

            foreach (XYZ point in points)
            {
                XYZ p = transform.OfPoint(point);
                if (_prototypeCandidate)
                    p = _elementTransformInverse.OfPoint(p);
                _buffer.Vertices.Add(p);
            }

            foreach (PolymeshFacet facet in facets)
            {
                _buffer.Faces.Add(new BufferedFace
                {
                    A = baseIndex + facet.V1,
                    B = baseIndex + facet.V2,
                    C = baseIndex + facet.V3,
                    Material = _currentMaterial
                });
            }
        }

        public RenderNodeAction OnFaceBegin(FaceNode node)
        {
            return RenderNodeAction.Proceed;
        }
        public void OnFaceEnd(FaceNode node) { }
        public void OnRPC(RPCNode node) { }
        public void OnLight(LightNode node) { }

        public void Dispose()
        {
            _modelWriter.Dispose();
            _prototypeWriter.Dispose();
        }

        private void PushTransform(Transform local)
        {
            Transform parent = _transforms.Peek();
            _transforms.Push(parent.Multiply(local ?? Transform.Identity));
        }

        private void PopTransform()
        {
            if (_transforms.Count > 1)
                _transforms.Pop();
        }

        private Document CurrentDocument
        {
            get { return _linkContexts.Count > 0 ? _linkContexts.Peek().Document : _doc; }
        }

        private LinkExportContext CurrentLinkContext
        {
            get { return _linkContexts.Count > 0 ? _linkContexts.Peek() : null; }
        }

        private bool IsLinkedElement
        {
            get { return CurrentLinkContext != null; }
        }

        private Transform CurrentLinkTransform
        {
            get
            {
                LinkExportContext link = CurrentLinkContext;
                return link != null && link.LinkTransform != null ? link.LinkTransform : Transform.Identity;
            }
        }

        private string CurrentSourceModelKey
        {
            get
            {
                LinkExportContext link = CurrentLinkContext;
                return link != null ? link.SourceModelKey : "HOST";
            }
        }

        private LinkExportContext CreateLinkContext(Document linkDoc, RevitLinkInstance instance, Transform linkTransform)
        {
            string docGuid = StableDocumentId(linkDoc);
            string instanceName = instance != null ? Safe(() => instance.Name) : Safe(() => linkDoc.Title);
            string typeName = "";
            int instanceId = -1;
            if (instance != null)
            {
                instanceId = instance.Id.IntegerValue;
                try
                {
                    Element type = _doc.GetElement(instance.GetTypeId());
                    typeName = type != null ? type.Name : "";
                }
                catch { }
            }

            string instanceToken = instanceId >= 0
                ? "I" + instanceId.ToString(CultureInfo.InvariantCulture)
                : "T" + SafeObjToken(TransformFingerprint(linkTransform));
            string modelKey = "LINK_" + SafeObjToken(docGuid) + "_" + instanceToken;
            string docName = FirstNonEmpty(Safe(() => linkDoc.Title), Path.GetFileName(Safe(() => linkDoc.PathName)), "LinkedModel");
            var record = new BridgeLinkInstance
            {
                SourceModelKey = modelKey,
                SourceDocumentName = docName,
                SourceDocumentGuid = docGuid,
                LinkInstanceId = instanceId,
                LinkInstanceName = instanceName,
                LinkTypeName = typeName,
                LinkTransform = TransformToArray(linkTransform),
                LinkDepth = _linkContexts.Count + 1,
                Status = "loaded"
            };

            _project.Performance.LinkFiles = Math.Max(_project.Performance.LinkFiles, _project.Links.Select(x => x.SourceDocumentGuid).Distinct().Count() + 1);

            return new LinkExportContext
            {
                Document = linkDoc,
                Instance = instance,
                Record = record,
                SourceModelKey = modelKey,
                SourceDocumentName = docName,
                SourceDocumentGuid = docGuid,
                LinkInstanceId = instanceId,
                LinkInstanceName = instanceName,
                LinkTypeName = typeName,
                LinkTransform = linkTransform ?? Transform.Identity,
                Depth = _linkContexts.Count + 1
            };
        }

        private RevitLinkInstance ResolveLinkInstance(Document linkDoc, Transform linkTransform)
        {
            string cacheKey = StableDocumentId(linkDoc) + "|" + TransformFingerprint(linkTransform);
            RevitLinkInstance cached;
            if (_resolvedLinkInstances.TryGetValue(cacheKey, out cached))
                return cached;

            try
            {
                var collector = new FilteredElementCollector(_doc, _view.Id).OfClass(typeof(RevitLinkInstance));
                foreach (RevitLinkInstance candidate in collector.Cast<RevitLinkInstance>())
                {
                    Document candidateDoc = null;
                    try { candidateDoc = candidate.GetLinkDocument(); }
                    catch { candidateDoc = null; }
                    if (candidateDoc == null || !string.Equals(StableDocumentId(candidateDoc), StableDocumentId(linkDoc), StringComparison.Ordinal))
                        continue;

                    Transform candidateTransform = null;
                    try { candidateTransform = candidate.GetTotalTransform(); }
                    catch
                    {
                        try { candidateTransform = candidate.GetTransform(); }
                        catch { candidateTransform = null; }
                    }
                    if (TransformsAlmostEqual(candidateTransform, linkTransform))
                    {
                        _resolvedLinkInstances[cacheKey] = candidate;
                        return candidate;
                    }
                }
            }
            catch { }

            return null;
        }

        private void RecordSkippedLink(string name, int instanceId, string status, string reason, int depth)
        {
            if (_project.SkippedLinks.Any(x =>
                string.Equals(x.LinkName, name, StringComparison.Ordinal) &&
                x.LinkInstanceId == instanceId &&
                string.Equals(x.Status, status, StringComparison.Ordinal)))
                return;
            _project.SkippedLinks.Add(new BridgeSkippedLink
            {
                LinkName = name,
                LinkInstanceId = instanceId,
                Status = status,
                Reason = reason,
                LinkDepth = depth
            });
            _project.Warnings.Add("Skipped link: " + name + " / " + status + " / " + reason);
            _project.Performance.SkippedLinks++;
        }

        private void RecordUnavailableVisibleLinks()
        {
            if (!_options.ExportLinkedModels)
                return;
            try
            {
                var collector = new FilteredElementCollector(_doc, _view.Id).OfClass(typeof(RevitLinkInstance));
                foreach (RevitLinkInstance instance in collector.Cast<RevitLinkInstance>())
                {
                    Document linkDoc = null;
                    try { linkDoc = instance.GetLinkDocument(); }
                    catch
                    {
                        RecordSkippedLink(Safe(() => instance.Name), instance.Id.IntegerValue, "unreadable", "Link document could not be read.", 1);
                        continue;
                    }
                    if (linkDoc == null)
                        RecordSkippedLink(Safe(() => instance.Name), instance.Id.IntegerValue, "unloaded", "Link document is not loaded.", 1);
                }
            }
            catch { }
        }

        private bool CanUsePrototype(FamilyInstance instance)
        {
            try
            {
                if (_options.DisablePrototypes || ViewHasActiveSectionBox())
                    return false;
                if (instance.Symbol == null || instance.Symbol.Family == null || instance.Symbol.Family.IsInPlace)
                    return false;
                string category = instance.Category != null ? instance.Category.Name : "";
                // Large host-like families are safer as unique geometry. Repeated MEP/furniture/doors/windows
                // receive the largest benefit from source-level instancing.
                if (ContainsAny(category, "墙", "楼板", "屋顶", "幕墙嵌板"))
                    return false;
                return true;
            }
            catch { return false; }
        }

        private void ApplyFamilyRule(BridgeElement record)
        {
            if (record == null)
                return;
            record.FamilyRuleKey = ExportCommand.FamilyRuleKey(record.Category, record.Family, record.Type);
            record.ExportAction = "AUTO";

            if (_options == null || _options.FamilyActions == null)
                return;

            string action;
            if (!_options.FamilyActions.TryGetValue(record.FamilyRuleKey, out action))
                return;

            action = NormalizeExportAction(action);
            record.ExportAction = action;
            if (action == "FULL")
                record.LodHint = "FULL";
        }

        private static string NormalizeExportAction(string action)
        {
            string value = (action ?? "").Trim().ToUpperInvariant();
            if (value == "SKIP" || value == "FULL" || value == "PROTOTYPE")
                return value;
            return "PROTOTYPE";
        }

        private void FlushCurrentElement()
        {
            if (_currentRecord == null || _buffer == null || _buffer.Faces.Count == 0)
                return;

            if (_prototypeCandidate)
            {
                string hash = ComputePrototypeHash(_buffer, _currentRecord);
                string prototypeId = "P_" + hash.Substring(0, 20);
                BridgePrototype prototype;
                if (!_prototypes.TryGetValue(hash, out prototype))
                {
                    string objName = prototypeId;
                    WriteBuffer(_prototypeWriter, objName, _buffer, ref _prototypeVertexOffset);
                    prototype = new BridgePrototype
                    {
                        PrototypeId = prototypeId,
                        ObjName = objName,
                        Category = _currentRecord.Category,
                        Family = _currentRecord.Family,
                        Type = _currentRecord.Type,
                MaterialKeys = new List<string>(_currentRecord.MaterialKeys),
                        SourceModelKey = _currentRecord.SourceModelKey,
                        VertexCount = _buffer.Vertices.Count,
                        FaceCount = _buffer.Faces.Count,
                        InstanceCount = 0
                    };
                    _prototypes.Add(hash, prototype);
                    _project.Prototypes.Add(prototype);
                    _project.Performance.VerticesWritten += _buffer.Vertices.Count;
                    _project.Performance.FacesWritten += _buffer.Faces.Count;
                }
                else
                {
                    _project.Performance.EstimatedRepeatedFacesAvoided += prototype.FaceCount;
                }

                prototype.InstanceCount++;
                _currentRecord.GeometryMode = "prototype_instance";
                _currentRecord.PrototypeId = prototype.PrototypeId;
                _currentRecord.PrototypeObjName = prototype.ObjName;
                _currentRecord.Transform = TransformToArray(_elementTransform);
                _project.Performance.PrototypeInstances++;
            }
            else
            {
                WriteBuffer(_modelWriter, _currentRecord.ObjName, _buffer, ref _modelVertexOffset);
                _project.Performance.MeshElements++;
                _project.Performance.VerticesWritten += _buffer.Vertices.Count;
                _project.Performance.FacesWritten += _buffer.Faces.Count;
            }
        }

        private static void WriteBuffer(StreamWriter writer, string objName, ElementMeshBuffer buffer, ref int vertexOffset)
        {
            writer.WriteLine();
            writer.WriteLine("o " + objName);
            writer.WriteLine("g " + objName);
            foreach (XYZ p in buffer.Vertices)
            {
                writer.WriteLine(string.Format(CultureInfo.InvariantCulture, "v {0:0.######} {1:0.######} {2:0.######}",
                    p.X * FeetToMeters, p.Y * FeetToMeters, p.Z * FeetToMeters));
            }

            string lastMaterial = null;
            foreach (BufferedFace face in buffer.Faces)
            {
                if (!string.Equals(lastMaterial, face.Material, StringComparison.Ordinal))
                {
                    lastMaterial = face.Material ?? "SLBH_MAT_DEFAULT";
                    writer.WriteLine("usemtl " + lastMaterial);
                }
                writer.WriteLine(string.Format(CultureInfo.InvariantCulture, "f {0} {1} {2}",
                    vertexOffset + face.A + 1,
                    vertexOffset + face.B + 1,
                    vertexOffset + face.C + 1));
            }
            vertexOffset += buffer.Vertices.Count;
        }

        private static string ComputePrototypeHash(ElementMeshBuffer buffer, BridgeElement record)
        {
            var builder = new StringBuilder(buffer.Vertices.Count * 24 + buffer.Faces.Count * 20);
            builder.Append(record.SourceModelKey).Append('|').Append(record.Category).Append('|').Append(record.Family).Append('|').Append(record.Type).Append('|');
            builder.Append(record.DisplayColorHex).Append('|').Append(record.SystemCode).Append('|');
            foreach (string key in record.MaterialKeys.OrderBy(x => x)) builder.Append(key).Append(',');
            builder.Append(';');
            foreach (XYZ p in buffer.Vertices)
            {
                builder.Append(Math.Round(p.X * FeetToMeters, 6).ToString("R", CultureInfo.InvariantCulture)).Append(',');
                builder.Append(Math.Round(p.Y * FeetToMeters, 6).ToString("R", CultureInfo.InvariantCulture)).Append(',');
                builder.Append(Math.Round(p.Z * FeetToMeters, 6).ToString("R", CultureInfo.InvariantCulture)).Append(';');
            }
            foreach (BufferedFace f in buffer.Faces)
                builder.Append(f.A).Append(',').Append(f.B).Append(',').Append(f.C).Append(',').Append(f.Material).Append(';');

            byte[] hash = Hasher.ComputeHash(Encoding.UTF8.GetBytes(builder.ToString()));
            var result = new StringBuilder(hash.Length * 2);
            foreach (byte b in hash) result.Append(b.ToString("x2", CultureInfo.InvariantCulture));
            return result.ToString();
        }

        private static double[] TransformToArray(Transform transform)
        {
            Transform t = transform ?? Transform.Identity;
            XYZ x = t.BasisX; XYZ y = t.BasisY; XYZ z = t.BasisZ; XYZ o = t.Origin;
            return new[]
            {
                x.X, y.X, z.X, o.X * FeetToMeters,
                x.Y, y.Y, z.Y, o.Y * FeetToMeters,
                x.Z, y.Z, z.Z, o.Z * FeetToMeters,
                0.0, 0.0, 0.0, 1.0
            };
        }

        private static bool IsUsableTransform(Transform transform)
        {
            if (transform == null)
                return false;
            try
            {
                XYZ x = transform.BasisX;
                XYZ y = transform.BasisY;
                XYZ z = transform.BasisZ;
                if (x == null || y == null || z == null)
                    return false;
                double lx = x.GetLength();
                double ly = y.GetLength();
                double lz = z.GetLength();
                if (lx < 1e-9 || ly < 1e-9 || lz < 1e-9)
                    return false;
                double determinant = x.DotProduct(y.CrossProduct(z));
                return Math.Abs(determinant) > 1e-9;
            }
            catch { return false; }
        }

        private BridgeElement CreateElementRecord(Element element, string objName)
        {
            string category = element.Category != null ? element.Category.Name : "未分类";
            string family = GetFamilyName(element);
            string type = GetTypeName(element);
            string level = GetLevelName(element);
            string discipline = GuessDiscipline(category, family, type);
            SystemInfo system = GetSystemInfo(element);
            _currentSystem = system;
            DisplayInfo display = GetDisplayInfo(element, system);
            LinkExportContext link = CurrentLinkContext;
            string uniqueId = Safe(() => element.UniqueId);
            string sourceKey = CurrentSourceModelKey;
            string stableKey = BuildStableElementKey(element);

            return new BridgeElement
            {
                ElementId = element.Id.IntegerValue,
                UniqueId = uniqueId,
                StableElementKey = stableKey,
                IsLinkedElement = link != null,
                SourceDocumentName = link != null ? link.SourceDocumentName : FirstNonEmpty(Safe(() => _doc.Title), Path.GetFileName(Safe(() => _doc.PathName)), "Host"),
                SourceDocumentGuid = link != null ? link.SourceDocumentGuid : StableDocumentId(_doc),
                SourceModelKey = sourceKey,
                LinkInstanceId = link != null ? link.LinkInstanceId : -1,
                LinkInstanceName = link != null ? link.LinkInstanceName : "",
                LinkTypeName = link != null ? link.LinkTypeName : "",
                LinkedElementId = link != null ? element.Id.IntegerValue : -1,
                LinkedUniqueId = link != null ? uniqueId : "",
                LinkTransform = link != null ? TransformToArray(link.LinkTransform) : TransformToArray(Transform.Identity),
                LinkDepth = link != null ? link.Depth : 0,
                ObjName = objName,
                DisplayName = BuildDisplayName(category, type),
                Category = category,
                SourceCategory = category,
                CategoryCode = NormalizeCode(category),
                Discipline = discipline,
                Family = family,
                Type = type,
                Level = level,
                Workset = GetWorksetName(element),
                HasMepConnector = HasMepConnector(element),
                LodHint = GetLodHint(element),

                SystemName = system.Name,
                SystemTypeName = system.TypeName,
                SystemTypeId = system.TypeId,
                SystemClassification = system.Classification,
                SystemAbbreviation = system.Abbreviation,
                SystemCode = system.Code,
                SystemSource = system.Source,

                DisplayColor = ColorToArray(display.Color),
                DisplayColorHex = ColorToHex(display.Color),
                DisplayColorSource = display.Source,
                HasDisplayOverride = display.HasOverride
            };
        }

        private SystemInfo GetSystemInfo(Element element)
        {
            SystemInfo info = new SystemInfo();
            MEPSystem system = null;

            MEPCurve mepCurve = element as MEPCurve;
            if (mepCurve != null)
            {
                try { system = mepCurve.MEPSystem; }
                catch { system = null; }
            }

            if (system == null)
            {
                FamilyInstance instance = element as FamilyInstance;
                if (instance != null)
                    system = GetSystemFromConnectors(instance);
            }

            MEPSystemType systemType = null;
            if (system != null)
            {
                info.Name = FirstNonEmpty(system.Name, GetParameterText(element, BuiltInParameter.RBS_SYSTEM_NAME_PARAM), "未命名系统");
                info.Source = mepCurve != null ? "mep_curve" : "connector";
                try { systemType = CurrentDocument.GetElement(system.GetTypeId()) as MEPSystemType; }
                catch { systemType = null; }
            }

            if (systemType == null)
                systemType = GetSystemTypeFromParameters(element);

            if (systemType != null)
            {
                info.TypeName = systemType.Name ?? "";
                info.TypeId = systemType.Id.IntegerValue;
                info.Abbreviation = FirstNonEmpty(Safe(() => systemType.Abbreviation), GetParameterText(element, BuiltInParameter.RBS_SYSTEM_ABBREVIATION_PARAM), GetParameterText(element, BuiltInParameter.RBS_DUCT_PIPE_SYSTEM_ABBREVIATION_PARAM));
                info.Classification = Safe(() => systemType.SystemClassification.ToString());
                info.FillColor = SafeColor(() => systemType.FillColor);
                info.MaterialId = SafeElementId(() => systemType.MaterialId);
                if (info.Source == "none")
                    info.Source = "system_type_parameter";
            }

            if (string.IsNullOrWhiteSpace(info.Name) || info.Name == "未分配系统")
                info.Name = FirstNonEmpty(GetParameterText(element, BuiltInParameter.RBS_SYSTEM_NAME_PARAM), "未分配系统");
            if (string.IsNullOrWhiteSpace(info.TypeName))
                info.TypeName = GetElementIdParameterName(element, BuiltInParameter.RBS_DUCT_SYSTEM_TYPE_PARAM, BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM);
            if (string.IsNullOrWhiteSpace(info.Classification))
                info.Classification = GetParameterText(element, BuiltInParameter.RBS_SYSTEM_CLASSIFICATION_PARAM);
            if (string.IsNullOrWhiteSpace(info.Abbreviation))
                info.Abbreviation = FirstNonEmpty(GetParameterText(element, BuiltInParameter.RBS_SYSTEM_ABBREVIATION_PARAM), GetParameterText(element, BuiltInParameter.RBS_DUCT_PIPE_SYSTEM_ABBREVIATION_PARAM));

            info.Code = ClassifySystemCode(info.Name, info.TypeName, info.Abbreviation, info.Classification);
            return info;
        }

        private MEPSystem GetSystemFromConnectors(FamilyInstance instance)
        {
            try
            {
                ConnectorManager manager = instance.MEPModel != null ? instance.MEPModel.ConnectorManager : null;
                if (manager == null)
                    return null;

                foreach (Connector connector in manager.Connectors)
                {
                    if (connector == null)
                        continue;
                    MEPSystem connectorSystem = connector.MEPSystem;
                    if (connectorSystem != null)
                        return connectorSystem;
                }
            }
            catch { }
            return null;
        }

        private MEPSystemType GetSystemTypeFromParameters(Element element)
        {
            BuiltInParameter[] parameters =
            {
                BuiltInParameter.RBS_DUCT_SYSTEM_TYPE_PARAM,
                BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM
            };

            foreach (BuiltInParameter bip in parameters)
            {
                try
                {
                    Parameter parameter = element.get_Parameter(bip);
                    if (parameter != null && parameter.StorageType == StorageType.ElementId)
                    {
                        ElementId id = parameter.AsElementId();
                        if (id != null && id != ElementId.InvalidElementId)
                        {
                            MEPSystemType result = CurrentDocument.GetElement(id) as MEPSystemType;
                            if (result != null)
                                return result;
                        }
                    }
                }
                catch { }
            }
            return null;
        }

        private DisplayInfo GetDisplayInfo(Element element, SystemInfo system)
        {
            DisplayInfo info = new DisplayInfo();
            Color color;

            // Element overrides of the host view are keyed by host ElementIds. A linked element's id
            // belongs to the link document, so querying it here would return the override of an
            // unrelated host element that happens to share the same integer id.
            if (!IsLinkedElement && TryGetOverrideColor(_view.GetElementOverrides(element.Id), out color))
            {
                info.Color = color;
                info.Source = "element_override";
                info.HasOverride = true;
                return info;
            }

            if (TryGetFilterColor(element, out color))
            {
                info.Color = color;
                info.Source = "view_filter";
                info.HasOverride = true;
                return info;
            }

            if (system != null && IsValidColor(system.FillColor))
            {
                info.Color = system.FillColor;
                info.Source = "mep_system_type";
                info.HasOverride = true;
                return info;
            }

            if (system != null && system.MaterialId != null && system.MaterialId != ElementId.InvalidElementId)
            {
                Material systemMaterial = CurrentDocument.GetElement(system.MaterialId) as Material;
                if (systemMaterial != null && IsValidColor(systemMaterial.Color))
                {
                    info.Color = systemMaterial.Color;
                    info.Source = "mep_system_material";
                    info.HasOverride = true;
                    return info;
                }
            }

            if (element.Category != null && TryGetOverrideColor(_view.GetCategoryOverrides(element.Category.Id), out color))
            {
                info.Color = color;
                info.Source = "category_override";
                info.HasOverride = true;
                return info;
            }

            Material material = GetFirstElementMaterial(element);
            if (material != null && IsValidColor(material.Color))
            {
                info.Color = material.Color;
                info.Source = "material";
                info.HasOverride = false;
                return info;
            }

            info.Color = null;
            info.Source = "none";
            info.HasOverride = false;
            return info;
        }

        private bool TryGetFilterColor(Element element, out Color color)
        {
            color = null;
            try
            {
                if (_viewFilters == null)
                    _viewFilters = _view.GetFilters().ToList();
                foreach (ElementId filterId in _viewFilters)
                {
                    if (!_view.IsFilterApplied(filterId) || !_view.GetFilterVisibility(filterId))
                        continue;

                    bool matches = false;
                    ParameterFilterElement parameterFilter = _doc.GetElement(filterId) as ParameterFilterElement;
                    if (parameterFilter != null)
                    {
                        ElementFilter filter = parameterFilter.GetElementFilter();
                        matches = filter != null && filter.PassesFilter(CurrentDocument, element.Id);
                    }
                    else
                    {
                        // Selection filters store host ElementIds; they can never contain a linked element.
                        SelectionFilterElement selectionFilter = IsLinkedElement ? null : _doc.GetElement(filterId) as SelectionFilterElement;
                        matches = selectionFilter != null && selectionFilter.Contains(element.Id);
                    }

                    if (!matches)
                        continue;

                    OverrideGraphicSettings overrides = _view.GetFilterOverrides(filterId);
                    if (TryGetOverrideColor(overrides, out color))
                        return true;
                }
            }
            catch { }
            return false;
        }

        private static bool TryGetOverrideColor(OverrideGraphicSettings overrides, out Color color)
        {
            color = null;
            if (overrides == null)
                return false;

            Color[] candidates =
            {
                SafeColor(() => overrides.SurfaceForegroundPatternColor),
                SafeColor(() => overrides.SurfaceBackgroundPatternColor),
                SafeColor(() => overrides.ProjectionLineColor)
            };

            foreach (Color candidate in candidates)
            {
                if (IsValidColor(candidate))
                {
                    color = candidate;
                    return true;
                }
            }
            return false;
        }

        private bool TryPopulateStraightRoundCurve(Element element, BridgeElement record)
        {
            MEPCurve curve = element as MEPCurve;
            if (curve == null)
                return false;

            LocationCurve location = curve.Location as LocationCurve;
            Line line = location != null ? location.Curve as Line : null;
            if (line == null)
                return false;

            double diameter;
            try { diameter = curve.Diameter; }
            catch { return false; }
            if (element is Pipe)
            {
                // MEPCurve.Diameter is the nominal size for pipes; the modelled solid uses the
                // outside diameter, so prefer it when Revit provides a larger value.
                double outside = GetDoubleParameter(element, BuiltInParameter.RBS_PIPE_OUTER_DIAMETER);
                if (outside > diameter)
                    diameter = outside;
            }
            if (diameter <= 1e-6)
                return false;

            Duct duct = element as Duct;
            if (duct != null)
            {
                try
                {
                    MEPCurveType curveType = CurrentDocument.GetElement(duct.GetTypeId()) as MEPCurveType;
                    if (curveType != null && curveType.Shape != ConnectorProfileType.Round)
                        return false;
                }
                catch { }
            }

            XYZ start = CurrentLinkTransform.OfPoint(line.GetEndPoint(0));
            XYZ end = CurrentLinkTransform.OfPoint(line.GetEndPoint(1));
            // The section box lives in host coordinates, so test the transformed endpoints.
            if (IsCurveClippedByActiveSectionBox(start, end))
                return false;
            record.GeometryMode = "straight_round_curve";
            record.CurveKind = duct != null ? "round_duct" : "round_pipe";
            record.PrimitiveType = duct != null ? "ROUND_DUCT" : "ROUND_PIPE";
            record.CurveStart = PointToMeters(start);
            record.CurveEnd = PointToMeters(end);
            record.DiameterMeters = diameter * FeetToMeters;
            record.LengthMeters = line.Length * FeetToMeters;
            record.Slope = GetDoubleParameter(element, BuiltInParameter.RBS_DUCT_SLOPE, BuiltInParameter.RBS_PIPE_SLOPE);
            return true;
        }

        private bool TryPopulateRoundStructuralMember(Element element, BridgeElement record)
        {
            FamilyInstance instance = element as FamilyInstance;
            if (instance == null || record == null)
                return false;

            string text = string.Join(" ", new[] { record.Category, record.Family, record.Type });
            if (!HasRoundStructuralHint(text) || HasNonRoundStructuralHint(text))
                return false;
            if (!ContainsAny(text,
                "structural column", "column", "柱",
                "structural framing", "framing", "brace", "支撑", "结构框架"))
                return false;

            LocationCurve location = instance.Location as LocationCurve;
            Line line = location != null ? location.Curve as Line : null;

            double diameter = GetFamilyDiameter(instance);
            if (diameter <= 1e-6)
                TryEstimateRoundDiameterFromBoundingBox(instance, out diameter);
            if (diameter <= 1e-6)
                return false;

            XYZ rawStart;
            XYZ rawEnd;
            double length;
            if (line != null && line.Length > 1e-6)
            {
                rawStart = line.GetEndPoint(0);
                rawEnd = line.GetEndPoint(1);
                length = line.Length;
            }
            else if (!TryGetPointBasedRoundMemberEndpoints(instance, diameter, out rawStart, out rawEnd, out length))
            {
                return false;
            }
            XYZ start = CurrentLinkTransform.OfPoint(rawStart);
            XYZ end = CurrentLinkTransform.OfPoint(rawEnd);
            // The section box lives in host coordinates, so test the transformed endpoints.
            if (IsCurveClippedByActiveSectionBox(start, end))
                return false;
            record.GeometryMode = "straight_round_curve";
            record.CurveKind = "round_structural_column";
            record.PrimitiveType = "ROUND_STRUCTURAL_COLUMN";
            record.CurveStart = PointToMeters(start);
            record.CurveEnd = PointToMeters(end);
            record.DiameterMeters = diameter * FeetToMeters;
            record.LengthMeters = length * FeetToMeters;
            record.Slope = 0.0;
            record.LodHint = _profile == ExportProfile.Fine ? "FULL" : "PARAMETRIC";
            return true;
        }

        private static bool HasRoundStructuralHint(string text)
        {
            return ContainsAny(text,
                "round", "circular", "circle", "pipe", "tube", "rod", "chs", "round hss", "hss round",
                "圆", "圆形", "圆管", "圆钢", "钢管", "管柱");
        }

        private static bool HasNonRoundStructuralHint(string text)
        {
            return ContainsAny(text,
                "i-beam", "h-beam", "w-shape", "wide flange", "rectangular", "rectangle", "square",
                "box beam", "angle", "channel", "i shape", "h shape",
                "工字", "H型", "h型", "矩形", "方管", "箱型", "槽钢", "角钢");
        }

        private bool TryGetPointBasedRoundMemberEndpoints(FamilyInstance instance, double diameter, out XYZ start, out XYZ end, out double length)
        {
            start = null;
            end = null;
            length = 0.0;

            LocationPoint location = instance.Location as LocationPoint;
            if (location == null)
                return false;

            BoundingBoxXYZ box = null;
            try { box = instance.get_BoundingBox(null) ?? instance.get_BoundingBox(_view); }
            catch { box = null; }
            if (box == null)
                return false;

            double dx = Math.Abs(box.Max.X - box.Min.X);
            double dy = Math.Abs(box.Max.Y - box.Min.Y);
            double dz = Math.Abs(box.Max.Z - box.Min.Z);
            length = dz;
            if (length <= 1e-6)
                return false;

            // This handles the common vertical round steel column family.  If the world-aligned
            // horizontal extents do not look like the reported diameter, keep the original mesh.
            double horizontalMax = Math.Max(dx, dy);
            double horizontalMin = Math.Min(dx, dy);
            if (!NearlyEqualRelative(horizontalMax, diameter, 0.35) || !NearlyEqualRelative(horizontalMin, diameter, 0.35))
                return false;
            if (length <= diameter * 1.25)
                return false;

            XYZ p = location.Point;
            start = new XYZ(p.X, p.Y, box.Min.Z);
            end = new XYZ(p.X, p.Y, box.Max.Z);
            return true;
        }

        private bool IsCurveClippedByActiveSectionBox(XYZ start, XYZ end)
        {
            if (!ViewHasActiveSectionBox())
                return false;
            try
            {
                BoundingBoxXYZ box = _view.GetSectionBox();
                if (box == null)
                    return true;
                return !PointInsideSectionBox(start, box, 1e-6) || !PointInsideSectionBox(end, box, 1e-6);
            }
            catch
            {
                return true;
            }
        }

        private static bool PointInsideSectionBox(XYZ point, BoundingBoxXYZ box, double tolerance)
        {
            if (point == null || box == null)
                return false;
            Transform transform = box.Transform ?? Transform.Identity;
            XYZ local = transform.Inverse.OfPoint(point);
            return local.X >= box.Min.X - tolerance && local.X <= box.Max.X + tolerance
                && local.Y >= box.Min.Y - tolerance && local.Y <= box.Max.Y + tolerance
                && local.Z >= box.Min.Z - tolerance && local.Z <= box.Max.Z + tolerance;
        }

        private static double GetFamilyDiameter(FamilyInstance instance)
        {
            string[] names = { "Diameter", "Outside Diameter", "Nominal Diameter", "D", "d", "OD", "直径", "外径", "公称直径" };
            foreach (string name in names)
            {
                double value = GetDoubleParameterByName(instance, name);
                if (value > 1e-6)
                    return value;
                try
                {
                    if (instance.Symbol != null)
                    {
                        value = GetDoubleParameterByName(instance.Symbol, name);
                        if (value > 1e-6)
                            return value;
                    }
                }
                catch { }
            }
            return 0.0;
        }

        private static bool NearlyEqualRelative(double value, double target, double tolerance)
        {
            if (target <= 1e-9)
                return false;
            return Math.Abs(value - target) / target <= tolerance;
        }

        private bool TryEstimateRoundDiameterFromBoundingBox(Element element, out double diameter)
        {
            diameter = 0.0;
            BoundingBoxXYZ box = null;
            try { box = element.get_BoundingBox(null) ?? element.get_BoundingBox(_view); }
            catch { box = null; }
            if (box == null)
                return false;

            double[] sizes =
            {
                Math.Abs(box.Max.X - box.Min.X),
                Math.Abs(box.Max.Y - box.Min.Y),
                Math.Abs(box.Max.Z - box.Min.Z)
            };
            Array.Sort(sizes);
            if (sizes[0] <= 1e-6 || sizes[2] <= sizes[1] * 1.25)
                return false;
            if (!NearlyEqualRelative(sizes[0], sizes[1], 0.35))
                return false;
            diameter = (sizes[0] + sizes[1]) * 0.5;
            return diameter > 1e-6;
        }

        private static double GetDoubleParameterByName(Element element, string name)
        {
            if (element == null || string.IsNullOrWhiteSpace(name))
                return 0.0;
            try
            {
                Parameter parameter = element.LookupParameter(name);
                if (parameter == null || parameter.StorageType != StorageType.Double)
                    return 0.0;
                return parameter.AsDouble();
            }
            catch { return 0.0; }
        }

        private void PopulateMaterialKeysFromElement(Element element, BridgeElement record)
        {
            HashSet<int> ids = new HashSet<int>();
            AddMaterialIds(ids, SafeMaterialIds(() => element.GetMaterialIds(false)));
            Element type = CurrentDocument.GetElement(element.GetTypeId());
            if (type != null)
                AddMaterialIds(ids, SafeMaterialIds(() => type.GetMaterialIds(false)));

            SystemInfo system = _currentSystem ?? GetSystemInfo(element);
            if (system.MaterialId != null && system.MaterialId != ElementId.InvalidElementId)
                ids.Add(system.MaterialId.IntegerValue);

            foreach (int id in ids)
            {
                ElementId materialId = new ElementId(id);
                EnsureMaterialRecord(materialId);
                AddMaterialKey(record, MaterialKey(CurrentSourceModelKey, id));
            }

            if (record.MaterialKeys.Count == 0)
                AddMaterialKey(record, "SLBH_MAT_DEFAULT");
        }

        private static void AddMaterialIds(HashSet<int> target, ICollection<ElementId> source)
        {
            if (source == null)
                return;
            foreach (ElementId id in source)
            {
                if (id != null && id != ElementId.InvalidElementId)
                    target.Add(id.IntegerValue);
            }
        }

        private static ICollection<ElementId> SafeMaterialIds(Func<ICollection<ElementId>> getter)
        {
            try { return getter(); }
            catch { return null; }
        }

        private Material GetFirstElementMaterial(Element element)
        {
            try
            {
                ICollection<ElementId> ids = element.GetMaterialIds(false);
                if (ids != null)
                {
                    foreach (ElementId id in ids)
                    {
                        Material material = CurrentDocument.GetElement(id) as Material;
                        if (material != null)
                            return material;
                    }
                }
            }
            catch { }
            return null;
        }

        private void EnsureDefaultMaterial()
        {
            if (_project.Materials.Any(x => x.ObjMaterialName == "SLBH_MAT_DEFAULT"))
                return;
            _project.Materials.Add(new BridgeMaterial
            {
                MaterialId = -1,
                ObjMaterialName = "SLBH_MAT_DEFAULT",
                SourceModelKey = "GLOBAL",
                SourceName = "默认材质",
                Color = new[] { 0.60, 0.60, 0.62 },
                Transparency = 0.0
            });
        }

        private void EnsureMaterialRecord(ElementId materialId)
        {
            if (materialId == null || materialId == ElementId.InvalidElementId)
                return;
            int id = materialId.IntegerValue;
            string sourceKey = CurrentSourceModelKey;
            string seenKey = sourceKey + ":" + id.ToString(CultureInfo.InvariantCulture);
            if (!_seenMaterials.Add(seenKey))
                return;

            Material material = CurrentDocument.GetElement(materialId) as Material;
            Color color = material != null ? material.Color : null;
            _project.Materials.Add(new BridgeMaterial
            {
                MaterialId = id,
                ObjMaterialName = MaterialKey(sourceKey, id),
                SourceModelKey = sourceKey,
                SourceName = material != null ? material.Name : "默认材质",
                Color = IsValidColor(color)
                    ? new[] { color.Red / 255.0, color.Green / 255.0, color.Blue / 255.0 }
                    : new[] { 0.65, 0.65, 0.65 },
                Transparency = material != null ? material.Transparency / 100.0 : 0.0
            });
        }

        private static void AddMaterialKey(BridgeElement record, string key)
        {
            if (record == null || string.IsNullOrWhiteSpace(key))
                return;
            if (!record.MaterialKeys.Contains(key))
                record.MaterialKeys.Add(key);
        }

        private static string MaterialKey(string sourceModelKey, int id)
        {
            return "SLBH_M_" + SafeObjToken(sourceModelKey) + "_" + id.ToString(CultureInfo.InvariantCulture);
        }

        private string GetFamilyName(Element element)
        {
            FamilyInstance instance = element as FamilyInstance;
            if (instance != null && instance.Symbol != null && instance.Symbol.Family != null)
                return instance.Symbol.Family.Name;
            Element type = CurrentDocument.GetElement(element.GetTypeId());
            Parameter parameter = type != null ? type.get_Parameter(BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM) : null;
            return parameter != null ? parameter.AsString() ?? type.Name ?? "" : type != null ? type.Name ?? "" : "";
        }

        private string GetTypeName(Element element)
        {
            Element type = CurrentDocument.GetElement(element.GetTypeId());
            return type != null ? type.Name ?? "未命名类型" : element.Name ?? "未命名类型";
        }

        private string GetLevelName(Element element)
        {
            ElementId levelId = element.LevelId;
            if (levelId != null && levelId != ElementId.InvalidElementId)
            {
                Element level = CurrentDocument.GetElement(levelId);
                if (level != null)
                    return level.Name;
            }

            BuiltInParameter[] candidates =
            {
                BuiltInParameter.FAMILY_LEVEL_PARAM,
                BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM,
                BuiltInParameter.WALL_BASE_CONSTRAINT,
                BuiltInParameter.LEVEL_PARAM,
                BuiltInParameter.SCHEDULE_LEVEL_PARAM,
                BuiltInParameter.STAIRS_BASE_LEVEL_PARAM,
                BuiltInParameter.ROOF_BASE_LEVEL_PARAM,
                BuiltInParameter.RBS_START_LEVEL_PARAM
            };

            foreach (BuiltInParameter bip in candidates)
            {
                Parameter parameter = element.get_Parameter(bip);
                if (parameter != null && parameter.StorageType == StorageType.ElementId)
                {
                    Element level = CurrentDocument.GetElement(parameter.AsElementId());
                    if (level != null)
                        return level.Name;
                }
            }
            return "未分类楼层";
        }

        private string GetWorksetName(Element element)
        {
            try
            {
                // Linked elements carry WorksetIds of the link document, not of the host.
                Workset workset = CurrentDocument.GetWorksetTable().GetWorkset(element.WorksetId);
                return workset != null ? workset.Name : "";
            }
            catch { return ""; }
        }

        private string GetParameterText(Element element, BuiltInParameter parameterId)
        {
            try
            {
                Parameter parameter = element.get_Parameter(parameterId);
                if (parameter == null)
                    return "";
                string value = parameter.AsString();
                if (string.IsNullOrWhiteSpace(value))
                    value = parameter.AsValueString();
                return value ?? "";
            }
            catch { return ""; }
        }

        private string GetElementIdParameterName(Element element, params BuiltInParameter[] parameterIds)
        {
            foreach (BuiltInParameter parameterId in parameterIds)
            {
                try
                {
                    Parameter parameter = element.get_Parameter(parameterId);
                    if (parameter != null && parameter.StorageType == StorageType.ElementId)
                    {
                        Element target = CurrentDocument.GetElement(parameter.AsElementId());
                        if (target != null)
                            return target.Name ?? "";
                    }
                }
                catch { }
            }
            return "";
        }

        private static double GetDoubleParameter(Element element, params BuiltInParameter[] parameterIds)
        {
            foreach (BuiltInParameter parameterId in parameterIds)
            {
                try
                {
                    Parameter parameter = element.get_Parameter(parameterId);
                    if (parameter != null && parameter.StorageType == StorageType.Double)
                        return parameter.AsDouble();
                }
                catch { }
            }
            return 0.0;
        }

        private bool HasMepConnector(Element element)
        {
            try
            {
                MEPCurve curve = element as MEPCurve;
                if (curve != null)
                    return curve.ConnectorManager != null && curve.ConnectorManager.Connectors != null && curve.ConnectorManager.Connectors.Size > 0;
                FamilyInstance instance = element as FamilyInstance;
                ConnectorManager manager = instance != null && instance.MEPModel != null ? instance.MEPModel.ConnectorManager : null;
                return manager != null && manager.Connectors != null && manager.Connectors.Size > 0;
            }
            catch { return false; }
        }

        private string GetLodHint(Element element)
        {
            if (_profile == ExportProfile.Fine)
                return "FULL";
            try
            {
                // Only the size matters here. A host view cannot be passed for linked elements, so
                // fall back to the model bounding box (link-local, same dimensions).
                BoundingBoxXYZ box = IsLinkedElement ? element.get_BoundingBox(null) : element.get_BoundingBox(_view);
                if (box == null)
                    return "FULL";
                double max = Math.Max(Math.Max(Math.Abs(box.Max.X - box.Min.X), Math.Abs(box.Max.Y - box.Min.Y)), Math.Abs(box.Max.Z - box.Min.Z)) * FeetToMeters;
                if (_profile == ExportProfile.Fast && max <= 0.60) return "PROXY";
                if (_profile == ExportProfile.Standard && max <= 0.35) return "BOUNDS";
            }
            catch { }
            return "FULL";
        }

        private static string BuildDisplayName(string category, string type)
        {
            string shortCategory = (category ?? "")
                .Replace("结构", "")
                .Replace("建筑", "")
                .Replace("常规模型", "模型")
                .Trim();
            return SanitizeName(shortCategory + "_" + type);
        }

        private static string GuessDiscipline(string category, string family, string type)
        {
            string value = string.Join(" ", new[] { category ?? "", family ?? "", type ?? "" });
            if (ContainsAny(value, "中央空调", "空调", "室内机", "天花机", "风机盘管", "FCU", "VRV", "VRF", "AHU", "风管", "风口", "机械设备")) return "暖通";
            if (ContainsAny(value, "喷头", "喷淋", "消火栓", "消防")) return "消防";
            if (ContainsAny(value, "管道", "管件", "卫生器具", "给水", "排水")) return "给排水";
            if (ContainsAny(value, "桥架", "线管", "电气", "照明", "配电")) return "电气";
            if (ContainsAny(value, "结构", "钢筋", "桁架", "基础")) return "结构";
            if (ContainsAny(value, "墙", "门", "窗", "楼板", "屋顶", "幕墙", "楼梯", "栏杆")) return "建筑";
            return "其他";
        }

        internal static string ClassifySystemCode(string name, string typeName, string abbreviation, string classification)
        {
            string text = string.Join(" ", new[] { name, typeName, abbreviation, classification }).ToLowerInvariant();

            if (ContainsAny(text, "排烟", "smoke exhaust", "smoke_exhaust")) return "SMOKE_EXHAUST";
            if (ContainsAny(text, "补风", "makeup air", "make-up air")) return "MAKEUP_AIR";
            if (ContainsAny(text, "新风", "fresh air", "outdoor air")) return "FRESH_AIR";
            if (ContainsAny(text, "回风", "return air", "returnair")) return "RETURN_AIR";
            if (ContainsAny(text, "排风", "exhaust air", "exhaustair")) return "EXHAUST_AIR";
            if (ContainsAny(text, "送风", "supply air", "supplyair")) return "SUPPLY_AIR";
            if (ContainsAny(text, "消防", "喷淋", "消火栓", "fire protection", "firesprinkler", "wet fire")) return "FIRE_PROTECTION";
            if (ContainsAny(text, "生活给水", "给水", "domestic cold water", "domestic hot water", "watersupply")) return "WATER_SUPPLY";
            if (ContainsAny(text, "污水", "废水", "排水", "sanitary", "drainage")) return "DRAINAGE";
            if (ContainsAny(text, "冷冻水供", "chilled water supply")) return "CHW_SUPPLY";
            if (ContainsAny(text, "冷冻水回", "chilled water return")) return "CHW_RETURN";
            if (ContainsAny(text, "冷却水供", "condenser water supply")) return "CW_SUPPLY";
            if (ContainsAny(text, "冷却水回", "condenser water return")) return "CW_RETURN";
            if (ContainsAny(text, "supplyhydronic")) return "HYDRONIC_SUPPLY";
            if (ContainsAny(text, "returnhydronic")) return "HYDRONIC_RETURN";
            return string.IsNullOrWhiteSpace(text) ? "UNASSIGNED" : "OTHER_SYSTEM";
        }

        private static bool ContainsAny(string source, params string[] values)
        {
            if (source == null)
                return false;
            return values.Any(value => source.IndexOf(value, StringComparison.OrdinalIgnoreCase) >= 0);
        }

        private static string NormalizeCode(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
                return "UNCATEGORIZED";
            return new string(value.ToUpperInvariant().Select(c => char.IsLetterOrDigit(c) ? c : '_').ToArray()).Trim('_');
        }

        private static string SanitizeName(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
                return "未命名构件";
            char[] invalid = { '/', '\\', ':', '*', '?', '"', '<', '>', '|', '\n', '\r', '\t' };
            foreach (char c in invalid)
                value = value.Replace(c, '_');
            return value.Length > 60 ? value.Substring(0, 60) : value;
        }

        private static string FirstNonEmpty(params string[] values)
        {
            foreach (string value in values)
            {
                if (!string.IsNullOrWhiteSpace(value))
                    return value;
            }
            return "";
        }

        private string BuildStableElementKey(Element element)
        {
            string uniqueId = Safe(() => element.UniqueId);
            if (string.IsNullOrWhiteSpace(uniqueId))
                uniqueId = element.Id.IntegerValue.ToString(CultureInfo.InvariantCulture);

            LinkExportContext link = CurrentLinkContext;
            if (link == null)
                return "HOST|" + uniqueId;

            return link.SourceModelKey + "|" + link.LinkInstanceId.ToString(CultureInfo.InvariantCulture) + "|" + uniqueId;
        }

        internal static string StableDocumentId(Document document)
        {
            string value = "";
            if (document != null)
            {
                try
                {
                    ModelPath central = document.GetWorksharingCentralModelPath();
                    if (central != null)
                        value = ModelPathUtils.ConvertModelPathToUserVisiblePath(central);
                }
                catch { }
                if (string.IsNullOrWhiteSpace(value))
                    value = Safe(() => document.PathName);
                if (string.IsNullOrWhiteSpace(value))
                    value = Safe(() => document.Title);
            }
            return ShortHash(string.IsNullOrWhiteSpace(value) ? "UNKNOWN_DOCUMENT" : value);
        }

        private static string TransformFingerprint(Transform transform)
        {
            double[] values = TransformToArray(transform);
            var builder = new StringBuilder();
            foreach (double value in values)
                builder.Append(Math.Round(value, 6).ToString("R", CultureInfo.InvariantCulture)).Append(',');
            return builder.ToString();
        }

        private static bool TransformsAlmostEqual(Transform a, Transform b)
        {
            double[] av = TransformToArray(a);
            double[] bv = TransformToArray(b);
            for (int i = 0; i < av.Length; i++)
            {
                if (Math.Abs(av[i] - bv[i]) > 1e-6)
                    return false;
            }
            return true;
        }

        private bool ViewHasActiveSectionBox()
        {
            try { return _view != null && _view.IsSectionBoxActive; }
            catch { return false; }
        }

        internal static string SafeObjToken(string value)
        {
            return ShortHash(value ?? "");
        }

        internal static string ShortHash(string value)
        {
            byte[] hash = Hasher.ComputeHash(Encoding.UTF8.GetBytes(value ?? ""));
            var result = new StringBuilder(24);
            for (int i = 0; i < 12 && i < hash.Length; i++)
                result.Append(hash[i].ToString("x2", CultureInfo.InvariantCulture));
            return result.ToString();
        }

        private static string Safe(Func<string> getter)
        {
            try { return getter() ?? ""; }
            catch { return ""; }
        }

        private static Color SafeColor(Func<Color> getter)
        {
            try { return getter(); }
            catch { return null; }
        }

        private static ElementId SafeElementId(Func<ElementId> getter)
        {
            try { return getter() ?? ElementId.InvalidElementId; }
            catch { return ElementId.InvalidElementId; }
        }

        private static bool IsValidColor(Color color)
        {
            try { return color != null && color.IsValid; }
            catch { return false; }
        }

        private static double[] ColorToArray(Color color)
        {
            if (!IsValidColor(color))
                return null;
            return new[] { color.Red / 255.0, color.Green / 255.0, color.Blue / 255.0 };
        }

        private static string ColorToHex(Color color)
        {
            if (!IsValidColor(color))
                return "";
            return string.Format(CultureInfo.InvariantCulture, "#{0:X2}{1:X2}{2:X2}", color.Red, color.Green, color.Blue);
        }

        private static double[] PointToMeters(XYZ point)
        {
            return new[] { point.X * FeetToMeters, point.Y * FeetToMeters, point.Z * FeetToMeters };
        }
    }
}
