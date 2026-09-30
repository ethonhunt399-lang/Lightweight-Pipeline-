using System.Collections.Generic;
using System.Runtime.Serialization;

namespace SLBH.RevitBridge
{
    // Data contract of the MEP coordination export (manifest.json + mep.json).
    // All lengths are meters, all coordinates are Revit internal coordinates of the host
    // document (link transforms already applied), all angles are radians.

    [DataContract]
    public sealed class MepManifest
    {
        public const string CurrentSchemaVersion = "0.2.0";

        public MepManifest()
        {
            SchemaName = "slbh.mep.manifest";
            SchemaVersion = CurrentSchemaVersion;
            ExporterVersion = MepExportCommand.ExporterVersion;
            Unit = "meter";
            CoordinateSpace = "revit_internal_host";
            Files = new MepFiles();
            Scope = new MepScope();
            BasePoints = new List<MepBasePoint>();
            Levels = new List<MepLevel>();
            Grids = new List<MepGrid>();
            Rooms = new List<MepRoom>();
            Links = new List<MepLink>();
            Counts = new MepCounts();
            Timings = new MepTimings();
            Diagnostics = new List<MepDiagnostic>();
        }

        [DataMember(Name = "schema_name", Order = 0)] public string SchemaName { get; set; }
        [DataMember(Name = "schema_version", Order = 1)] public string SchemaVersion { get; set; }
        [DataMember(Name = "exporter_version", Order = 2)] public string ExporterVersion { get; set; }
        [DataMember(Name = "created_at_utc", Order = 3)] public string CreatedAtUtc { get; set; }
        [DataMember(Name = "revit_version", Order = 4)] public string RevitVersion { get; set; }
        [DataMember(Name = "project_name", Order = 5)] public string ProjectName { get; set; }
        [DataMember(Name = "source_document", Order = 6)] public string SourceDocument { get; set; }
        [DataMember(Name = "view_name", Order = 7)] public string ViewName { get; set; }
        [DataMember(Name = "unit", Order = 8)] public string Unit { get; set; }
        [DataMember(Name = "coordinate_space", Order = 9)] public string CoordinateSpace { get; set; }
        [DataMember(Name = "files", Order = 10)] public MepFiles Files { get; set; }
        [DataMember(Name = "scope", Order = 11)] public MepScope Scope { get; set; }
        [DataMember(Name = "project_location", Order = 12)] public MepProjectLocation ProjectLocation { get; set; }
        [DataMember(Name = "base_points", Order = 13)] public List<MepBasePoint> BasePoints { get; set; }
        [DataMember(Name = "levels", Order = 14)] public List<MepLevel> Levels { get; set; }
        [DataMember(Name = "grids", Order = 15)] public List<MepGrid> Grids { get; set; }
        [DataMember(Name = "rooms", Order = 16)] public List<MepRoom> Rooms { get; set; }
        [DataMember(Name = "links", Order = 17)] public List<MepLink> Links { get; set; }
        [DataMember(Name = "counts", Order = 18)] public MepCounts Counts { get; set; }
        [DataMember(Name = "timings_ms", Order = 19)] public MepTimings Timings { get; set; }
        [DataMember(Name = "diagnostics", Order = 20)] public List<MepDiagnostic> Diagnostics { get; set; }
    }

    [DataContract]
    public sealed class MepFiles
    {
        public MepFiles()
        {
            Mep = "mep.json";
            Project = "project.json";
            ModelObj = "model.obj";
            PrototypesObj = "prototypes.obj";
        }

        [DataMember(Name = "mep", Order = 0)] public string Mep { get; set; }
        [DataMember(Name = "project", Order = 1)] public string Project { get; set; }
        [DataMember(Name = "model_obj", Order = 2)] public string ModelObj { get; set; }
        [DataMember(Name = "prototypes_obj", Order = 3)] public string PrototypesObj { get; set; }
    }

    [DataContract]
    public sealed class MepScope
    {
        [DataMember(Name = "section_box_active", Order = 0)] public bool SectionBoxActive { get; set; }
        [DataMember(Name = "section_box_min", Order = 1)] public double[] SectionBoxMin { get; set; }
        [DataMember(Name = "section_box_max", Order = 2)] public double[] SectionBoxMax { get; set; }
        [DataMember(Name = "section_box_transform", Order = 3)] public double[] SectionBoxTransform { get; set; }
        [DataMember(Name = "include_linked_models", Order = 4)] public bool IncludeLinkedModels { get; set; }
        [DataMember(Name = "host_rule", Order = 5)] public string HostRule { get; set; }
        [DataMember(Name = "link_rule", Order = 6)] public string LinkRule { get; set; }
    }

