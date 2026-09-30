import os

import bpy


BASE_RULES = [
    (("混凝土", "砼", "concrete", "c30", "c35", "c40"), "现浇混凝土"),
    (("玻璃", "glass", "low-e", "lowe"), "清玻璃"),
    (("不锈钢", "stainless"), "不锈钢"),
    (("镀锌", "galvanized"), "镀锌钢板"),
    (("风管", "duct"), "镀锌钢板"),
    (("钢", "steel", "金属", "metal", "铝", "aluminum"), "金属"),
    (("木", "wood"), "木材"),
    (("石材", "花岗岩", "大理石", "stone", "granite", "marble"), "石材"),
    (("沥青", "asphalt"), "沥青"),
    (("土", "soil", "earth"), "土壤"),
    (("乳胶漆", "涂料", "paint", "墙面"), "白色墙面"),
    (("桥架", "cable tray"), "电缆桥架"),
]

DEFAULT_COLORS = {
    "现浇混凝土": (0.45, 0.45, 0.45, 1.0),
    "清玻璃": (0.55, 0.75, 0.85, 0.22),
    "不锈钢": (0.55, 0.57, 0.60, 1.0),
    "镀锌钢板": (0.45, 0.48, 0.52, 1.0),
    "金属": (0.35, 0.38, 0.42, 1.0),
    "木材": (0.42, 0.22, 0.10, 1.0),
    "石材": (0.52, 0.50, 0.47, 1.0),
    "沥青": (0.05, 0.05, 0.05, 1.0),
    "土壤": (0.22, 0.10, 0.04, 1.0),
    "白色墙面": (0.82, 0.82, 0.80, 1.0),
    "电缆桥架": (0.52, 0.55, 0.58, 1.0),
    "默认BIM材质": (0.60, 0.60, 0.62, 1.0),
}

SYSTEM_COLORS = {
    "SMOKE_EXHAUST": (0.82, 0.04, 0.02),
    "MAKEUP_AIR": (0.95, 0.30, 0.08),
    "FRESH_AIR": (0.08, 0.62, 0.32),
    "SUPPLY_AIR": (0.05, 0.62, 0.78),
    "RETURN_AIR": (0.26, 0.52, 0.78),
    "EXHAUST_AIR": (0.45, 0.28, 0.58),
    "FIRE_PROTECTION": (0.75, 0.02, 0.02),
    "WATER_SUPPLY": (0.02, 0.40, 0.16),
    "DRAINAGE": (0.18, 0.18, 0.20),
    "CHW_SUPPLY": (0.03, 0.30, 0.80),
    "CHW_RETURN": (0.25, 0.58, 0.90),
    "CW_SUPPLY": (0.05, 0.52, 0.62),
    "CW_RETURN": (0.30, 0.72, 0.72),
    "HYDRONIC_SUPPLY": (0.75, 0.18, 0.08),
    "HYDRONIC_RETURN": (0.92, 0.50, 0.12),
}


