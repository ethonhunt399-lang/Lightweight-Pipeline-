"""Semantic classification helpers for SLBH Revit Bridge.

The source Revit category is always preserved.  These helpers add a practical
classification for Blender management when families were authored in an
inaccurate Revit category (for example an air conditioner in Furniture).
"""

SYSTEM_SERIES_LABELS = {
    "SMOKE_EXHAUST": "排烟系统",
    "MAKEUP_AIR": "补风系统",
    "FRESH_AIR": "新风系统",
    "SUPPLY_AIR": "空调送风",
    "RETURN_AIR": "空调回风",
    "EXHAUST_AIR": "排风系统",
    "FIRE_PROTECTION": "消防系统",
    "SPRINKLER": "自喷系统",
    "WATER_SUPPLY": "给水系统",
    "DRAINAGE": "排水系统",
    "CHW_SUPPLY": "冷冻水供水",
    "CHW_RETURN": "冷冻水回水",
    "CW_SUPPLY": "冷却水供水",
    "CW_RETURN": "冷却水回水",
    "HYDRONIC_SUPPLY": "空调水供水",
    "HYDRONIC_RETURN": "空调水回水",
    "HVAC_EQUIPMENT": "空调设备",
}

HVAC_EQUIPMENT_KEYWORDS = (
    "中央空调", "空调", "室内机", "天花机", "四面出风", "风机盘管",
    "fcu", "vrv", "vrf", "多联机", "ahu", "空调机组", "风柜",
)

SPRINKLER_KEYWORDS = ("喷头", "sprinkler", "洒水喷头", "下垂型喷头", "直立型喷头")

MEP_CATEGORY_KEYWORDS = (
    "风管", "风管管件", "风管附件", "风口", "机械设备", "管道", "管件",
    "管道附件", "阀门", "喷头", "桥架", "线管", "电气设备", "卫生器具",
    "duct", "pipe", "sprinkler", "mechanical equipment", "cable tray", "conduit",
)

NON_SYSTEM_CATEGORY_KEYWORDS = (
    "家具", "门", "窗", "墙", "楼板", "屋顶", "结构柱", "结构梁", "结构基础",
    "常规模型", "栏杆", "楼梯", "幕墙", "照明设备", "灯具", "软装",
    "furniture", "door", "window", "wall", "floor", "roof", "generic model",
)

UNASSIGNED_VALUES = {
    "", "未分配系统", "未命名系统", "未连接系统", "unassigned", "none", "null",
}


def _text(metadata):
    values = (
        metadata.get("display_name"), metadata.get("obj_name"), metadata.get("family"),
        metadata.get("type"), metadata.get("category"), metadata.get("system_name"),
        metadata.get("system_type_name"), metadata.get("system_abbreviation"),
    )
    return " ".join(str(v or "") for v in values).lower()


def is_meaningful_system(value):
    return str(value or "").strip().lower() not in UNASSIGNED_VALUES


def is_system_capable(metadata):
    category = str(metadata.get("category") or "").lower()
    source_category = str(metadata.get("source_category") or "").lower()
    combined = f"{category} {source_category}"
    if any(keyword.lower() in combined for keyword in MEP_CATEGORY_KEYWORDS):
        return True
    if metadata.get("has_mep_connector") is True:
        return True
    if is_meaningful_system(metadata.get("system_name")) or is_meaningful_system(metadata.get("system_type_name")):
        return True
    return False


def system_series_from_metadata(metadata):
    code = str(metadata.get("system_code") or "").strip()
    if code in SYSTEM_SERIES_LABELS:
        return SYSTEM_SERIES_LABELS[code]

    type_name = str(metadata.get("system_type_name") or "").strip()
    system_name = str(metadata.get("system_name") or "").strip()
    text = f"{type_name} {system_name}".lower()
    ordered = (
        (("排烟",), "排烟系统"),
        (("补风",), "补风系统"),
        (("新风",), "新风系统"),
        (("回风",), "空调回风"),
        (("送风", "supply"), "空调送风"),
        (("排风", "exhaust"), "排风系统"),
        (("喷淋", "自喷", "sprinkler"), "自喷系统"),
        (("消火栓", "消防"), "消防系统"),
        (("冷冻水供",), "冷冻水供水"),
        (("冷冻水回",), "冷冻水回水"),
        (("给水",), "给水系统"),
        (("排水", "污水", "废水"), "排水系统"),
    )
    for keywords, label in ordered:
        if any(keyword in text for keyword in keywords):
            return label

    if is_meaningful_system(type_name):
        return type_name
    if is_meaningful_system(system_name):
        return system_name
    return "未连接系统" if is_system_capable(metadata) else str(metadata.get("category") or "未分类")


def enrich_metadata(raw):
    """Return a copy with practical Blender semantic classification."""
    metadata = dict(raw or {})
    source_category = str(metadata.get("source_category") or metadata.get("category") or "未分类")
    metadata["source_category"] = source_category
    metadata.setdefault("category", source_category)
    metadata.setdefault("discipline", "其他")
    metadata.setdefault("system_code", "UNASSIGNED")
    metadata.setdefault("system_name", "未分配系统")
    metadata.setdefault("system_type_name", "")

    text = _text(metadata)

    # A badly authored Furniture/Generic Model family may still be a real HVAC unit.
    if any(keyword in text for keyword in HVAC_EQUIPMENT_KEYWORDS):
        metadata["category"] = "空调设备"
        metadata["discipline"] = "暖通"
        metadata["semantic_override"] = "family_keyword_hvac"
        if not is_meaningful_system(metadata.get("system_name")) and not is_meaningful_system(metadata.get("system_type_name")):
            metadata["system_code"] = "HVAC_EQUIPMENT"
            metadata["system_series"] = "空调设备"
            metadata["system_name"] = ""
            metadata["system_type_name"] = ""

    # Sprinkler families are often in a generic category but are semantically fire protection.
    if any(keyword in text for keyword in SPRINKLER_KEYWORDS):
        metadata["category"] = "喷头"
        metadata["discipline"] = "消防"
        metadata["semantic_override"] = "family_keyword_sprinkler"
        if not is_meaningful_system(metadata.get("system_name")) and not is_meaningful_system(metadata.get("system_type_name")):
            metadata["system_code"] = "SPRINKLER"
            metadata["system_series"] = "自喷系统"

    if not metadata.get("system_series"):
        metadata["system_series"] = system_series_from_metadata(metadata)

    # Non-MEP objects should never be put into a fake “unassigned system” collection.
    if not is_system_capable(metadata):
        metadata["system_series"] = str(metadata.get("category") or "未分类")
        metadata["system_name"] = ""
        metadata["system_type_name"] = ""
        if metadata.get("system_code") in {"UNASSIGNED", "OTHER_SYSTEM", ""}:
            metadata["system_code"] = "NON_MEP"
    elif not is_meaningful_system(metadata.get("system_name")) and not is_meaningful_system(metadata.get("system_type_name")):
        # Keep explicit equipment/sprinkler semantic group; otherwise mark genuinely unconnected MEP.
        if metadata.get("system_code") not in {"HVAC_EQUIPMENT", "SPRINKLER"}:
            metadata["system_series"] = "未连接系统"

    return metadata


def family_type_key(metadata):
    family = str(metadata.get("family") or "").strip()
    type_name = str(metadata.get("type") or "").strip()
    category = str(metadata.get("category") or "").strip()
    return (category, family, type_name)
