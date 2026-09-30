import json
import math
import os
import time
from collections import Counter

import bpy
from mathutils import Matrix, Vector

from .materials import MaterialResolver
from .display_proxy import ensure_project_layers, mark_source_object
from .optimization import (
    geometry_signature,
    normalized_payload,
    set_viewport_mode,
    should_use_viewport_proxy,
)
from .semantics import enrich_metadata, family_type_key, system_series_from_metadata


IMPORTER_VERSION = (0, 3, 6)


def import_bridge_package(
    context,
    selected_path: str,
    organize_mode: str,
    material_mode: str,
    round_quality: str,
    optimization=None,
):
    optimization = optimization or {}
    profile = optimization.get("profile", "STANDARD")
    share_instances = bool(optimization.get("share_instances", True))
    viewport_mode = optimization.get("viewport_mode", "BOUNDS")
    max_size = float(optimization.get("max_size", 0.35))
    min_faces = int(optimization.get("min_faces", 300))
    repeat_threshold = int(optimization.get("repeat_threshold", 4))

    package_dir, json_path = _resolve_package(selected_path)
    model_path = os.path.join(package_dir, "model.obj")
    prototypes_path = os.path.join(package_dir, "prototypes.obj")

    with open(json_path, "r", encoding="utf-8-sig") as handle:
        project = json.load(handle)
    version_warnings = _check_project_version(project)

    elements = [enrich_metadata(item) for item in project.get("elements", [])]
    element_map = {item.get("obj_name"): item for item in elements if item.get("obj_name")}
    material_map = {item["obj_material_name"]: item for item in project.get("materials", [])}
    parsed_unique = _parse_obj(model_path) if os.path.isfile(model_path) else {}
    parsed_prototypes = _parse_obj(prototypes_path) if os.path.isfile(prototypes_path) else {}

    root = _get_or_create_collection(
        context.scene.collection,
        _safe_name(project.get("project_name", "Revit项目")),
    )
    legacy_root = root
    root = _create_import_root(context.scene.collection, project, package_dir)
    _remove_empty_legacy_root(context.scene.collection, legacy_root)
    root["slbh.bridge_root"] = True
    root["slbh.project_name"] = project.get("project_name", root.name)
    root["slbh.view_name"] = project.get("view_name", "")
    root["slbh.source_document"] = project.get("source_document", "")
    root["slbh.import_package_path"] = package_dir
    root["slbh.export_profile"] = project.get("export_profile", "STANDARD")
    ensure_project_layers(root)
    resolver = MaterialResolver(context, material_map, material_mode)

    group_counts = Counter(family_type_key(metadata) for metadata in elements)
    geometry_cache = {}
    source_prototype_meshes = {}

    object_count = 0
    parametric_count = 0
    shared_count = 0
    source_instance_count = 0
    viewport_proxy_count = 0
    unique_mesh_count = 0
    used_materials = set()
    imported_element_keys = set()

    # Unique geometry retained in model.obj.
    for obj_name, mesh_data in parsed_unique.items():
        metadata = enrich_metadata(element_map.get(obj_name, {}))
        obj, reused = _create_mesh_object(
            mesh_data, metadata, project, resolver, geometry_cache if share_instances else None
        )
        if reused:
            shared_count += 1
        else:
            unique_mesh_count += 1
        _link_imported_object(root, obj, metadata, organize_mode)
        imported_element_keys.add(_element_key(metadata))
        object_count += 1
        used_materials.update(mat.name for mat in obj.data.materials if mat)
        if _should_proxy_imported(obj, metadata, profile, group_counts, max_size, min_faces, repeat_threshold):
            set_viewport_mode(obj, viewport_mode)
            viewport_proxy_count += 1

    # v0.2 source-level prototypes: prototype mesh is parsed once, every Revit instance
    # creates only a lightweight Object that shares that Mesh datablock.
    for metadata in elements:
        if metadata.get("geometry_mode") != "prototype_instance":
            continue
        prototype_id = str(metadata.get("prototype_id") or "")
        prototype_obj_name = str(metadata.get("prototype_obj_name") or prototype_id)
        mesh_data = parsed_prototypes.get(prototype_obj_name)
        if not prototype_id or mesh_data is None:
            continue

        mesh = source_prototype_meshes.get(prototype_id)
        if mesh is None:
            mesh = _create_prototype_mesh(mesh_data, metadata, resolver, prototype_id)
            source_prototype_meshes[prototype_id] = mesh
            unique_mesh_count += 1
        elif share_instances:
            shared_count += 1

        object_mesh = mesh if share_instances else mesh.copy()
        obj = _create_prototype_instance_object(object_mesh, metadata, project)
        _link_imported_object(root, obj, metadata, organize_mode)
        imported_element_keys.add(_element_key(metadata))
        object_count += 1
        source_instance_count += 1
        used_materials.update(mat.name for mat in obj.data.materials if mat)
        if _should_proxy_imported(obj, metadata, profile, group_counts, max_size, min_faces, repeat_threshold):
            set_viewport_mode(obj, viewport_mode)
            viewport_proxy_count += 1

    # Parameterized straight round ducts/pipes.
    for metadata in elements:
        element_key = _element_key(metadata)
        if element_key in imported_element_keys or metadata.get("geometry_mode") != "straight_round_curve":
            continue
        obj, reused = _create_straight_round_object(
            metadata, project, resolver, round_quality, geometry_cache if share_instances else None
        )
        if obj is None:
            continue
        if reused:
            shared_count += 1
        else:
            unique_mesh_count += 1
        _link_imported_object(root, obj, metadata, organize_mode)
        imported_element_keys.add(element_key)
        object_count += 1
        parametric_count += 1
        used_materials.update(mat.name for mat in obj.data.materials if mat)

    performance = project.get("performance") or {}
    return {
        "objects": object_count,
        "materials": len(used_materials),
        "source_elements": len(elements),
        "host_elements": int(performance.get("host_elements", 0)),
        "linked_elements": int(performance.get("linked_elements", 0)),
        "link_files": int(performance.get("link_files", 0)),
        "link_instances": int(performance.get("link_instances", 0)),
        "skipped_links": project.get("skipped_links", []),
        "skipped_elements": int(performance.get("skipped_elements", 0)),
        "parametric_round": parametric_count,
        "source_instances": source_instance_count,
        "source_prototypes": len(source_prototype_meshes),
        "shared_instances": shared_count,
        "unique_meshes": unique_mesh_count,
        "viewport_proxies": viewport_proxy_count,
        "avoided_faces": int(performance.get("estimated_repeated_faces_avoided", 0)),
        "bridge_version": project.get("bridge_version", ""),
        "schema_version": project.get("schema_version", ""),
        "warnings": version_warnings,
    }


