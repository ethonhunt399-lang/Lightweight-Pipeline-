"""Performance optimizers for imported Revit geometry."""

from __future__ import annotations

import hashlib
import json
import struct
from collections import Counter, defaultdict

import bpy
from mathutils import Matrix, Vector

from .semantics import family_type_key


MERGE_PROXY_COLLECTION = "SLBH_Merged_By_Level_Type"


def _bbox_center(vertices):
    if not vertices:
        return Vector((0.0, 0.0, 0.0))
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    return Vector(((min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5, (min(zs) + max(zs)) * 0.5))


def normalized_payload(mesh_data):
    center = _bbox_center(mesh_data.get("vertices", []))
    vertices = [tuple(Vector(v) - center) for v in mesh_data.get("vertices", [])]
    return center, vertices


def geometry_signature(vertices, faces, material_names, metadata=None, precision=5):
    """Translation-invariant strict signature. Rotation differences intentionally stay distinct."""
    digest = hashlib.blake2b(digest_size=20)
    family_key = family_type_key(metadata or {})
    source_key = str((metadata or {}).get("source_model_key") or "HOST")
    digest.update(source_key.encode("utf-8", errors="replace"))
    digest.update(b"\0")
    digest.update("\x1f".join(family_key).encode("utf-8", errors="replace"))
    digest.update(struct.pack("<II", len(vertices), len(faces)))
    scale = 10 ** precision
    for vertex in vertices:
        digest.update(struct.pack("<iii", *(int(round(float(value) * scale)) for value in vertex[:3])))
    for face, mat_name in faces:
        digest.update(struct.pack("<H", len(face)))
        for index in face:
            digest.update(struct.pack("<I", int(index)))
        digest.update(str(mat_name).encode("utf-8", errors="replace"))
        digest.update(b"\0")
    for name in material_names:
        digest.update(name.encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return digest.hexdigest()


def should_use_viewport_proxy(obj, metadata, profile, group_count, max_size, min_faces, repeat_threshold):
    if profile == "SAFE":
        return False
    if obj.type != "MESH" or len(obj.data.polygons) < min_faces:
        return False
    if group_count < repeat_threshold:
        return False
    size = max((float(v) for v in obj.dimensions), default=0.0)
    if size <= 0.0 or size > max_size:
        return False
    # Standard mode is conservative; Light mode also catches slightly larger repeated accessories.
    if profile == "STANDARD":
        category = str(metadata.get("category") or "").lower()
        text = " ".join(str(metadata.get(key) or "") for key in ("category", "family", "type")).lower()
        keywords = ("喷头", "阀", "附件", "支吊架", "灯具", "风口", "sprinkler", "valve", "fitting")
        return any(keyword in f"{category} {text}" for keyword in keywords)
    return True


def set_viewport_mode(obj, mode):
    if mode == "FULL":
        obj.display_type = "TEXTURED"
        obj.hide_viewport = False
        obj["slbh.viewport_proxy"] = False
    elif mode == "WIRE":
        obj.display_type = "WIRE"
        obj.hide_viewport = False
        obj["slbh.viewport_proxy"] = True
    elif mode == "HIDE":
        obj.hide_viewport = True
        obj["slbh.viewport_proxy"] = True
    else:
        obj.display_type = "BOUNDS"
        obj.hide_viewport = False
        obj["slbh.viewport_proxy"] = True


def _object_metadata(obj):
    return {
        "category": obj.get("slbh.category", ""),
        "family": obj.get("slbh.family", ""),
        "type": obj.get("slbh.type", ""),
        "source_model_key": obj.get("slbh.source_model_key", "HOST"),
    }


def _normalized_object_signature(obj, precision=5):
    mesh = obj.data
    if not mesh.vertices or not mesh.polygons:
        return None, None
    center = Vector((0.0, 0.0, 0.0))
    mins = [float("inf")] * 3
    maxs = [float("-inf")] * 3
    for vertex in mesh.vertices:
        co = vertex.co
        for axis in range(3):
            mins[axis] = min(mins[axis], co[axis])
            maxs[axis] = max(maxs[axis], co[axis])
    center = Vector(tuple((mins[i] + maxs[i]) * 0.5 for i in range(3)))
    local_vertices = [tuple(vertex.co - center) for vertex in mesh.vertices]
    faces = [(tuple(poly.vertices), str(poly.material_index)) for poly in mesh.polygons]
    mats = [slot.material.name if slot.material else "" for slot in obj.material_slots]
    signature = geometry_signature(local_vertices, faces, mats, _object_metadata(obj), precision=precision)
    return signature, center


def instance_duplicate_meshes(objects):
    """Make exact translation-identical family meshes share one Mesh datablock."""
    cache = {}
    reused = 0
    unique = 0
    removed = 0

    for obj in objects:
        if obj.type != "MESH" or "slbh.element_id" not in obj:
            continue
        signature, center = _normalized_object_signature(obj)
        if not signature:
            continue

        old_mesh = obj.data
        old_world = obj.matrix_world.copy()
        target_world = old_world @ Matrix.Translation(center)

        prototype = cache.get(signature)
        was_reused = prototype is not None
        if prototype is None:
            # Create a normalized prototype instead of mutating a possibly shared mesh.
            vertices = [tuple(vertex.co - center) for vertex in old_mesh.vertices]
            faces = [tuple(poly.vertices) for poly in old_mesh.polygons]
            new_mesh = bpy.data.meshes.new(f"{old_mesh.name}_共享")
            new_mesh.from_pydata(vertices, [], faces)
            for material in old_mesh.materials:
                new_mesh.materials.append(material)
            for new_poly, old_poly in zip(new_mesh.polygons, old_mesh.polygons):
                new_poly.material_index = old_poly.material_index
                new_poly.use_smooth = old_poly.use_smooth
            new_mesh.update()
            cache[signature] = new_mesh
            prototype = new_mesh
            unique += 1
        else:
            reused += 1

        obj.data = prototype
        obj.matrix_world = target_world
        obj["slbh.prototype_id"] = signature[:16]
        obj["slbh.is_instance"] = bool(was_reused)

        if old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)
            removed += 1

    return {"unique": unique, "reused": reused, "removed_meshes": removed}


def apply_viewport_optimization(objects, mode, max_size, min_faces, repeat_threshold):
    groups = Counter(family_type_key(_object_metadata(obj)) for obj in objects if obj.type == "MESH")
    changed = 0
    for obj in objects:
        if obj.type != "MESH" or "slbh.element_id" not in obj:
            continue
        key = family_type_key(_object_metadata(obj))
        if max(obj.dimensions, default=0.0) <= max_size and len(obj.data.polygons) >= min_faces and groups[key] >= repeat_threshold:
            set_viewport_mode(obj, mode)
            changed += 1
    return changed


def restore_viewport(objects):
    changed = 0
    for obj in objects:
        if "slbh.element_id" not in obj:
            continue
        if obj.hide_viewport or obj.display_type != "TEXTURED" or obj.get("slbh.viewport_proxy"):
            set_viewport_mode(obj, "FULL")
            changed += 1
    return changed


def heavy_family_report(objects, limit=20):
    groups = defaultdict(lambda: {"count": 0, "unique_meshes": set(), "faces_each": [], "total_faces": 0})
    for obj in objects:
        if obj.type != "MESH" or "slbh.element_id" not in obj:
            continue
        key = family_type_key(_object_metadata(obj))
        item = groups[key]
        item["count"] += 1
        item["unique_meshes"].add(obj.data.name)
        face_count = len(obj.data.polygons)
        item["faces_each"].append(face_count)
        item["total_faces"] += face_count

    ranked = sorted(groups.items(), key=lambda pair: pair[1]["total_faces"], reverse=True)[:limit]
    lines = ["SLBH Revit桥接：高负担族类型统计", "=" * 56]
    for index, (key, item) in enumerate(ranked, 1):
        category, family, type_name = key
        avg = item["total_faces"] / max(item["count"], 1)
        lines.append(
            f"{index:02d}. {category} | {family or '-'} | {type_name or '-'}\n"
            f"    数量 {item['count']}，唯一网格 {len(item['unique_meshes'])}，平均面数 {avg:.0f}，场景总面数 {item['total_faces']}"
        )
    return "\n".join(lines), ranked


def replace_with_bbox_proxies(objects, max_size, min_faces, repeat_threshold):
    """Permanently replace small repeated high-poly objects with shared 6-face box proxies."""
    candidates = [obj for obj in objects if obj.type == "MESH" and "slbh.element_id" in obj]
    groups = Counter(family_type_key(_object_metadata(obj)) for obj in candidates)
    cache = {}
    changed = 0
    before = 0
    after = 0
    removed = 0

    for obj in candidates:
        mesh = obj.data
        face_count = len(mesh.polygons)
        key_group = family_type_key(_object_metadata(obj))
        if face_count < min_faces or groups[key_group] < repeat_threshold:
            continue
        if max(obj.dimensions, default=0.0) > max_size:
            continue
        if not mesh.vertices:
            continue

        mins = [float("inf")] * 3
        maxs = [float("-inf")] * 3
        for vertex in mesh.vertices:
            for axis in range(3):
                value = float(vertex.co[axis])
                mins[axis] = min(mins[axis], value)
                maxs[axis] = max(maxs[axis], value)
        center = Vector(tuple((mins[i] + maxs[i]) * 0.5 for i in range(3)))
        half = tuple(max((maxs[i] - mins[i]) * 0.5, 1e-5) for i in range(3))
        material_name = obj.active_material.name if obj.active_material else ""
        proxy_key = (key_group, tuple(round(v, 5) for v in half), material_name)

        proxy_mesh = cache.get(proxy_key)
        if proxy_mesh is None:
            x, y, z = half
            vertices = [
                (-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z),
                (-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z),
            ]
            faces = [
                (0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
                (1, 5, 6, 2), (2, 6, 7, 3), (4, 0, 3, 7),
            ]
            proxy_mesh = bpy.data.meshes.new(f"{obj.name}_低模代理")
            proxy_mesh.from_pydata(vertices, [], faces)
            if obj.active_material:
                proxy_mesh.materials.append(obj.active_material)
            proxy_mesh.update()
            cache[proxy_key] = proxy_mesh

        old_mesh = obj.data
        old_world = obj.matrix_world.copy()
        obj.data = proxy_mesh
        obj.matrix_world = old_world @ Matrix.Translation(center)
        obj["slbh.low_poly_proxy"] = True
        obj["slbh.original_face_count"] = face_count
        obj["slbh.prototype_id"] = f"PROXY_{abs(hash(proxy_key))}"
        before += face_count
        after += len(proxy_mesh.polygons)
        changed += 1
        if old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)
            removed += 1

    return {"changed": changed, "before": before, "after": after, "removed_meshes": removed}


def create_level_type_merge_proxies(context, objects, min_count=2, hide_originals=True):
    """Build render/viewport proxy objects by merging BIM objects with the same source, level and type."""
    candidates = [
        obj for obj in objects
        if obj.type == "MESH"
        and "slbh.element_id" in obj
        and not obj.get("slbh.merge_proxy", False)
        and obj.data is not None
        and len(obj.data.polygons) > 0
    ]
    groups = defaultdict(list)
    for obj in candidates:
        groups[_merge_group_key(obj)].append(obj)

    proxy_collection = _get_or_create_collection(context.scene.collection, MERGE_PROXY_COLLECTION)
    created = 0
    merged_objects = 0
    merged_faces = 0
    skipped_groups = 0

    for key, group in groups.items():
        if len(group) < int(min_count):
            skipped_groups += 1
            continue
        proxy = _create_merge_proxy_object(key, group)
        if proxy is None:
            skipped_groups += 1
            continue
        proxy_collection.objects.link(proxy)
        created += 1
        merged_objects += len(group)
        merged_faces += len(proxy.data.polygons)
        if hide_originals:
            _set_originals_hidden(group, True)

    return {
        "created": created,
        "merged_objects": merged_objects,
        "merged_faces": merged_faces,
        "skipped_groups": skipped_groups,
    }


def rebuild_level_type_merge_proxies(context, objects, min_count=2, hide_originals=True):
    deleted = delete_merge_proxies(context, restore_originals=False)
    created = create_level_type_merge_proxies(context, objects, min_count=min_count, hide_originals=hide_originals)
    created["deleted"] = deleted["deleted"]
    return created


def delete_merge_proxies(context, restore_originals=True):
    deleted = 0
    removed_meshes = 0
    for obj in list(context.scene.objects):
        if not obj.get("slbh.merge_proxy", False):
            continue
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        deleted += 1
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
            removed_meshes += 1

    if restore_originals:
        set_merge_display_mode(context, "ORIGINAL")
    return {"deleted": deleted, "removed_meshes": removed_meshes}


def set_merge_display_mode(context, mode):
    show_originals = mode in {"ORIGINAL", "BOTH"}
    show_proxies = mode in {"PROXY", "BOTH"}
    originals = 0
    proxies = 0
    for obj in context.scene.objects:
        if obj.get("slbh.merge_proxy", False):
            _set_object_hidden(obj, not show_proxies)
            proxies += 1
        elif obj.get("slbh.hidden_by_merge_proxy", False):
            _set_object_hidden(obj, not show_originals)
            if show_originals:
                obj["slbh.hidden_by_merge_proxy"] = False
            originals += 1
        elif mode == "PROXY" and "slbh.element_id" in obj:
            obj["slbh.hidden_by_merge_proxy"] = True
            _set_object_hidden(obj, True)
            originals += 1
    return {"originals": originals, "proxies": proxies}


def _merge_group_key(obj):
    return (
        str(obj.get("slbh.source_model_key", "HOST")),
        str(obj.get("slbh.level", "")),
        str(obj.get("slbh.category", "")),
        str(obj.get("slbh.family", "")),
        str(obj.get("slbh.type", "")),
        str(obj.get("slbh.system_series", "")),
        str(obj.get("slbh.base_material", "")),
    )


def _create_merge_proxy_object(key, group):
    source_key, level, category, family, type_name, system_series, base_material = key
    vertices = []
    faces = []
    face_material_indices = []
    face_smooth = []
    materials = []
    material_indices = {}

    for obj in group:
        mesh = obj.data
        vertex_offset = len(vertices)
        world = obj.matrix_world.copy()
        for vertex in mesh.vertices:
            vertices.append(tuple(world @ vertex.co))
        for polygon in mesh.polygons:
            faces.append(tuple(vertex_offset + index for index in polygon.vertices))
            material = mesh.materials[polygon.material_index] if polygon.material_index < len(mesh.materials) else None
            if material is None:
                material = _default_merge_material()
            material_key = material.name if material else ""
            if material_key not in material_indices:
                material_indices[material_key] = len(materials)
                materials.append(material)
            face_material_indices.append(material_indices[material_key])
            face_smooth.append(bool(polygon.use_smooth))

    if not vertices or not faces:
        return None

    name = _safe_object_name("MERGED_{}_{}_{}_{}".format(level, category, family, type_name))
    merged_mesh = bpy.data.meshes.new(name + "_Mesh")
    merged_mesh.from_pydata(vertices, [], faces)
    for material in materials:
        merged_mesh.materials.append(material)
    for polygon, material_index, smooth in zip(merged_mesh.polygons, face_material_indices, face_smooth):
        polygon.material_index = material_index
        polygon.use_smooth = smooth
    merged_mesh.update()

    proxy = bpy.data.objects.new(name, merged_mesh)
    proxy["slbh.merge_proxy"] = True
    proxy["slbh.merge_group"] = "LEVEL_TYPE"
    proxy["slbh.source_model_key"] = source_key
    proxy["slbh.level"] = level
    proxy["slbh.category"] = category
    proxy["slbh.family"] = family
    proxy["slbh.type"] = type_name
    proxy["slbh.system_series"] = system_series
    proxy["slbh.base_material"] = base_material
    proxy["slbh.member_count"] = len(group)
    proxy["slbh.member_element_ids"] = json.dumps([int(obj.get("slbh.element_id", -1)) for obj in group], ensure_ascii=False)
    proxy["slbh.member_unique_ids"] = json.dumps([str(obj.get("slbh.unique_id", "")) for obj in group], ensure_ascii=False)
    proxy["slbh.member_stable_keys"] = json.dumps([str(obj.get("slbh.stable_element_key", "")) for obj in group], ensure_ascii=False)
    proxy["slbh.source_documents"] = json.dumps(sorted({str(obj.get("slbh.source_document_name", "")) for obj in group}), ensure_ascii=False)
    proxy["slbh.original_object_names"] = json.dumps([obj.name for obj in group], ensure_ascii=False)
    return proxy


def _set_originals_hidden(objects, hidden):
    for obj in objects:
        obj["slbh.hidden_by_merge_proxy"] = bool(hidden)
        _set_object_hidden(obj, hidden)


def _set_object_hidden(obj, hidden):
    try:
        obj.hide_set(bool(hidden))
    except RuntimeError:
        pass
    obj.hide_viewport = bool(hidden)
    obj.hide_render = bool(hidden)


def _get_or_create_collection(parent, name):
    for child in parent.children:
        if child.get("slbh.logical_name") == name or child.name == name:
            return child
    collection = bpy.data.collections.new(name)
    collection["slbh.logical_name"] = name
    parent.children.link(collection)
    return collection


def _safe_object_name(value):
    text = str(value or "MERGED")
    for char in '/\\:*?"<>|\n\r\t':
        text = text.replace(char, "_")
    return text[:63]


def _default_merge_material():
    material = bpy.data.materials.get("SLBH_Merged_Default")
    if material is None:
        material = bpy.data.materials.new("SLBH_Merged_Default")
        material.diffuse_color = (0.60, 0.60, 0.62, 1.0)
    return material
