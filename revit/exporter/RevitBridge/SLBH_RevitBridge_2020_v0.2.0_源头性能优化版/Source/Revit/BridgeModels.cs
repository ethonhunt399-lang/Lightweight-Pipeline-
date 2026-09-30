using System.Collections.Generic;
using System.Runtime.Serialization;

namespace SLBH.RevitBridge
{
    [DataContract]
    public sealed class BridgeProject
    {
        public BridgeProject()
        {
            SchemaVersion = "0.3.6";
            ExporterVersion = "0.3.7";
            ImporterVersion = "0.3.6";
            MinImporterVersion = "0.3.0";
            BridgeVersion = "0.3.7";
            ExportProfile = "STANDARD";
            Unit = "meter";
            Elements = new List<BridgeElement>();
            Materials = new List<BridgeMaterial>();
            Prototypes = new List<BridgePrototype>();
            Links = new List<BridgeLinkInstance>();
            SkippedLinks = new List<BridgeSkippedLink>();
            FamilyRules = new List<BridgeFamilyRule>();
            Performance = new BridgePerformance();
            Warnings = new List<string>();
        }

        [DataMember(Name = "schema_version")] public string SchemaVersion { get; set; }
        [DataMember(Name = "exporter_version")] public string ExporterVersion { get; set; }
        [DataMember(Name = "importer_version")] public string ImporterVersion { get; set; }
        [DataMember(Name = "min_importer_version")] public string MinImporterVersion { get; set; }
        [DataMember(Name = "bridge_version")] public string BridgeVersion { get; set; }
        [DataMember(Name = "project_name")] public string ProjectName { get; set; }
        [DataMember(Name = "source_document")] public string SourceDocument { get; set; }
        [DataMember(Name = "revit_version")] public string RevitVersion { get; set; }
        [DataMember(Name = "view_name")] public string ViewName { get; set; }
        [DataMember(Name = "view_display_style")] public string ViewDisplayStyle { get; set; }
        [DataMember(Name = "view_detail_level")] public string ViewDetailLevel { get; set; }
        [DataMember(Name = "export_profile")] public string ExportProfile { get; set; }
        [DataMember(Name = "unit")] public string Unit { get; set; }
        [DataMember(Name = "elements")] public List<BridgeElement> Elements { get; set; }
        [DataMember(Name = "materials")] public List<BridgeMaterial> Materials { get; set; }
        [DataMember(Name = "prototypes")] public List<BridgePrototype> Prototypes { get; set; }
        [DataMember(Name = "links")] public List<BridgeLinkInstance> Links { get; set; }
        [DataMember(Name = "skipped_links")] public List<BridgeSkippedLink> SkippedLinks { get; set; }
        [DataMember(Name = "family_rules")] public List<BridgeFamilyRule> FamilyRules { get; set; }
        [DataMember(Name = "performance")] public BridgePerformance Performance { get; set; }
        [DataMember(Name = "warnings")] public List<string> Warnings { get; set; }
    }

    [DataContract]
    public sealed class BridgeExportOptions
    {
        public BridgeExportOptions()
        {
            ExportLinkedModels = true;
            LinkOrganization = "SOURCE_MODEL";
            LinkDetailPolicy = "CURRENT_VIEW_AND_LINK_DISPLAY";
            FamilyActions = new Dictionary<string, string>();
        }

        [DataMember(Name = "export_linked_models")] public bool ExportLinkedModels { get; set; }
        [DataMember(Name = "link_organization")] public string LinkOrganization { get; set; }
        [DataMember(Name = "link_detail_policy")] public string LinkDetailPolicy { get; set; }
        public Dictionary<string, string> FamilyActions { get; set; }
    }

    [DataContract]
    public sealed class BridgeFamilyRule
    {
        public BridgeFamilyRule()
        {
            RecommendedAction = "PROTOTYPE";
            Action = "PROTOTYPE";
        }

        [DataMember(Name = "rule_key")] public string RuleKey { get; set; }
        [DataMember(Name = "category")] public string Category { get; set; }
        [DataMember(Name = "family")] public string Family { get; set; }
        [DataMember(Name = "type")] public string Type { get; set; }
        [DataMember(Name = "count")] public int Count { get; set; }
        [DataMember(Name = "estimated_triangles_each")] public int EstimatedTrianglesEach { get; set; }
        [DataMember(Name = "estimated_triangles_total")] public long EstimatedTrianglesTotal { get; set; }
        [DataMember(Name = "max_size_m")] public double MaxSizeMeters { get; set; }
        [DataMember(Name = "is_high_burden")] public bool IsHighBurden { get; set; }
        [DataMember(Name = "is_instance_candidate")] public bool IsInstanceCandidate { get; set; }
        [DataMember(Name = "recommended_action")] public string RecommendedAction { get; set; }
        [DataMember(Name = "action")] public string Action { get; set; }
        [DataMember(Name = "reason")] public string Reason { get; set; }
    }