def _version_tuple(value):
    parts = []
    for item in str(value or "0").replace("-", ".").split("."):
        digits = "".join(ch for ch in item if ch.isdigit())
        if digits == "":
            break
        parts.append(int(digits))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _check_project_version(project):
    warnings = []
    schema = project.get("schema_version")
    bridge = project.get("bridge_version", "")
    exporter = project.get("exporter_version", bridge or "")
    min_importer = project.get("min_importer_version", "0.1.0")

    if not schema:
        warnings.append("Legacy bridge package without schema_version; importing in compatibility mode.")
        schema = bridge or "0.2.0"

    schema_version = _version_tuple(schema)
    min_importer_version = _version_tuple(min_importer)

    if min_importer_version > IMPORTER_VERSION:
        raise ValueError(
            "Bridge package requires importer {} or newer; current importer is {}.".format(
                min_importer,
                ".".join(str(v) for v in IMPORTER_VERSION),
            )
        )

    if schema_version[:2] > IMPORTER_VERSION[:2]:
        raise ValueError(
            "Unsupported bridge schema {} from exporter {}; please update the Blender add-on.".format(
                schema,
                exporter or "unknown",
            )
        )

    if schema_version > IMPORTER_VERSION:
        warnings.append(
            "Bridge schema {} is newer than importer {}; importing with conservative compatibility.".format(
                schema,
                ".".join(str(v) for v in IMPORTER_VERSION),
            )
        )

    return warnings


