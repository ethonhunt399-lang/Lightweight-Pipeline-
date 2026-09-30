"""Overall display proxy tools for large SLBH Revit Bridge scenes."""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict

import bpy
from mathutils import Vector


ADDON_PROXY_VERSION = "0.3.6"

SOURCE_LAYER_NAME = "00_BIM源模型"
OVERALL_PROXY_LAYER_NAME = "10_整体展示代理"
SYSTEM_PROXY_LAYER_NAME = "20_系统图代理"
LOCAL_FINE_LAYER_NAME = "30_局部精细模型"
LIGHT_LAYER_NAME = "灯光"
CAMERA_LAYER_NAME = "相机"
OUTPUT_LAYER_NAME = "出图场景"

OVERALL_STATS_TEXT = "SLBH_整体展示代理统计"
SMALL_REPORT_TEXT = "SLBH_展示代理小构件扫描"


def ensure_project_layers(root):
    layers = {
        "source": _get_or_create_collection(root, SOURCE_LAYER_NAME),
        "overall": _get_or_create_collection(root, OVERALL_PROXY_LAYER_NAME),
        "system": _get_or_create_collection(root, SYSTEM_PROXY_LAYER_NAME),
        "local": _get_or_create_collection(root, LOCAL_FINE_LAYER_NAME),
        "lights": _get_or_create_collection(root, LIGHT_LAYER_NAME),
        "cameras": _get_or_create_collection(root, CAMERA_LAYER_NAME),
        "output": _get_or_create_collection(root, OUTPUT_LAYER_NAME),
    }
    layers["source"]["slbh.layer_role"] = "SOURCE_MODEL"
    layers["overall"]["slbh.layer_role"] = "OVERALL_DISPLAY_PROXY"
    layers["system"]["slbh.layer_role"] = "SYSTEM_DIAGRAM_PROXY_RESERVED"
    layers["local"]["slbh.layer_role"] = "LOCAL_FINE_RESERVED"
    return layers


def mark_source_object(obj):
    obj["slbh.is_source_object"] = True
    obj["slbh.proxy_type"] = "SOURCE_BIM_OBJECT"


def scan_overall_proxy_sources(context, settings):
    objects = _source_objects(context, settings)
    stats = _source_stats(objects)
    groups = _small_component_groups(objects)
    lines = [
        "SLBH overall display proxy source scan",
        "=" * 64,
        f"Source objects: {stats['source_objects']}",
        f"Visible source objects: {stats['visible_source_objects']}",
        f"Unique meshes: {stats['unique_meshes']}",
        f"Source polygons counted per object: {stats['source_faces']}",
        "",
        "High-load small family/type groups",
        "-" * 64,
    ]
    for index, (key, data) in enumerate(groups[:40], 1):
        category, family, type_name = key
        lines.append(
            f"{index:02d}. {category} | {family or '-'} | {type_name or '-'} | "
            f"count={data['count']} faces={data['faces']} max_size={data['max_size']:.3f}m "
            f"recommended={data['recommended']}"
        )
    if not groups:
        lines.append("No source objects matched the high-load small component scan.")
    _write_text(SMALL_REPORT_TEXT, "\n".join(lines))
    _update_proxy_status(context)
    return {"source_objects": stats["source_objects"], "groups": len(groups), "faces": stats["source_faces"]}


def rebuild_overall_display_proxy(context, settings):
    roots = _bridge_roots(context)
    if not roots:
        raise RuntimeError("No SLBH bridge project root was found.")

    delete_overall_display_proxy(context, restore_source=False)
    created_objects = []
    try:
        result = _create_overall_display_proxy(context, settings, created_objects)
    except Exception:
        for obj in created_objects:
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        raise

    if getattr(settings, "overall_hide_source_after_create", True):
        set_overall_display_mode(context, "PROXY_ONLY")
    _write_proxy_stats(result)
    return result


def delete_overall_display_proxy(context, restore_source=True):
    deleted = 0
    removed_meshes = 0
    for obj in list(context.scene.objects):
        if not obj.get("slbh.overall_display_proxy", False):
            continue
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        deleted += 1
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
            removed_meshes += 1

    for root in _bridge_roots(context):
        layers = ensure_project_layers(root)
        layers["overall"]["slbh.proxy_status"] = "MISSING"
        if restore_source:
            _set_collection_hidden(layers["source"], False)
            _set_collection_hidden(layers["overall"], True)
    return {"deleted": deleted, "removed_meshes": removed_meshes}


