import bpy


def select_matching(context, key: str) -> int:
    active = context.active_object
    if active is None or key not in active:
        return 0

    value = active.get(key)
    bpy.ops.object.select_all(action="DESELECT")

    count = 0
    for obj in context.scene.objects:
        if obj.get(key) == value:
            try:
                obj.select_set(True)
                count += 1
            except RuntimeError:
                # Object may not be selectable in the current view layer.
                continue

    if context.view_layer.objects.get(active.name) is not None:
        context.view_layer.objects.active = active
    return count


def select_same_active_material(context, scope="SCENE") -> int:
    active = context.active_object
    if active is None or active.type != "MESH" or not active.active_material:
        return 0

    target = active.active_material
    bpy.ops.object.select_all(action="DESELECT")

    if scope == "COLLECTION" and active.users_collection:
        candidates = []
        seen = set()
        for collection in active.users_collection:
            for obj in collection.all_objects:
                if obj.name not in seen:
                    candidates.append(obj)
                    seen.add(obj.name)
    else:
        candidates = list(context.scene.objects)

    count = 0
    for obj in candidates:
        if obj.type != "MESH":
            continue
        if any(slot.material == target for slot in obj.material_slots):
            try:
                obj.select_set(True)
                count += 1
            except RuntimeError:
                continue

    if context.view_layer.objects.get(active.name) is not None:
        context.view_layer.objects.active = active
    return count


def isolate_same_source_model(context) -> int:
    active = context.active_object
    if active is None or "slbh.source_model_key" not in active:
        return 0

    source_key = active.get("slbh.source_model_key")
    shown = 0
    for obj in context.scene.objects:
        if "slbh.element_id" not in obj:
            continue
        same_source = obj.get("slbh.source_model_key") == source_key
        obj.hide_set(not same_source)
        obj.hide_viewport = not same_source
        if same_source:
            shown += 1
    return shown