def _link_imported_object(root, obj, metadata, organize_mode):
    layers = ensure_project_layers(root)
    mark_source_object(obj)
    target = _target_collection(layers["source"], metadata, organize_mode)
    target.objects.link(obj)
    obj["slbh.project_root"] = root.name
    obj["slbh.import_id"] = root.get("slbh.import_id", "")
    obj["slbh.import_package_path"] = root.get("slbh.import_package_path", "")


def _element_key(metadata):
    stable = str(metadata.get("stable_element_key") or "").strip()
    if stable:
        return stable
    source = str(metadata.get("source_model_key") or ("LINK" if metadata.get("is_linked_element") else "HOST"))
    unique = str(metadata.get("linked_unique_id") or metadata.get("unique_id") or metadata.get("element_id") or "")
    link_instance = str(metadata.get("link_instance_id") or "")
    return "|".join((source, link_instance, unique))


def _should_proxy_imported(obj, metadata, profile, group_counts, max_size, min_faces, repeat_threshold):
    lod_hint = str(metadata.get("lod_hint") or "FULL")
    if lod_hint in {"PROXY", "BOUNDS"}:
        return True
    return should_use_viewport_proxy(
        obj, metadata, profile, group_counts[family_type_key(metadata)],
        max_size, min_faces, repeat_threshold,
    )


def _create_prototype_mesh(mesh_data, metadata, resolver, prototype_id):
    display_name = _safe_name(metadata.get("family") or metadata.get("type") or "共享原型")
    source_material_names = []
    for _, mat_name in mesh_data["faces"]:
        if mat_name not in source_material_names:
            source_material_names.append(mat_name)
    resolved_materials = [resolver.resolve(mat_name, metadata) for mat_name in source_material_names]

    mesh = bpy.data.meshes.new(display_name + "_共享网格")
    mesh.from_pydata(mesh_data["vertices"], [], [face[0] for face in mesh_data["faces"]])
    mesh.update()
    for material in resolved_materials:
        mesh.materials.append(material)
    index_by_name = {name: index for index, name in enumerate(source_material_names)}
    for polygon, (_, mat_name) in zip(mesh.polygons, mesh_data["faces"]):
        polygon.material_index = index_by_name.get(mat_name, 0)
    mesh["slbh.prototype_id"] = prototype_id
    mesh["slbh.source_instance_mesh"] = True
    return mesh


def _create_prototype_instance_object(mesh, metadata, project):
    display_name = _safe_name(metadata.get("display_name") or metadata.get("type") or "Revit实例")
    obj = bpy.data.objects.new(display_name, mesh)
    matrix_values = metadata.get("transform")
    if isinstance(matrix_values, (list, tuple)) and len(matrix_values) >= 16:
        try:
            rows = tuple(tuple(float(matrix_values[row * 4 + col]) for col in range(4)) for row in range(4))
            obj.matrix_world = Matrix(rows)
        except (TypeError, ValueError):
            pass
    _write_properties(obj, metadata, project)
    obj["slbh.prototype_id"] = metadata.get("prototype_id", "")
    obj["slbh.is_instance"] = True
    obj["slbh.source_level_instance"] = True
    _write_base_material_property(obj)
    return obj