def set_overall_display_mode(context, mode):
    source_visible = mode in {"SOURCE_ONLY", "BOTH"}
    proxy_visible = mode in {"PROXY_ONLY", "BOTH"}
    if mode == "HIDE_ALL":
        source_visible = False
        proxy_visible = False

    roots = _bridge_roots(context)
    for root in roots:
        layers = ensure_project_layers(root)
        _set_collection_hidden(layers["source"], not source_visible)
        _set_collection_hidden(layers["overall"], not proxy_visible)

    return {"roots": len(roots), "source_visible": source_visible, "proxy_visible": proxy_visible}


def enter_large_model_mode(context):
    result = set_overall_display_mode(context, "PROXY_ONLY")
    try:
        context.space_data.overlay.show_names = False
        context.space_data.overlay.show_relationship_lines = False
    except Exception:
        pass
    return result


def _create_overall_display_proxy(context, settings, created_objects):
    objects = _source_objects(context, settings)
    source_stats = _source_stats(objects)
    groups = defaultdict(list)
    filtered = 0
    low_proxy = 0
    point_proxy = 0

    for obj in objects:
        action = _proxy_action(obj, settings)
        if action == "EXCLUDE":
            filtered += 1
            continue
        if action == "LOW":
            low_proxy += 1
        if action == "POINT":
            point_proxy += 1
        groups[_group_key(obj, getattr(settings, "overall_group_rule", "LEVEL_DISCIPLINE_SYSTEM_MATERIAL"))].append((obj, action))

    proxy_objects = 0
    proxy_faces = 0
    merged_source_objects = 0

    for root in _bridge_roots(context):
        ensure_project_layers(root)

    for key, items in sorted(groups.items(), key=lambda pair: str(pair[0])):
        root = _root_for_object(items[0][0], context)
        if root is None:
            continue
        layers = ensure_project_layers(root)
        proxy = _build_proxy_object(key, items, settings)
        if proxy is None:
            continue
        layers["overall"].objects.link(proxy)
        created_objects.append(proxy)
        proxy_objects += 1
        proxy_faces += len(proxy.data.polygons)
        merged_source_objects += len(items)

    for root in _bridge_roots(context):
        layers = ensure_project_layers(root)
        layers["overall"]["slbh.proxy_created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        layers["overall"]["slbh.proxy_plugin_version"] = ADDON_PROXY_VERSION
        layers["overall"]["slbh.proxy_kind"] = "OVERALL_DISPLAY"
        layers["overall"]["slbh.proxy_group_rule"] = getattr(settings, "overall_group_rule", "")
        layers["overall"]["slbh.proxy_small_mode"] = getattr(settings, "overall_small_mode", "")
        layers["overall"]["slbh.proxy_source_object_count"] = source_stats["source_objects"]
        layers["overall"]["slbh.proxy_status"] = "CURRENT"

    object_reduction = _ratio_reduction(source_stats["source_objects"], proxy_objects)
    face_reduction = _ratio_reduction(source_stats["source_faces"], proxy_faces)
    return {
        "source_objects": source_stats["source_objects"],
        "visible_source_objects": source_stats["visible_source_objects"],
        "unique_meshes": source_stats["unique_meshes"],
        "source_faces": source_stats["source_faces"],
        "proxy_objects": proxy_objects,
        "proxy_faces": proxy_faces,
        "geometry_nodes_instances": 0,
        "filtered_objects": filtered,
        "low_proxy_objects": low_proxy,
        "point_proxy_objects": point_proxy,
        "merged_source_objects": merged_source_objects,
        "object_reduction_percent": object_reduction,
        "face_reduction_percent": face_reduction,
    }


def _source_objects(context, settings):
    candidates = [
        obj for obj in context.scene.objects
        if obj.type == "MESH"
        and "slbh.element_id" in obj
        and not obj.get("slbh.overall_display_proxy", False)
        and not obj.get("slbh.merge_proxy", False)
    ]
    for obj in candidates:
        mark_source_object(obj)
    scope = getattr(settings, "overall_proxy_scope", "ALL_SOURCE")
    if scope == "SELECTED_OBJECTS":
        selected = {obj.name for obj in context.selected_objects}
        candidates = [obj for obj in candidates if obj.name in selected]
    elif scope == "VISIBLE_ONLY":
        candidates = [obj for obj in candidates if _visible_get(obj)]
    elif scope == "LEVEL_RANGE":
        start = _level_rank(getattr(settings, "overall_level_start", ""))
        end = _level_rank(getattr(settings, "overall_level_end", ""))
        if start is not None and end is not None and start > end:
            start, end = end, start
        candidates = [obj for obj in candidates if _level_in_range(obj.get("slbh.level", ""), start, end)]
    elif scope == "DISCIPLINE":
        discipline = getattr(settings, "overall_discipline", "ALL")
        if discipline != "ALL":
            candidates = [obj for obj in candidates if str(obj.get("slbh.discipline", "")) == discipline]
    elif scope == "ACTIVE_SOURCE_MODEL":
        active = context.active_object
        source_key = active.get("slbh.source_model_key") if active else None
        if source_key:
            candidates = [obj for obj in candidates if obj.get("slbh.source_model_key") == source_key]
    return candidates


def _source_stats(objects):
    unique_meshes = {obj.data.name for obj in objects if obj.type == "MESH" and obj.data is not None}
    faces = sum(len(obj.data.polygons) for obj in objects if obj.type == "MESH" and obj.data is not None)
    visible = sum(1 for obj in objects if _visible_get(obj))
    return {
        "source_objects": len(objects),
        "visible_source_objects": visible,
        "unique_meshes": len(unique_meshes),
        "source_faces": faces,
    }


def _small_component_groups(objects):
    groups = defaultdict(lambda: {"count": 0, "faces": 0, "max_size": 0.0, "recommended": ""})
    for obj in objects:
        key = (
            str(obj.get("slbh.category", "")),
            str(obj.get("slbh.family", "")),
            str(obj.get("slbh.type", "")),
        )
        item = groups[key]
        item["count"] += 1
        item["faces"] += len(obj.data.polygons) if obj.data else 0
        item["max_size"] = max(item["max_size"], max((float(v) for v in obj.dimensions), default=0.0))
    ranked = []
    for key, data in groups.items():
        text = " ".join(key)
        if data["count"] < 4 and data["faces"] < 1000:
            continue
        data["recommended"] = _recommended_small_action(text, data["max_size"])
        ranked.append((key, data))
    return sorted(ranked, key=lambda pair: pair[1]["faces"], reverse=True)


def _proxy_action(obj, settings):
    mode = getattr(settings, "overall_small_mode", "DEFAULT")
    if mode == "KEEP":
        return "KEEP"
    if mode == "LOW":
        return "LOW" if _is_small_component(obj) else "KEEP"
    if mode == "POINT":
        return "POINT" if _is_small_component(obj) else "KEEP"
    if mode == "EXCLUDE":
        return "EXCLUDE" if _is_small_component(obj) else "KEEP"

    text = _object_text(obj)
    if _contains(text, ("bolt", "fastener", "螺栓", "紧固", "hanger", "support", "支吊架")):
        return "EXCLUDE"
    if _contains(text, ("sprinkler", "喷头")):
        return "EXCLUDE"
    if _contains(text, ("diffuser", "air terminal", "风口", "散流器", "valve", "damper", "阀", "风阀", "light", "灯具")):
        return "LOW"
    if _is_main_route(obj):
        return "KEEP"
    return "LOW" if _is_small_component(obj) else "KEEP"


def _recommended_small_action(text, max_size):
    lowered = str(text or "").lower()
    if _contains(lowered, ("bolt", "fastener", "螺栓", "紧固", "hanger", "support", "支吊架", "sprinkler", "喷头")):
        return "EXCLUDE"
    if _contains(lowered, ("diffuser", "air terminal", "风口", "散流器", "valve", "damper", "阀", "风阀")):
        return "LOW"
    return "LOW" if max_size <= 0.60 else "KEEP"


def _is_small_component(obj):
    max_size = max((float(v) for v in obj.dimensions), default=0.0)
    return max_size > 0.0 and max_size <= 0.75


def _is_main_route(obj):
    text = _object_text(obj)
    return _contains(text, (
        "duct", "pipe", "cable tray", "conduit",
        "风管", "管道", "桥架", "线管", "立管",
        "mechanical equipment", "设备", "机组", "泵", "风机",
    ))


def _group_key(obj, rule):
    level = str(obj.get("slbh.level", "未分类楼层"))
    discipline = str(obj.get("slbh.discipline", "其他"))
    system = str(obj.get("slbh.system_series", "未分类系统"))
    material = str(obj.get("slbh.base_material", "")) or _active_material_name(obj)
    source = str(obj.get("slbh.source_model_key", "HOST"))

    if rule == "LEVEL_DISCIPLINE":
        return (source, level, discipline)
    if rule == "LEVEL_SYSTEM":
        return (source, level, system)
    if rule == "LEVEL_DISCIPLINE_SYSTEM":
        return (source, level, discipline, system)
    if rule == "LEVEL_SYSTEM_MATERIAL":
        return (source, level, system, material)
    if rule == "DISCIPLINE_SYSTEM":
        return (source, discipline, system)
    if rule == "PROJECT_SYSTEM":
        return (source, system)
    return (source, level, discipline, system, material)


def _build_proxy_object(key, items, settings):
    vertices = []
    faces = []
    material_indices = []
    smooth_flags = []
    materials = []
    material_lookup = {}
    source_collections = set()
    source_docs = set()
    stable_samples = []

    for obj, action in items:
        for collection in obj.users_collection:
            source_collections.add(collection.name)
        source_docs.add(str(obj.get("slbh.source_document_name", "")))
        if len(stable_samples) < 100:
            stable_samples.append(str(obj.get("slbh.stable_element_key", "")))
        if action == "LOW":
            _append_box_geometry(obj, vertices, faces, material_indices, smooth_flags, materials, material_lookup)
        elif action == "POINT":
            _append_point_geometry(obj, vertices, faces, material_indices, smooth_flags, materials, material_lookup)
        else:
            _append_mesh_geometry(obj, vertices, faces, material_indices, smooth_flags, materials, material_lookup)

    if not vertices or not faces:
        return None

    name = _safe_object_name("OVERALL_" + "_".join(_safe_name(part) for part in key[1:]))
    mesh = bpy.data.meshes.new(name + "_Mesh")
    mesh.from_pydata(vertices, [], faces)
    for material in materials:
        mesh.materials.append(material)
    for polygon, mat_index, smooth in zip(mesh.polygons, material_indices, smooth_flags):
        polygon.material_index = mat_index
        polygon.use_smooth = bool(smooth)
    mesh.update()

    proxy = bpy.data.objects.new(name, mesh)
    proxy["slbh.overall_display_proxy"] = True
    proxy["slbh.proxy_type"] = "OVERALL_DISPLAY"
    proxy["slbh.proxy_version"] = ADDON_PROXY_VERSION
    proxy["slbh.proxy_group_rule"] = getattr(settings, "overall_group_rule", "")
    proxy["slbh.proxy_group_key"] = json.dumps([str(item) for item in key], ensure_ascii=False)
    proxy["slbh.member_count"] = len(items)
    proxy["slbh.source_collections"] = json.dumps(sorted(source_collections), ensure_ascii=False)
    proxy["slbh.source_documents"] = json.dumps(sorted(source_docs), ensure_ascii=False)
    proxy["slbh.sample_stable_keys"] = json.dumps(stable_samples, ensure_ascii=False)
    proxy["slbh.level"] = _group_part(key, 1)
    proxy["slbh.discipline"] = _group_part(key, 2)
    proxy["slbh.system_series"] = _group_part(key, 3)
    proxy["slbh.base_material"] = _group_part(key, len(key) - 1)
    return proxy


def _append_mesh_geometry(obj, vertices, faces, material_indices, smooth_flags, materials, material_lookup):
    mesh = obj.data
    if mesh is None:
        return
    offset = len(vertices)
    world = obj.matrix_world.copy()
    vertices.extend(tuple(world @ vertex.co) for vertex in mesh.vertices)
    for polygon in mesh.polygons:
        faces.append(tuple(offset + index for index in polygon.vertices))
        material = _polygon_material(obj, polygon)
        material_indices.append(_material_index(material, materials, material_lookup))
        smooth_flags.append(bool(polygon.use_smooth))


def _append_box_geometry(obj, vertices, faces, material_indices, smooth_flags, materials, material_lookup):
    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    if not corners:
        return
    offset = len(vertices)
    vertices.extend(tuple(corner) for corner in corners)
    box_faces = ((0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (4, 0, 3, 7))
    material = obj.active_material or _default_proxy_material()
    mat_index = _material_index(material, materials, material_lookup)
    for face in box_faces:
        faces.append(tuple(offset + index for index in face))
        material_indices.append(mat_index)
        smooth_flags.append(False)


def _append_point_geometry(obj, vertices, faces, material_indices, smooth_flags, materials, material_lookup):
    center = obj.matrix_world.translation
    size = max(min(max((float(v) for v in obj.dimensions), default=0.2) * 0.20, 0.12), 0.025)
    offset = len(vertices)
    for dx, dy, dz in (
        (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
        (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
    ):
        vertices.append(tuple(center + Vector((dx * size, dy * size, dz * size))))
    material = obj.active_material or _default_proxy_material()
    mat_index = _material_index(material, materials, material_lookup)
    for face in ((0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (4, 0, 3, 7)):
        faces.append(tuple(offset + index for index in face))
        material_indices.append(mat_index)
        smooth_flags.append(False)


def _polygon_material(obj, polygon):
    mesh = obj.data
    if mesh and polygon.material_index < len(mesh.materials):
        material = mesh.materials[polygon.material_index]
        if material is not None:
            return material
    return obj.active_material or _default_proxy_material()


def _material_index(material, materials, lookup):
    key = material.name if material else "__DEFAULT__"
    if key not in lookup:
        lookup[key] = len(materials)
        materials.append(material or _default_proxy_material())
    return lookup[key]


def _default_proxy_material():
    material = bpy.data.materials.get("SLBH_Overall_Proxy_Default")
    if material is None:
        material = bpy.data.materials.new("SLBH_Overall_Proxy_Default")
        material.diffuse_color = (0.62, 0.62, 0.64, 1.0)
    return material


def _bridge_roots(context):
    roots = []
    for collection in context.scene.collection.children:
        if collection.get("slbh.bridge_root"):
            roots.append(collection)
    return roots


def _root_for_object(obj, context):
    preferred = obj.get("slbh.project_root")
    if preferred:
        collection = bpy.data.collections.get(preferred)
        if collection is not None:
            return collection
    for root in _bridge_roots(context):
        if root.all_objects.get(obj.name) is not None:
            return root
    return None


def _update_proxy_status(context):
    source_count = len([
        obj for obj in context.scene.objects
        if obj.type == "MESH" and "slbh.element_id" in obj and not obj.get("slbh.overall_display_proxy", False)
    ])
    for root in _bridge_roots(context):
        overall = ensure_project_layers(root)["overall"]
        previous = int(overall.get("slbh.proxy_source_object_count", -1))
        if previous >= 0 and previous != source_count:
            overall["slbh.proxy_status"] = "OUTDATED"


def _set_collection_hidden(collection, hidden):
    collection.hide_viewport = bool(hidden)
    collection.hide_render = bool(hidden)


def _visible_get(obj):
    try:
        return obj.visible_get()
    except Exception:
        return not obj.hide_viewport


def _level_in_range(level_name, start, end):
    value = _level_rank(level_name)
    if value is None:
        return start is None and end is None
    if start is not None and value < start:
        return False
    if end is not None and value > end:
        return False
    return True


def _level_rank(level_name):
    text = str(level_name or "").upper()
    digits = ""
    sign = 1
    if "B" in text and not any(prefix in text for prefix in ("FB", "F")):
        sign = -1
    for char in text:
        if char.isdigit():
            digits += char
        elif digits:
            break
    if not digits:
        return None
    try:
        return sign * int(digits)
    except ValueError:
        return None


def _object_text(obj):
    return " ".join(str(obj.get(key, "")) for key in (
        "slbh.category", "slbh.source_category", "slbh.family", "slbh.type",
        "slbh.system_name", "slbh.system_series", "slbh.discipline",
    )).lower()


def _contains(text, keywords):
    lowered = str(text or "").lower()
    return any(str(keyword).lower() in lowered for keyword in keywords)


def _active_material_name(obj):
    return obj.active_material.name if obj.active_material else ""


def _safe_name(value):
    text = str(value or "Unnamed")
    for char in '/\\:*?"<>|\n\r\t':
        text = text.replace(char, "_")
    return text[:48]


def _safe_object_name(value):
    name = _safe_name(value)
    return name[:63] if len(name) > 63 else name


def _group_part(key, index):
    try:
        return str(key[index])
    except Exception:
        return ""


def _ratio_reduction(before, after):
    if before <= 0:
        return 0.0
    return max(0.0, (1.0 - (float(after) / float(before))) * 100.0)


def _write_proxy_stats(result):
    lines = [
        "SLBH overall display proxy statistics",
        "=" * 64,
        f"Source objects: {result['source_objects']}",
        f"Visible source objects: {result['visible_source_objects']}",
        f"Unique meshes: {result['unique_meshes']}",
        f"Source polygons counted per object: {result['source_faces']}",
        f"Overall proxy objects: {result['proxy_objects']}",
        f"Overall proxy polygons: {result['proxy_faces']}",
        f"Geometry Nodes instances: {result['geometry_nodes_instances']}",
        f"Filtered objects: {result['filtered_objects']}",
        f"Low-proxy replacements: {result['low_proxy_objects']}",
        f"Point-proxy replacements: {result['point_proxy_objects']}",
        f"Object count reduction: {result['object_reduction_percent']:.1f}%",
        f"Polygon reduction: {result['face_reduction_percent']:.1f}%",
        "",
        "Note: polygons are counted from Blender mesh polygons. Geometry Nodes instances are not used in this first phase.",
    ]
    _write_text(OVERALL_STATS_TEXT, "\n".join(lines))


def _write_text(name, text):
    block = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    block.clear()
    block.write(text)


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
