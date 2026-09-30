"""Collection reorganization helpers for SLBH Revit Bridge."""

import bpy

from .display_proxy import ensure_project_layers, mark_source_object
from .semantics import enrich_metadata, system_series_from_metadata


def _safe_name(value):
    text = str(value or "未命名")
    for char in '/\\:*?"<>|\n\r\t':
        text = text.replace(char, "_")
    return text[:63]


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


def _find_bridge_root(scene_root, obj):
    preferred = obj.get("slbh.project_root")
    if preferred:
        collection = bpy.data.collections.get(preferred)
        if collection and collection.all_objects.get(obj.name) is not None:
            return collection
    for child in scene_root.children:
        if child.get("slbh.bridge_root") and child.all_objects.get(obj.name) is not None:
            return child
        if child.all_objects.get(obj.name) is not None:
            return child
    return None


def _collection_is_within(root, candidate):
    if candidate == root:
        return True
    stack = list(root.children)
    while stack:
        current = stack.pop()
        if current == candidate:
            return True
        stack.extend(current.children)
    return False


def _merge_duplicate_logical_children(parent):
    by_key = {}
    for child in list(parent.children):
        _merge_duplicate_logical_children(child)
        logical = child.get("slbh.logical_name") or child.name.split(".")[0]
        existing = by_key.get(logical)
        if existing is None:
            child["slbh.logical_name"] = logical
            by_key[logical] = child
            continue
        for obj in list(child.objects):
            if existing.objects.get(obj.name) is None:
                existing.objects.link(obj)
            child.objects.unlink(obj)
        for sub in list(child.children):
            child.children.unlink(sub)
            if existing.children.get(sub.name) is None:
                existing.children.link(sub)
        parent.children.unlink(child)
        if child.users == 0:
            bpy.data.collections.remove(child)


def _remove_empty_children(parent):
    for child in list(parent.children):
        _remove_empty_children(child)
        if len(child.objects) == 0 and len(child.children) == 0:
            parent.children.unlink(child)
            if child.users == 0:
                bpy.data.collections.remove(child)


def reorganize_bridge_objects(context, organize_mode, selected_only=False):
    objects = (
        list(context.selected_objects)
        if selected_only
        else [obj for obj in context.scene.objects if "slbh.element_id" in obj]
    )
    moved = 0
    roots = set()

    for obj in objects:
        if "slbh.element_id" not in obj:
            continue

        root = _find_bridge_root(context.scene.collection, obj)
        if root is None:
            root_name = _safe_name(obj.get("slbh.project_root") or "Revit项目")
            root = _get_or_create_collection(context.scene.collection, root_name)
            root["slbh.bridge_root"] = True
        roots.add(root)

        metadata = enrich_metadata({
            "level": obj.get("slbh.level", "未分类楼层"),
            "discipline": obj.get("slbh.discipline", "其他"),
            "category": obj.get("slbh.category", "未分类"),
            "source_category": obj.get("slbh.source_category", obj.get("slbh.category", "未分类")),
            "family": obj.get("slbh.family", ""),
            "type": obj.get("slbh.type", ""),
            "display_name": obj.name,
            "is_linked_element": obj.get("slbh.is_linked_element", False),
            "source_model_key": obj.get("slbh.source_model_key", "HOST"),
            "source_document_name": obj.get("slbh.source_document_name", ""),
            "link_instance_name": obj.get("slbh.link_instance_name", ""),
            "link_type_name": obj.get("slbh.link_type_name", ""),
            "system_name": obj.get("slbh.system_name", ""),
            "system_type_name": obj.get("slbh.system_type", ""),
            "system_code": obj.get("slbh.system_code", "UNASSIGNED"),
            "system_series": obj.get("slbh.system_series", ""),
            "system_abbreviation": obj.get("slbh.system_abbreviation", ""),
        })

        for key, prop in (
            ("category", "slbh.category"),
            ("source_category", "slbh.source_category"),
            ("discipline", "slbh.discipline"),
            ("system_name", "slbh.system_name"),
            ("system_type_name", "slbh.system_type"),
            ("system_code", "slbh.system_code"),
            ("system_series", "slbh.system_series"),
            ("semantic_override", "slbh.semantic_override"),
        ):
            obj[prop] = metadata.get(key, "")
        obj["slbh.project_root"] = root.name

        layers = ensure_project_layers(root)
        source_root = layers["source"]
        mark_source_object(obj)
        target = _target_collection(source_root, metadata, organize_mode)
        if target.objects.get(obj.name) is None:
            target.objects.link(obj)
        for collection in list(obj.users_collection):
            if collection == target:
                continue
            if _collection_is_within(source_root, collection):
                collection.objects.unlink(obj)
        moved += 1

    for root in roots:
        layers = ensure_project_layers(root)
        _merge_duplicate_logical_children(layers["source"])
        _remove_empty_children(layers["source"])
    return moved