def _create_mesh_object(mesh_data, metadata, project, resolver, prototype_cache=None):
    display_name = _safe_name(metadata.get("display_name") or metadata.get("obj_name") or "Revit构件")

    source_material_names = []
    for _, mat_name in mesh_data["faces"]:
        if mat_name not in source_material_names:
            source_material_names.append(mat_name)
    resolved_materials = [resolver.resolve(mat_name, metadata) for mat_name in source_material_names]
    resolved_names = [material.name for material in resolved_materials]

    center, local_vertices = normalized_payload(mesh_data)
    signature = geometry_signature(
        local_vertices,
        mesh_data["faces"],
        resolved_names,
        metadata,
    )

    mesh = prototype_cache.get(signature) if prototype_cache is not None else None
    reused = mesh is not None

    if mesh is None:
        mesh = bpy.data.meshes.new(display_name + "_网格")
        mesh.from_pydata(local_vertices, [], [face[0] for face in mesh_data["faces"]])
        mesh.update()

        for material in resolved_materials:
            mesh.materials.append(material)

        index_by_name = {name: index for index, name in enumerate(source_material_names)}
        for polygon, (_, mat_name) in zip(mesh.polygons, mesh_data["faces"]):
            polygon.material_index = index_by_name.get(mat_name, 0)

        if prototype_cache is not None:
            prototype_cache[signature] = mesh

    obj = bpy.data.objects.new(display_name, mesh)
    obj.location = center
    _write_properties(obj, metadata, project)
    obj["slbh.prototype_id"] = signature[:16]
    obj["slbh.is_instance"] = bool(reused)
    _write_base_material_property(obj)
    return obj, reused


def _create_straight_round_object(metadata, project, resolver, quality, prototype_cache=None):
    start_value = metadata.get("curve_start")
    end_value = metadata.get("curve_end")
    try:
        start = Vector(tuple(float(v) for v in start_value[:3]))
        end = Vector(tuple(float(v) for v in end_value[:3]))
        diameter = float(metadata.get("diameter_m", 0.0))
    except (TypeError, ValueError, AttributeError):
        return None, False

    axis = end - start
    length = axis.length
    if length <= 1e-8 or diameter <= 1e-8:
        return None, False

    sides = _round_sides(diameter, quality)
    radius = diameter * 0.5
    half = length * 0.5

    # Canonical cylinder along local Z.  Object transform handles position/direction,
    # allowing identical lengths/diameters to share one mesh even when placed elsewhere.
    vertices = []
    for z in (-half, half):
        for index in range(sides):
            angle = 2.0 * math.pi * index / sides
            vertices.append((radius * math.cos(angle), radius * math.sin(angle), z))

    faces = []
    for index in range(sides):
        nxt = (index + 1) % sides
        faces.append((index, nxt, sides + nxt, sides + index))
    faces.append(tuple(reversed(range(sides))))
    faces.append(tuple(range(sides, sides * 2)))

    material = resolver.resolve_for_element(metadata)
    signature_text = (
        f"ROUND|{round(diameter, 6)}|{round(length, 6)}|{sides}|{material.name}|"
        f"{metadata.get('source_model_key','HOST')}|{metadata.get('category','')}|{metadata.get('family','')}|{metadata.get('type','')}"
    )
    signature = str(abs(hash(signature_text)))
    mesh = prototype_cache.get(signature) if prototype_cache is not None else None
    reused = mesh is not None

    display_name = _safe_name(metadata.get("display_name") or "圆形管线")
    if mesh is None:
        mesh = bpy.data.meshes.new(display_name + "_低模网格")
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        for polygon in mesh.polygons:
            polygon.use_smooth = len(polygon.vertices) == 4
        mesh.materials.append(material)
        if prototype_cache is not None:
            prototype_cache[signature] = mesh

    obj = bpy.data.objects.new(display_name, mesh)
    midpoint = (start + end) * 0.5
    direction = axis.normalized()
    obj.location = midpoint
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = Vector((0.0, 0.0, 1.0)).rotation_difference(direction)

    _write_properties(obj, metadata, project)
    obj["slbh.parametric_round"] = True
    obj["slbh.round_sides"] = sides
    obj["slbh.diameter_m"] = diameter
    obj["slbh.length_m"] = float(metadata.get("length_m", length))
    obj["slbh.prototype_id"] = signature[:16]
    obj["slbh.is_instance"] = bool(reused)
    _write_base_material_property(obj)
    return obj, reused