    [DataContract]
    public sealed class MepProjectLocation
    {
        [DataMember(Name = "name", Order = 0)] public string Name { get; set; }
        [DataMember(Name = "east_west_m", Order = 1)] public double EastWestMeters { get; set; }
        [DataMember(Name = "north_south_m", Order = 2)] public double NorthSouthMeters { get; set; }
        [DataMember(Name = "elevation_m", Order = 3)] public double ElevationMeters { get; set; }
        [DataMember(Name = "angle_rad", Order = 4)] public double AngleRadians { get; set; }
        [DataMember(Name = "internal_to_shared", Order = 5)] public double[] InternalToShared { get; set; }
    }

    [DataContract]
    public sealed class MepBasePoint
    {
        [DataMember(Name = "kind", Order = 0)] public string Kind { get; set; }
        [DataMember(Name = "position", Order = 1)] public double[] Position { get; set; }
        [DataMember(Name = "east_west_m", Order = 2)] public double EastWestMeters { get; set; }
        [DataMember(Name = "north_south_m", Order = 3)] public double NorthSouthMeters { get; set; }
        [DataMember(Name = "elevation_m", Order = 4)] public double ElevationMeters { get; set; }
        [DataMember(Name = "angle_to_true_north_rad", Order = 5)] public double AngleToTrueNorthRadians { get; set; }
    }

    [DataContract]
    public sealed class MepLevel
    {
        [DataMember(Name = "source_model_key", Order = 0)] public string SourceModelKey { get; set; }
        [DataMember(Name = "unique_id", Order = 1)] public string UniqueId { get; set; }
        [DataMember(Name = "name", Order = 2)] public string Name { get; set; }
        [DataMember(Name = "elevation_m", Order = 3)] public double ElevationMeters { get; set; }
        [DataMember(Name = "display_elevation_m", Order = 4)] public double DisplayElevationMeters { get; set; }
    }

    [DataContract]
    public sealed class MepGrid
    {
        [DataMember(Name = "source_model_key", Order = 0)] public string SourceModelKey { get; set; }
        [DataMember(Name = "unique_id", Order = 1)] public string UniqueId { get; set; }
        [DataMember(Name = "name", Order = 2)] public string Name { get; set; }
        [DataMember(Name = "curve_kind", Order = 3)] public string CurveKind { get; set; }
        [DataMember(Name = "start", Order = 4)] public double[] Start { get; set; }
        [DataMember(Name = "end", Order = 5)] public double[] End { get; set; }
        [DataMember(Name = "center", Order = 6)] public double[] Center { get; set; }
        [DataMember(Name = "radius_m", Order = 7)] public double RadiusMeters { get; set; }
    }

    [DataContract]
    public sealed class MepRoom
    {
        public MepRoom()
        {
            Boundary = new List<List<double[]>>();
        }

        [DataMember(Name = "source_model_key", Order = 0)] public string SourceModelKey { get; set; }
        [DataMember(Name = "unique_id", Order = 1)] public string UniqueId { get; set; }
        [DataMember(Name = "name", Order = 2)] public string Name { get; set; }
        [DataMember(Name = "number", Order = 3)] public string Number { get; set; }
        [DataMember(Name = "level", Order = 4)] public string Level { get; set; }
        [DataMember(Name = "bbox_min", Order = 5)] public double[] BoundingBoxMin { get; set; }
        [DataMember(Name = "bbox_max", Order = 6)] public double[] BoundingBoxMax { get; set; }
        [DataMember(Name = "boundary", Order = 7)] public List<List<double[]>> Boundary { get; set; }
    }

    [DataContract]
    public sealed class MepLink
    {
        [DataMember(Name = "source_model_key", Order = 0)] public string SourceModelKey { get; set; }
        [DataMember(Name = "document_name", Order = 1)] public string DocumentName { get; set; }
        [DataMember(Name = "link_instance_id", Order = 2)] public int LinkInstanceId { get; set; }
        [DataMember(Name = "link_instance_unique_id", Order = 3)] public string LinkInstanceUniqueId { get; set; }
        [DataMember(Name = "transform", Order = 4)] public double[] Transform { get; set; }
        [DataMember(Name = "status", Order = 5)] public string Status { get; set; }
    }