class MaterialResolver:
    def __init__(self, context, source_materials, material_mode="REALISTIC"):
        self.context = context
        self.source_materials = source_materials
        self.material_mode = material_mode
        self.library_path = self._get_library_path()
        self.cache = {}

    def resolve(self, obj_material_name, metadata):
        source = self.source_materials.get(obj_material_name, {})
        source_name = source.get("source_name", "")
        base_name = self._guess_base_target(source_name, metadata)
        base_color = self._base_color(base_name, source)
        tint_color, tint_key, tint_source = self._resolve_tint(metadata)
        cache_key = (base_name, tint_key)
        if cache_key in self.cache:
            return self.cache[cache_key]

        base_material = self._get_or_create_base_material(base_name, base_color)
        if tint_color is None:
            material = base_material
            self._sync_material_color(material, tuple(material.diffuse_color))
        else:
            variant_name = f"{base_name}_{tint_key}"
            material = bpy.data.materials.get(variant_name)
            if material is None:
                material = bpy.data.materials.new(name=variant_name)
            self._configure_tint_material(material, base_material, (*tint_color, base_color[3]))
            material["slbh.tint_source"] = tint_source
            material["slbh.tint_key"] = tint_key

        material["slbh.base_material_name"] = base_name
        material["slbh.source_material_name"] = source_name
        material["slbh.source_material_id"] = int(source.get("material_id", -1))
        self.cache[cache_key] = material
        return material

    def resolve_for_element(self, metadata):
        keys = metadata.get("material_keys") or []
        key = keys[0] if keys else "SLBH_MAT_DEFAULT"
        return self.resolve(key, metadata)

    def base_name_for(self, obj_material_name, metadata):
        source = self.source_materials.get(obj_material_name, {})
        return self._guess_base_target(source.get("source_name", ""), metadata)

    def _guess_base_target(self, source_name, metadata):
        haystack = " ".join([
            source_name,
            metadata.get("category", ""),
            metadata.get("family", ""),
            metadata.get("type", ""),
            metadata.get("discipline", ""),
        ]).lower()
        for keywords, target in BASE_RULES:
            if any(keyword.lower() in haystack for keyword in keywords):
                return target
        return "默认BIM材质"

    def _resolve_tint(self, metadata):
        if self.material_mode == "REVIT_COLOR":
            color = _valid_rgb(metadata.get("display_color"))
            if color is not None:
                hex_value = metadata.get("display_color_hex") or _rgb_to_hex(color)
                return color, f"着色_{hex_value.lstrip('#').upper()}", metadata.get("display_color_source", "revit")
        elif self.material_mode == "SYSTEM_COLOR":
            code = metadata.get("system_code", "")
            color = SYSTEM_COLORS.get(code)
            if color is not None:
                return color, f"系统_{code}", "slbh_system"
        return None, "基础", "none"

    def _base_color(self, base_name, source):
        color = DEFAULT_COLORS.get(base_name)
        if color is not None:
            return color
        rgb = _valid_rgb(source.get("color")) or (0.60, 0.60, 0.62)
        alpha = 1.0 - float(source.get("transparency", 0.0))
        return (*rgb, max(0.0, min(1.0, alpha)))

    def _get_or_create_base_material(self, base_name, color):
        material = bpy.data.materials.get(base_name)
        if material is None and self.library_path:
            material = self._append_from_library(base_name)
        if material is None:
            material = self._create_material(base_name, color)
        material["slbh.base_material_name"] = base_name
        return material

    def _get_library_path(self):
        addon_key = __package__.split(".")[0]
        addon = self.context.preferences.addons.get(addon_key)
        if addon and addon.preferences:
            path = bpy.path.abspath(addon.preferences.material_library)
            if path and os.path.isfile(path):
                return path
        return ""

    def _append_from_library(self, material_name):
        try:
            with bpy.data.libraries.load(self.library_path, link=False) as (source, target):
                if material_name not in source.materials:
                    return None
                target.materials = [material_name]
            return bpy.data.materials.get(material_name)
        except Exception:
            return None

    def _create_material(self, name, color):
        material = bpy.data.materials.new(name=name)
        material.use_nodes = True
        self._build_simple_principled(material, color, metallic=0.0, roughness=0.55)
        return material

    @classmethod
    def _configure_tint_material(cls, material, base_material, color):
        metallic, roughness, transmission = cls._surface_defaults(base_material)
        cls._build_simple_principled(
            material,
            color,
            metallic=metallic,
            roughness=roughness,
            transmission=transmission,
        )
        material["slbh.color_synced"] = True

    @staticmethod
    def _surface_defaults(material):
        name = material.get("slbh.base_material_name", material.name).lower() if material else ""
        metallic = 0.0
        roughness = 0.55
        transmission = 0.0
        if any(word in name for word in ("金属", "钢", "铝", "镀锌", "metal", "steel")):
            metallic, roughness = 0.65, 0.36
        if any(word in name for word in ("玻璃", "glass")):
            metallic, roughness, transmission = 0.0, 0.18, 0.65
        if material and material.use_nodes and material.node_tree:
            for node in material.node_tree.nodes:
                if node.type != "BSDF_PRINCIPLED":
                    continue
                metallic_input = node.inputs.get("Metallic")
                roughness_input = node.inputs.get("Roughness")
                transmission_input = node.inputs.get("Transmission Weight") or node.inputs.get("Transmission")
                if metallic_input and not metallic_input.is_linked:
                    metallic = float(metallic_input.default_value)
                if roughness_input and not roughness_input.is_linked:
                    roughness = float(roughness_input.default_value)
                if transmission_input and not transmission_input.is_linked:
                    transmission = float(transmission_input.default_value)
                break
        return metallic, roughness, transmission

    @classmethod
    def _build_simple_principled(cls, material, color, metallic=0.0, roughness=0.55, transmission=0.0):
        rgba = tuple(float(v) for v in color)
        if len(rgba) < 4:
            rgba = (*rgba[:3], 1.0)
        material.use_nodes = True
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        nodes.clear()
        output = nodes.new("ShaderNodeOutputMaterial")
        output.location = (280, 0)
        principled = nodes.new("ShaderNodeBsdfPrincipled")
        principled.location = (0, 0)
        links.new(principled.outputs.get("BSDF"), output.inputs.get("Surface"))

        base_input = principled.inputs.get("Base Color")
        if base_input:
            base_input.default_value = rgba
        metallic_input = principled.inputs.get("Metallic")
        if metallic_input:
            metallic_input.default_value = max(0.0, min(1.0, metallic))
        roughness_input = principled.inputs.get("Roughness")
        if roughness_input:
            roughness_input.default_value = max(0.0, min(1.0, roughness))
        alpha_input = principled.inputs.get("Alpha")
        if alpha_input:
            alpha_input.default_value = rgba[3]
        transmission_input = principled.inputs.get("Transmission Weight") or principled.inputs.get("Transmission")
        if transmission_input:
            transmission_input.default_value = max(0.0, min(1.0, transmission))

        cls._sync_material_color(material, rgba)
        if rgba[3] < 0.999:
            try:
                material.surface_render_method = "DITHERED"
            except Exception:
                pass

    @staticmethod
    def _sync_material_color(material, color):
        rgba = tuple(float(v) for v in color)
        if len(rgba) < 4:
            rgba = (*rgba[:3], 1.0)
        material.diffuse_color = rgba
        if not material.use_nodes or not material.node_tree:
            return
        found = False
        for node in material.node_tree.nodes:
            if node.type != "BSDF_PRINCIPLED":
                continue
            base_input = node.inputs.get("Base Color")
            if base_input and not base_input.is_linked:
                base_input.default_value = rgba
                found = True
            alpha_input = node.inputs.get("Alpha")
            if alpha_input and not alpha_input.is_linked:
                alpha_input.default_value = rgba[3]
        if not found:
            MaterialResolver._build_simple_principled(material, rgba)

    _apply_color = _sync_material_color