def _round_sides(diameter, quality):
    if quality == "FAST":
        return 8 if diameter <= 0.30 else 12
    if quality == "FINE":
        return 16 if diameter <= 0.30 else 24
    return 12 if diameter <= 0.30 else 16


def _resolve_package(selected_path: str):
    path = bpy.path.abspath(selected_path or "")
    if not path:
        raise ValueError("请选择 project.json 或 .slbh 文件夹中的文件")
    if os.path.isdir(path):
        package_dir = path
        json_path = os.path.join(path, "project.json")
    else:
        package_dir = os.path.dirname(path)
        json_path = path if os.path.basename(path).lower() == "project.json" else os.path.join(package_dir, "project.json")
    if not os.path.isfile(json_path):
        raise FileNotFoundError(f"找不到项目数据：{json_path}")
    return package_dir, json_path


def _parse_obj(path: str):
    global_vertices = []
    objects = {}
    current_name = None
    current_material = "SLBH_MAT_DEFAULT"

    def ensure_object(name):
        if name not in objects:
            objects[name] = {"faces_global": []}
        return objects[name]

    with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            token = parts[0]
            if token == "v" and len(parts) >= 4:
                global_vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif token == "o" and len(parts) >= 2:
                current_name = " ".join(parts[1:])
                ensure_object(current_name)
            elif token == "usemtl" and len(parts) >= 2:
                current_material = " ".join(parts[1:])
            elif token == "f" and current_name and len(parts) >= 4:
                indices = []
                for item in parts[1:]:
                    raw_index = item.split("/")[0]
                    index = int(raw_index)
                    if index < 0:
                        index = len(global_vertices) + index
                    else:
                        index -= 1
                    indices.append(index)
                ensure_object(current_name)["faces_global"].append((indices, current_material))

    result = {}
    for name, data in objects.items():
        if not data["faces_global"]:
            continue
        used = []
        seen = set()
        for face, _ in data["faces_global"]:
            for index in face:
                if index not in seen:
                    seen.add(index)
                    used.append(index)
        remap = {global_index: local_index for local_index, global_index in enumerate(used)}
        result[name] = {
            "vertices": [global_vertices[index] for index in used],
            "faces": [([remap[index] for index in face], material) for face, material in data["faces_global"]],
        }
    return result