    [DataContract]
    public sealed class MepCounts
    {
        [DataMember(Name = "curves", Order = 0)] public int Curves { get; set; }
        [DataMember(Name = "family_instances", Order = 1)] public int FamilyInstances { get; set; }
        [DataMember(Name = "connectors", Order = 2)] public int Connectors { get; set; }
        [DataMember(Name = "connections", Order = 3)] public int Connections { get; set; }
        [DataMember(Name = "crossing_scope_boundary", Order = 4)] public int CrossingScopeBoundary { get; set; }
        [DataMember(Name = "with_insulation", Order = 5)] public int WithInsulation { get; set; }
        [DataMember(Name = "support_candidates", Order = 6)] public int SupportCandidates { get; set; }
        [DataMember(Name = "levels", Order = 7)] public int Levels { get; set; }
        [DataMember(Name = "grids", Order = 8)] public int Grids { get; set; }
        [DataMember(Name = "rooms", Order = 9)] public int Rooms { get; set; }
        [DataMember(Name = "diagnostics", Order = 10)] public int Diagnostics { get; set; }
        [DataMember(Name = "diagnostics_dropped", Order = 11)] public int DiagnosticsDropped { get; set; }
    }

    [DataContract]
    public sealed class MepTimings
    {
        [DataMember(Name = "geometry_export", Order = 0)] public long GeometryExport { get; set; }
        [DataMember(Name = "mep_collect", Order = 1)] public long MepCollect { get; set; }
        [DataMember(Name = "context_collect", Order = 2)] public long ContextCollect { get; set; }
        [DataMember(Name = "write", Order = 3)] public long Write { get; set; }
    }

    [DataContract]
    public sealed class MepDiagnostic
    {
        [DataMember(Name = "key", Order = 0)] public string Key { get; set; }
        [DataMember(Name = "field", Order = 1)] public string Field { get; set; }
        [DataMember(Name = "message", Order = 2)] public string Message { get; set; }
    }

    [DataContract]
    public sealed class MepData
    {
        public MepData()
        {
            SchemaName = "slbh.mep.elements";
            SchemaVersion = MepManifest.CurrentSchemaVersion;
            Unit = "meter";
            Curves = new List<MepCurveRecord>();
            FamilyInstances = new List<MepFamilyRecord>();
        }

        [DataMember(Name = "schema_name", Order = 0)] public string SchemaName { get; set; }
        [DataMember(Name = "schema_version", Order = 1)] public string SchemaVersion { get; set; }
        [DataMember(Name = "unit", Order = 2)] public string Unit { get; set; }
        [DataMember(Name = "curves", Order = 3)] public List<MepCurveRecord> Curves { get; set; }
        [DataMember(Name = "family_instances", Order = 4)] public List<MepFamilyRecord> FamilyInstances { get; set; }
    }

    [DataContract]
    public abstract class MepElementRecord
    {
        protected MepElementRecord()
        {
            Connectors = new List<MepConnectorRecord>();
        }

        [DataMember(Name = "key", Order = 0)] public string Key { get; set; }
        [DataMember(Name = "element_id", Order = 1)] public int ElementId { get; set; }
        [DataMember(Name = "unique_id", Order = 2)] public string UniqueId { get; set; }
        [DataMember(Name = "source_model_key", Order = 3)] public string SourceModelKey { get; set; }
        [DataMember(Name = "kind", Order = 4)] public string Kind { get; set; }
        [DataMember(Name = "category", Order = 5)] public string Category { get; set; }
        [DataMember(Name = "builtin_category", Order = 6)] public string BuiltInCategory { get; set; }
        [DataMember(Name = "family", Order = 7)] public string Family { get; set; }
        [DataMember(Name = "type", Order = 8)] public string Type { get; set; }
        [DataMember(Name = "level", Order = 9)] public string Level { get; set; }
        [DataMember(Name = "workset", Order = 10)] public string Workset { get; set; }
        [DataMember(Name = "pinned", Order = 11)] public bool Pinned { get; set; }
        [DataMember(Name = "system_name", Order = 12)] public string SystemName { get; set; }
        [DataMember(Name = "system_type_name", Order = 13)] public string SystemTypeName { get; set; }
        [DataMember(Name = "system_classification", Order = 14)] public string SystemClassification { get; set; }
        [DataMember(Name = "system_abbreviation", Order = 15)] public string SystemAbbreviation { get; set; }
        [DataMember(Name = "system_code", Order = 16)] public string SystemCode { get; set; }
        [DataMember(Name = "service_type", Order = 17)] public string ServiceType { get; set; }
        [DataMember(Name = "size_text", Order = 18)] public string SizeText { get; set; }
        [DataMember(Name = "insulation_thickness_m", Order = 19)] public double InsulationThicknessMeters { get; set; }
        [DataMember(Name = "insulation_type", Order = 20)] public string InsulationType { get; set; }
        [DataMember(Name = "lining_thickness_m", Order = 21)] public double LiningThicknessMeters { get; set; }
        [DataMember(Name = "bbox_min", Order = 22)] public double[] BoundingBoxMin { get; set; }
        [DataMember(Name = "bbox_max", Order = 23)] public double[] BoundingBoxMax { get; set; }
        [DataMember(Name = "crosses_scope_boundary", Order = 24)] public bool CrossesScopeBoundary { get; set; }
        [DataMember(Name = "connectors", Order = 25)] public List<MepConnectorRecord> Connectors { get; set; }
        [DataMember(Name = "fingerprint", Order = 26)] public string Fingerprint { get; set; }
    }