    [DataContract]
    public sealed class BridgeLinkInstance
    {
        [DataMember(Name = "source_model_key")] public string SourceModelKey { get; set; }
        [DataMember(Name = "source_document_name")] public string SourceDocumentName { get; set; }
        [DataMember(Name = "source_document_guid")] public string SourceDocumentGuid { get; set; }
        [DataMember(Name = "link_instance_id")] public int LinkInstanceId { get; set; }
        [DataMember(Name = "link_instance_name")] public string LinkInstanceName { get; set; }
        [DataMember(Name = "link_type_name")] public string LinkTypeName { get; set; }
        [DataMember(Name = "link_transform")] public double[] LinkTransform { get; set; }
        [DataMember(Name = "link_depth")] public int LinkDepth { get; set; }
        [DataMember(Name = "element_count")] public int ElementCount { get; set; }
        [DataMember(Name = "status")] public string Status { get; set; }
    }

    [DataContract]
    public sealed class BridgeSkippedLink
    {
        [DataMember(Name = "link_name")] public string LinkName { get; set; }
        [DataMember(Name = "link_instance_id")] public int LinkInstanceId { get; set; }
        [DataMember(Name = "status")] public string Status { get; set; }
        [DataMember(Name = "reason")] public string Reason { get; set; }
        [DataMember(Name = "link_depth")] public int LinkDepth { get; set; }
    }

    [DataContract]
    public sealed class BridgeElement
    {
        public BridgeElement()
        {
            SystemTypeId = -1;
            MaterialKeys = new List<string>();
            GeometryMode = "mesh";
            LodHint = "FULL";
            SourceModelKey = "HOST";
            ExportAction = "AUTO";
            PrimitiveType = "";
        }

        [DataMember(Name = "element_id")] public int ElementId { get; set; }
        [DataMember(Name = "unique_id")] public string UniqueId { get; set; }
        [DataMember(Name = "stable_element_key")] public string StableElementKey { get; set; }
        [DataMember(Name = "is_linked_element")] public bool IsLinkedElement { get; set; }
        [DataMember(Name = "source_document_name")] public string SourceDocumentName { get; set; }
        [DataMember(Name = "source_document_guid")] public string SourceDocumentGuid { get; set; }
        [DataMember(Name = "source_model_key")] public string SourceModelKey { get; set; }
        [DataMember(Name = "link_instance_id")] public int LinkInstanceId { get; set; }
        [DataMember(Name = "link_instance_name")] public string LinkInstanceName { get; set; }
        [DataMember(Name = "link_type_name")] public string LinkTypeName { get; set; }
        [DataMember(Name = "linked_element_id")] public int LinkedElementId { get; set; }
        [DataMember(Name = "linked_unique_id")] public string LinkedUniqueId { get; set; }
        [DataMember(Name = "link_transform")] public double[] LinkTransform { get; set; }
        [DataMember(Name = "link_depth")] public int LinkDepth { get; set; }
        [DataMember(Name = "obj_name")] public string ObjName { get; set; }
        [DataMember(Name = "display_name")] public string DisplayName { get; set; }
        [DataMember(Name = "category")] public string Category { get; set; }
        [DataMember(Name = "source_category")] public string SourceCategory { get; set; }
        [DataMember(Name = "category_code")] public string CategoryCode { get; set; }
        [DataMember(Name = "discipline")] public string Discipline { get; set; }
        [DataMember(Name = "family")] public string Family { get; set; }
        [DataMember(Name = "type")] public string Type { get; set; }
        [DataMember(Name = "level")] public string Level { get; set; }
        [DataMember(Name = "workset")] public string Workset { get; set; }

        [DataMember(Name = "system_name")] public string SystemName { get; set; }
        [DataMember(Name = "system_type_name")] public string SystemTypeName { get; set; }
        [DataMember(Name = "system_type_id")] public int SystemTypeId { get; set; }
        [DataMember(Name = "system_classification")] public string SystemClassification { get; set; }
        [DataMember(Name = "system_abbreviation")] public string SystemAbbreviation { get; set; }
        [DataMember(Name = "system_code")] public string SystemCode { get; set; }
        [DataMember(Name = "system_source")] public string SystemSource { get; set; }
        [DataMember(Name = "has_mep_connector")] public bool HasMepConnector { get; set; }

        [DataMember(Name = "display_color")] public double[] DisplayColor { get; set; }
        [DataMember(Name = "display_color_hex")] public string DisplayColorHex { get; set; }
        [DataMember(Name = "display_color_source")] public string DisplayColorSource { get; set; }
        [DataMember(Name = "has_display_override")] public bool HasDisplayOverride { get; set; }
        [DataMember(Name = "material_keys")] public List<string> MaterialKeys { get; set; }