def _write_properties(obj, metadata, project):
    obj["slbh.element_id"] = int(metadata.get("element_id", -1))
    obj["slbh.unique_id"] = metadata.get("unique_id", "")
    obj["slbh.stable_element_key"] = _element_key(metadata)
    obj["slbh.is_linked_element"] = bool(metadata.get("is_linked_element", False))
    obj["slbh.source_model_key"] = metadata.get("source_model_key", "HOST")
    obj["slbh.source_document_name"] = metadata.get("source_document_name", project.get("source_document", ""))
    obj["slbh.source_document_guid"] = metadata.get("source_document_guid", "")
    obj["slbh.link_instance_id"] = int(metadata.get("link_instance_id", -1))
    obj["slbh.link_instance_name"] = metadata.get("link_instance_name", "")
    obj["slbh.link_type_name"] = metadata.get("link_type_name", "")
    obj["slbh.linked_element_id"] = int(metadata.get("linked_element_id", -1))
    obj["slbh.linked_unique_id"] = metadata.get("linked_unique_id", "")
    obj["slbh.link_depth"] = int(metadata.get("link_depth", 0))
    obj["slbh.source_category"] = metadata.get("source_category", metadata.get("category", "未分类"))
    obj["slbh.category"] = metadata.get("category", "未分类")
    obj["slbh.category_code"] = metadata.get("category_code", "UNCATEGORIZED")
    obj["slbh.family"] = metadata.get("family", "")
    obj["slbh.type"] = metadata.get("type", "")
    obj["slbh.level"] = metadata.get("level", "未分类楼层")
    obj["slbh.discipline"] = metadata.get("discipline", "其他")
    obj["slbh.workset"] = metadata.get("workset", "")
    obj["slbh.system_name"] = metadata.get("system_name", "")
    obj["slbh.system_type"] = metadata.get("system_type_name", "")
    obj["slbh.system_type_id"] = int(metadata.get("system_type_id", -1))
    obj["slbh.system_classification"] = metadata.get("system_classification", "")
    obj["slbh.system_abbreviation"] = metadata.get("system_abbreviation", "")
    obj["slbh.system_code"] = metadata.get("system_code", "NON_MEP")
    obj["slbh.system_series"] = metadata.get("system_series") or system_series_from_metadata(metadata)
    obj["slbh.system_source"] = metadata.get("system_source", "none")
    obj["slbh.semantic_override"] = metadata.get("semantic_override", "")
    obj["slbh.display_color_hex"] = metadata.get("display_color_hex", "")
    obj["slbh.display_color_source"] = metadata.get("display_color_source", "none")
    display_color = metadata.get("display_color")
    if isinstance(display_color, (list, tuple)) and len(display_color) >= 3:
        obj["slbh.display_color"] = [float(display_color[0]), float(display_color[1]), float(display_color[2])]
    obj["slbh.geometry_mode"] = metadata.get("geometry_mode", "mesh")
    obj["slbh.primitive_type"] = metadata.get("primitive_type", "")
    obj["slbh.prototype_id"] = metadata.get("prototype_id", obj.get("slbh.prototype_id", ""))
    obj["slbh.lod_hint"] = metadata.get("lod_hint", "FULL")
    obj["slbh.export_action"] = metadata.get("export_action", "")
    obj["slbh.family_rule_key"] = metadata.get("family_rule_key", "")
    obj["slbh.has_mep_connector"] = bool(metadata.get("has_mep_connector", False))
    obj["slbh.source_document"] = project.get("source_document", "")
    obj["slbh.bridge_version"] = project.get("bridge_version", "")
    obj["slbh.schema_version"] = project.get("schema_version", "")
    obj["slbh.exporter_version"] = project.get("exporter_version", "")
    obj["slbh.importer_version"] = project.get("importer_version", "")
    obj["slbh.min_importer_version"] = project.get("min_importer_version", "")
    obj["slbh.revit_version"] = project.get("revit_version", "")
    obj["slbh.view_display_style"] = project.get("view_display_style", "")
    obj["slbh.export_profile"] = project.get("export_profile", "STANDARD")


def _write_base_material_property(obj):
    names = []
    for slot in obj.material_slots:
        material = slot.material
        if material is None:
            continue
        base = material.get("slbh.base_material_name", material.name)
        if base not in names:
            names.append(base)
    obj["slbh.base_material"] = " | ".join(names)