    [DataContract]
    public sealed class MepCurveRecord : MepElementRecord
    {
        [DataMember(Name = "shape", Order = 30)] public string Shape { get; set; }
        [DataMember(Name = "outer_diameter_m", Order = 31)] public double OuterDiameterMeters { get; set; }
        [DataMember(Name = "nominal_diameter_m", Order = 32)] public double NominalDiameterMeters { get; set; }
        [DataMember(Name = "inner_diameter_m", Order = 33)] public double InnerDiameterMeters { get; set; }
        [DataMember(Name = "width_m", Order = 34)] public double WidthMeters { get; set; }
        [DataMember(Name = "height_m", Order = 35)] public double HeightMeters { get; set; }
        [DataMember(Name = "curve_kind", Order = 36)] public string CurveKind { get; set; }
        [DataMember(Name = "start", Order = 37)] public double[] Start { get; set; }
        [DataMember(Name = "end", Order = 38)] public double[] End { get; set; }
        [DataMember(Name = "points", Order = 39)] public List<double[]> Points { get; set; }
        [DataMember(Name = "length_m", Order = 40)] public double LengthMeters { get; set; }
        [DataMember(Name = "slope", Order = 41)] public double Slope { get; set; }
        [DataMember(Name = "section_x_axis", Order = 42)] public double[] SectionXAxis { get; set; }
        [DataMember(Name = "section_y_axis", Order = 43)] public double[] SectionYAxis { get; set; }
        [DataMember(Name = "reference_level", Order = 44)] public string ReferenceLevel { get; set; }
        [DataMember(Name = "reference_level_elevation_m", Order = 45)] public double ReferenceLevelElevationMeters { get; set; }
        [DataMember(Name = "start_offset_m", Order = 46)] public double StartOffsetMeters { get; set; }
        [DataMember(Name = "end_offset_m", Order = 47)] public double EndOffsetMeters { get; set; }
    }

    [DataContract]
    public sealed class MepFamilyRecord : MepElementRecord
    {
        [DataMember(Name = "part_type", Order = 30)] public string PartType { get; set; }
        [DataMember(Name = "angle_rad", Order = 31)] public double AngleRadians { get; set; }
        [DataMember(Name = "transform", Order = 32)] public double[] Transform { get; set; }
        [DataMember(Name = "host_key", Order = 33)] public string HostKey { get; set; }
        [DataMember(Name = "is_support_candidate", Order = 34)] public bool IsSupportCandidate { get; set; }
    }

    [DataContract]
    public sealed class MepConnectorRecord
    {
        public MepConnectorRecord()
        {
            Connected = new List<MepConnectionRef>();
        }

        [DataMember(Name = "id", Order = 0)] public int Id { get; set; }
        [DataMember(Name = "connector_type", Order = 1)] public string ConnectorType { get; set; }
        [DataMember(Name = "domain", Order = 2)] public string Domain { get; set; }
        [DataMember(Name = "shape", Order = 3)] public string Shape { get; set; }
        [DataMember(Name = "origin", Order = 4)] public double[] Origin { get; set; }
        [DataMember(Name = "direction", Order = 5)] public double[] Direction { get; set; }
        [DataMember(Name = "x_axis", Order = 6)] public double[] XAxis { get; set; }
        [DataMember(Name = "diameter_m", Order = 7)] public double DiameterMeters { get; set; }
        [DataMember(Name = "width_m", Order = 8)] public double WidthMeters { get; set; }
        [DataMember(Name = "height_m", Order = 9)] public double HeightMeters { get; set; }
        [DataMember(Name = "connected", Order = 10)] public List<MepConnectionRef> Connected { get; set; }
    }

    [DataContract]
    public sealed class MepConnectionRef
    {
        [DataMember(Name = "key", Order = 0)] public string Key { get; set; }
        [DataMember(Name = "connector_id", Order = 1)] public int ConnectorId { get; set; }
    }
}