        [DataMember(Name = "geometry_mode")] public string GeometryMode { get; set; }
        [DataMember(Name = "primitive_type")] public string PrimitiveType { get; set; }
        [DataMember(Name = "prototype_id")] public string PrototypeId { get; set; }
        [DataMember(Name = "prototype_obj_name")] public string PrototypeObjName { get; set; }
        [DataMember(Name = "transform")] public double[] Transform { get; set; }
        [DataMember(Name = "lod_hint")] public string LodHint { get; set; }
        [DataMember(Name = "export_action")] public string ExportAction { get; set; }
        [DataMember(Name = "family_rule_key")] public string FamilyRuleKey { get; set; }

        [DataMember(Name = "curve_kind")] public string CurveKind { get; set; }
        [DataMember(Name = "curve_start")] public double[] CurveStart { get; set; }
        [DataMember(Name = "curve_end")] public double[] CurveEnd { get; set; }
        [DataMember(Name = "diameter_m")] public double DiameterMeters { get; set; }
        [DataMember(Name = "length_m")] public double LengthMeters { get; set; }
        [DataMember(Name = "slope")] public double Slope { get; set; }
    }

    [DataContract]
    public sealed class BridgePrototype
    {
        public BridgePrototype()
        {
            MaterialKeys = new List<string>();
        }

        [DataMember(Name = "prototype_id")] public string PrototypeId { get; set; }
        [DataMember(Name = "obj_name")] public string ObjName { get; set; }
        [DataMember(Name = "source_model_key")] public string SourceModelKey { get; set; }
        [DataMember(Name = "category")] public string Category { get; set; }
        [DataMember(Name = "family")] public string Family { get; set; }
        [DataMember(Name = "type")] public string Type { get; set; }
        [DataMember(Name = "material_keys")] public List<string> MaterialKeys { get; set; }
        [DataMember(Name = "vertex_count")] public int VertexCount { get; set; }
        [DataMember(Name = "face_count")] public int FaceCount { get; set; }
        [DataMember(Name = "instance_count")] public int InstanceCount { get; set; }
    }

    [DataContract]
    public sealed class BridgeMaterial
    {
        [DataMember(Name = "material_id")] public int MaterialId { get; set; }
        [DataMember(Name = "obj_material_name")] public string ObjMaterialName { get; set; }
        [DataMember(Name = "source_model_key")] public string SourceModelKey { get; set; }
        [DataMember(Name = "source_name")] public string SourceName { get; set; }
        [DataMember(Name = "color")] public double[] Color { get; set; }
        [DataMember(Name = "transparency")] public double Transparency { get; set; }
    }

    [DataContract]
    public sealed class BridgePerformance
    {
        [DataMember(Name = "source_elements")] public int SourceElements { get; set; }
        [DataMember(Name = "host_elements")] public int HostElements { get; set; }
        [DataMember(Name = "linked_elements")] public int LinkedElements { get; set; }
        [DataMember(Name = "link_files")] public int LinkFiles { get; set; }
        [DataMember(Name = "link_instances")] public int LinkInstances { get; set; }
        [DataMember(Name = "skipped_links")] public int SkippedLinks { get; set; }
        [DataMember(Name = "skipped_elements")] public int SkippedElements { get; set; }
        [DataMember(Name = "rule_skipped_elements")] public int RuleSkippedElements { get; set; }
        [DataMember(Name = "mesh_elements")] public int MeshElements { get; set; }
        [DataMember(Name = "forced_full_mesh")] public int ForcedFullMesh { get; set; }
        [DataMember(Name = "forced_prototype_candidates")] public int ForcedPrototypeCandidates { get; set; }
        [DataMember(Name = "prototype_instances")] public int PrototypeInstances { get; set; }
        [DataMember(Name = "unique_prototypes")] public int UniquePrototypes { get; set; }
        [DataMember(Name = "parametric_round")] public int ParametricRound { get; set; }
        [DataMember(Name = "vertices_written")] public long VerticesWritten { get; set; }
        [DataMember(Name = "faces_written")] public long FacesWritten { get; set; }
        [DataMember(Name = "estimated_repeated_faces_avoided")] public long EstimatedRepeatedFacesAvoided { get; set; }
    }

    internal sealed class SystemInfo
    {
        public SystemInfo()
        {
            Name = "未分配系统";
            TypeName = "";
            TypeId = -1;
            Classification = "";
            Abbreviation = "";
            Code = "UNASSIGNED";
            Source = "none";
            MaterialId = Autodesk.Revit.DB.ElementId.InvalidElementId;
        }

        public string Name { get; set; }
        public string TypeName { get; set; }
        public int TypeId { get; set; }
        public string Classification { get; set; }
        public string Abbreviation { get; set; }
        public string Code { get; set; }
        public string Source { get; set; }
        public Autodesk.Revit.DB.Color FillColor { get; set; }
        public Autodesk.Revit.DB.ElementId MaterialId { get; set; }
    }

    internal sealed class DisplayInfo
    {
        public DisplayInfo()
        {
            Source = "none";
        }

        public Autodesk.Revit.DB.Color Color { get; set; }
        public string Source { get; set; }
        public bool HasOverride { get; set; }
    }

    public enum ExportProfile
    {
        Fast,
        Standard,
        Fine
    }
}