def _valid_rgb(value):
    try:
        if value is None or len(value) < 3:
            return None
        rgb = tuple(max(0.0, min(1.0, float(value[i]))) for i in range(3))
    except (TypeError, ValueError, IndexError):
        return None
    return rgb


def _rgb_to_hex(rgb):
    return "#{:02X}{:02X}{:02X}".format(*(round(v * 255) for v in rgb))


def reapply_material_mode(obj, mode):
    if obj is None or obj.type != "MESH":
        return False

    if mode == "REVIT_COLOR":
        tint = _valid_rgb(obj.get("slbh.display_color"))
        hex_value = obj.get("slbh.display_color_hex", "") or (_rgb_to_hex(tint) if tint else "")
        suffix = f"着色_{hex_value.lstrip('#').upper()}" if tint else ""
        source = obj.get("slbh.display_color_source", "revit")
    elif mode == "SYSTEM_COLOR":
        code = obj.get("slbh.system_code", "")
        tint = SYSTEM_COLORS.get(code)
        suffix = f"系统_{code}" if tint else ""
        source = "slbh_system"
    else:
        tint = None
        suffix = ""
        source = "none"

    changed = False
    for slot in obj.material_slots:
        current = slot.material
        if current is None:
            continue
        base_name = current.get("slbh.base_material_name", current.name)
        base_material = bpy.data.materials.get(base_name) or current
        if tint is None:
            target = base_material
        else:
            target_name = f"{base_name}_{suffix}"
            target = bpy.data.materials.get(target_name)
            if target is None:
                target = bpy.data.materials.new(name=target_name)
            alpha = float(base_material.diffuse_color[3]) if len(base_material.diffuse_color) > 3 else 1.0
            current_rgb = tuple(float(v) for v in target.diffuse_color[:3])
            needs_sync = (
                not bool(target.get("slbh.color_synced", False))
                or any(abs(current_rgb[i] - float(tint[i])) > 1e-5 for i in range(3))
            )
            if needs_sync:
                MaterialResolver._configure_tint_material(target, base_material, (*tint, alpha))
            target["slbh.base_material_name"] = base_name
            target["slbh.tint_source"] = source
            target["slbh.tint_key"] = suffix
        if slot.material != target:
            slot.material = target
            changed = True

    obj["slbh.material_mode"] = mode
    _sync_object_display_color(obj, tint)
    return changed


def _sync_object_display_color(obj, tint=None):
    if tint is None:
        material = obj.active_material
        if material is None and obj.material_slots:
            material = obj.material_slots[0].material
        if material is not None:
            rgba = tuple(material.diffuse_color)
            obj.color = rgba if len(rgba) >= 4 else (*rgba[:3], 1.0)
        return
    obj.color = (*tint[:3], 1.0)