def _target_collection(root, raw_metadata, mode):
    metadata = enrich_metadata(raw_metadata)
    source_model = _safe_name(_source_model_label(metadata))
    link_instance = _safe_name(metadata.get("link_instance_name") or metadata.get("source_model_key") or source_model)
    level = _safe_name(metadata.get("level") or "未分类楼层")
    discipline = _safe_name(metadata.get("discipline") or "其他")
    category = _safe_name(metadata.get("category") or "未分类")
    series = _safe_name(metadata.get("system_series") or system_series_from_metadata(metadata))
    system = _safe_name(metadata.get("system_type_name") or metadata.get("system_name") or series)

    if mode == "SOURCE_LEVEL_DISCIPLINE_SERIES":
        return _get_or_create_collection(
            _get_or_create_collection(
                _get_or_create_collection(_get_or_create_collection(root, source_model), level),
                discipline,
            ),
            series,
        )
    if mode == "SOURCE_INSTANCE_LEVEL_DISCIPLINE_SERIES":
        return _get_or_create_collection(
            _get_or_create_collection(
                _get_or_create_collection(
                    _get_or_create_collection(_get_or_create_collection(root, source_model), link_instance),
                    level,
                ),
                discipline,
            ),
            series,
        )
    if mode == "LEVEL_SOURCE_DISCIPLINE_SERIES":
        return _get_or_create_collection(
            _get_or_create_collection(
                _get_or_create_collection(_get_or_create_collection(root, level), source_model),
                discipline,
            ),
            series,
        )
    if mode == "LEVEL_DISCIPLINE_SERIES":
        return _get_or_create_collection(
            _get_or_create_collection(_get_or_create_collection(root, level), discipline),
            series,
        )
    if mode == "LEVEL_DISCIPLINE":
        return _get_or_create_collection(_get_or_create_collection(root, level), discipline)
    if mode == "LEVEL_SYSTEM":
        return _get_or_create_collection(_get_or_create_collection(root, level), series)
    if mode == "DISCIPLINE_SYSTEM":
        return _get_or_create_collection(_get_or_create_collection(root, discipline), series)
    if mode == "LEVEL":
        return _get_or_create_collection(root, level)
    if mode == "DISCIPLINE":
        return _get_or_create_collection(root, discipline)
    if mode == "SYSTEM":
        return _get_or_create_collection(root, series)
    if mode == "SYSTEM_NAME":
        return _get_or_create_collection(root, system)
    return _get_or_create_collection(root, category)


def _source_model_label(metadata):
    if not metadata.get("is_linked_element"):
        return "Host Model"
    return metadata.get("source_document_name") or metadata.get("link_type_name") or metadata.get("source_model_key") or "Linked Model"


def _create_import_root(scene_root, project, package_dir):
    label = _import_root_label(project, package_dir)
    name = _unique_root_name(scene_root, label)
    collection = bpy.data.collections.new(name)
    collection["slbh.logical_name"] = name
    collection["slbh.import_base_label"] = label
    collection["slbh.import_id"] = "{}_{}".format(int(time.time()), len(scene_root.children) + 1)
    scene_root.children.link(collection)
    return collection


def _remove_empty_legacy_root(scene_root, collection):
    if collection is None:
        return
    if collection.get("slbh.bridge_root"):
        return
    if len(collection.objects) > 0 or len(collection.children) > 0:
        return
    try:
        scene_root.children.unlink(collection)
    except Exception:
        pass
    if collection.users == 0:
        bpy.data.collections.remove(collection)


def _import_root_label(project, package_dir):
    project_name = str(project.get("project_name") or "Revit项目").strip()
    view_name = str(project.get("view_name") or "").strip()
    package_name = os.path.basename(os.path.normpath(package_dir or "")).strip()
    parts = [project_name]
    if view_name and view_name.lower() not in project_name.lower():
        parts.append(view_name)
    if package_name and package_name.lower() not in " ".join(parts).lower():
        parts.append(package_name)
    return _safe_name(" - ".join(parts))


def _unique_root_name(scene_root, base_name):
    base = _safe_name(base_name or "Revit项目")
    existing = {child.name for child in scene_root.children}
    existing.update(str(child.get("slbh.logical_name", "")) for child in scene_root.children)
    if base not in existing and bpy.data.collections.get(base) is None:
        return base
    for index in range(2, 10000):
        candidate = "{} - {:02d}".format(base, index)
        if candidate not in existing and bpy.data.collections.get(candidate) is None:
            return candidate
    return "{} - {}".format(base, int(time.time()))


def _get_or_create_collection(parent, name):
    logical = str(name)
    for child in parent.children:
        if child.get("slbh.logical_name") == logical or child.name == logical:
            return child
    collection = bpy.data.collections.new(logical)
    collection["slbh.logical_name"] = logical
    collection["slbh.parent_collection"] = parent.name
    parent.children.link(collection)
    return collection


def _safe_name(value):
    text = str(value or "未命名")
    for char in '/\\:*?"<>|\n\r\t':
        text = text.replace(char, "_")
    return text[:63]
