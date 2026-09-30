bl_info = {
    "name": "SLBH Revit桥接",
    "author": "SLBH",
    "version": (0, 3, 6),
    "blender": (4, 5, 0),
    "location": "3D视图 > 侧栏 > SLBH",
    "description": "导入Revit桥接包，支持Revit端原型实例、系统分类、材质同步与大模型轻量管理",
    "category": "Import-Export",
}

import math

import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup

from .importer import import_bridge_package
from .materials import reapply_material_mode
from .display_proxy import (
    delete_overall_display_proxy,
    enter_large_model_mode,
    rebuild_overall_display_proxy,
    scan_overall_proxy_sources,
    set_overall_display_mode,
)
from .optimization import (
    apply_viewport_optimization,
    delete_merge_proxies,
    heavy_family_report,
    instance_duplicate_meshes,
    replace_with_bbox_proxies,
    rebuild_level_type_merge_proxies,
    restore_viewport,
    set_merge_display_mode,
)
from .organizer import reorganize_bridge_objects
from .selection import isolate_same_source_model, select_matching, select_same_active_material


class SLBHBridgePreferences(AddonPreferences):
    bl_idname = __name__

    material_library: StringProperty(
        name="Blender材质库",
        description="包含SLBH专属材质的.blend文件；为空时创建基础材质",
        subtype="FILE_PATH",
        default="",
    )

    def draw(self, context):
        self.layout.prop(self, "material_library")


class SLBHBridgeSettings(PropertyGroup):
    package_path: StringProperty(name="桥接包", subtype="FILE_PATH")
    organize_mode: EnumProperty(
        name="集合组织",
        items=[
            ("SOURCE_LEVEL_DISCIPLINE_SERIES", "Source > Level > Discipline > Series", "Group linked imports by source model first"),
            ("SOURCE_INSTANCE_LEVEL_DISCIPLINE_SERIES", "Source > Link Instance > Level", "Group repeated link placements independently"),
            ("LEVEL_SOURCE_DISCIPLINE_SERIES", "Level > Source > Discipline", "Keep floor first, then split by source model"),
            ("LEVEL_DISCIPLINE_SERIES", "楼层 → 专业 → 系列", "按楼层、专业和系统系列组织，推荐"),
            ("LEVEL_DISCIPLINE", "楼层 → 专业", "按楼层和专业组织"),
            ("LEVEL_SYSTEM", "楼层 → 系统", "按楼层和系统组织"),
            ("DISCIPLINE_SYSTEM", "专业 → 系统", "按专业和系统组织"),
            ("LEVEL", "按楼层", "只按楼层组织"),
            ("DISCIPLINE", "按专业", "只按专业组织"),
            ("SYSTEM", "按系统系列", "按归一化系统系列组织"),
            ("SYSTEM_NAME", "按具体系统名称", "按Revit具体系统名称组织"),
            ("CATEGORY", "按类别", "只按语义类别组织"),
        ],
        default="SOURCE_LEVEL_DISCIPLINE_SERIES",
    )
    material_mode: EnumProperty(
        name="材质显示",
        items=[
            ("REALISTIC", "写实基础材质", "同基础材质共用Blender专属材质"),
            ("REVIT_COLOR", "Revit着色", "保留当前Revit视图的着色颜色"),
            ("SYSTEM_COLOR", "SLBH系统配色", "根据系统代码统一配色"),
        ],
        default="REVIT_COLOR",
    )
    round_quality: EnumProperty(
        name="圆管精度",
        items=[
            ("FAST", "快速", "8/12/16边，适合鸟瞰与大模型"),
            ("STANDARD", "标准", "12/16/24边，适合常规汇报"),
            ("FINE", "精细", "16/24/32边，适合局部特写"),
        ],
        default="STANDARD",
    )
    optimization_profile: EnumProperty(
        name="导入轻量方案",
        items=[
            ("SAFE", "安全", "只共享完全相同网格，不改变视口显示"),
            ("STANDARD", "标准轻量", "共享重复族；小型高面数重复构件以包围盒显示"),
            ("LIGHT", "大型模型", "更积极地将小型重复构件以包围盒显示"),
        ],
        default="STANDARD",
    )
    share_instances: BoolProperty(
        name="重复族共享网格",
        description="同族同类型且几何完全一致的对象共享一个Mesh数据块",
        default=True,
    )
    viewport_proxy_mode: EnumProperty(
        name="小构件视口模式",
        items=[
            ("BOUNDS", "包围盒", "视口只显示包围盒，渲染仍使用完整模型"),
            ("WIRE", "线框", "视口使用线框显示，渲染仍使用完整模型"),
            ("HIDE", "视口隐藏", "在视口中隐藏，渲染仍保留"),
            ("FULL", "完整显示", "不简化视口显示"),
        ],
        default="BOUNDS",
    )
    small_component_max_size: FloatProperty(
        name="小构件最大尺寸(m)",
        description="包围盒最大边小于该值时，才参与自动视口简化",
        default=0.35,
        min=0.01,
        max=5.0,
        precision=2,
    )
    heavy_face_threshold: IntProperty(
        name="高面数阈值",
        description="单个对象达到该面数，才参与自动视口简化",
        default=300,
        min=20,
        max=100000,
    )
    repeat_threshold: IntProperty(
        name="重复数量阈值",
        description="同族同类型达到该数量，才参与自动视口简化",
        default=4,
        min=2,
        max=10000,
    )
    merge_min_count: IntProperty(
        name="Merge minimum count",
        description="Only create a level/type merge proxy when a group has at least this many objects",
        default=2,
        min=2,
        max=100000,
    )
    merge_hide_originals: BoolProperty(
        name="Hide originals after merge",
        description="Keep original BIM objects and hide them after creating render proxies",
        default=True,
    )
    overall_proxy_scope: EnumProperty(
        name="整体代理范围",
        items=[
            ("ALL_SOURCE", "全部源模型", "Use all SLBH BIM source objects"),
            ("VISIBLE_ONLY", "当前可见对象", "Use currently visible source objects"),
            ("LEVEL_RANGE", "指定楼层范围", "Use source objects whose level is within the range below"),
            ("DISCIPLINE", "指定专业", "Use source objects from the selected discipline"),
            ("SELECTED_OBJECTS", "当前选中对象", "Use selected source objects"),
            ("ACTIVE_SOURCE_MODEL", "当前来源模型", "Use the active object's source model"),
        ],
        default="ALL_SOURCE",
    )
    overall_level_start: StringProperty(
        name="起始楼层",
        description="For example F01, 1F, B02. Empty means no lower limit.",
        default="",
    )
    overall_level_end: StringProperty(
        name="结束楼层",
        description="For example F20. Empty means no upper limit.",
        default="",
    )
    overall_discipline: EnumProperty(
        name="专业",
        items=[
            ("ALL", "全部", ""),
            ("暖通", "暖通", ""),
            ("给排水", "给排水", ""),
            ("消防", "消防", ""),
            ("电气", "电气", ""),
            ("建筑", "建筑", ""),
            ("结构", "结构", ""),
            ("其他", "其他", ""),
        ],
        default="ALL",
    )
    overall_small_mode: EnumProperty(
        name="小构件处理",
        items=[
            ("DEFAULT", "默认推荐", "Exclude tiny clutter, use low boxes for small air terminals/valves/lights"),
            ("KEEP", "全部保留", "Include small source objects as full proxy geometry"),
            ("LOW", "小构件低模", "Use bounding boxes for small components"),
            ("POINT", "小构件点位", "Use small point cubes for small components"),
            ("EXCLUDE", "小构件不加入", "Exclude small components from the overall display proxy"),
        ],
        default="DEFAULT",
    )
    overall_group_rule: EnumProperty(
        name="合并规则",
        items=[
            ("LEVEL_DISCIPLINE_SYSTEM_MATERIAL", "楼层+专业+系统+材质", "Default block rule"),
            ("LEVEL_DISCIPLINE", "楼层+专业", ""),
            ("LEVEL_SYSTEM", "楼层+系统", ""),
            ("LEVEL_DISCIPLINE_SYSTEM", "楼层+专业+系统", ""),
            ("LEVEL_SYSTEM_MATERIAL", "楼层+系统+材质", ""),
            ("DISCIPLINE_SYSTEM", "专业+系统", ""),
            ("PROJECT_SYSTEM", "全项目按系统", ""),
        ],
        default="LEVEL_DISCIPLINE_SYSTEM_MATERIAL",
    )
    overall_hide_source_after_create: BoolProperty(
        name="生成后隐藏源模型",
        description="Keep BIM source objects but hide the source collection after proxy creation",
        default=True,
    )
    apply_scope: EnumProperty(
        name="应用范围",
        items=[
            ("SELECTED", "选中对象", "只处理当前选中对象"),
            ("ALL_BRIDGE", "全部桥接对象", "处理当前场景全部SLBH桥接对象"),
        ],
        default="ALL_BRIDGE",
    )


def _scope_objects(context, settings):
    if settings.apply_scope == "SELECTED":
        return list(context.selected_objects)
    return [obj for obj in context.scene.objects if "slbh.element_id" in obj]


class SLBH_OT_import_bridge(Operator):
    bl_idname = "slbh.import_revit_bridge"
    bl_label = "导入Revit桥接包"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH")

    def invoke(self, context, event):
        self.filepath = context.scene.slbh_bridge.package_path
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        settings = context.scene.slbh_bridge
        try:
            report = import_bridge_package(
                context,
                self.filepath,
                settings.organize_mode,
                settings.material_mode,
                settings.round_quality,
                optimization={
                    "profile": settings.optimization_profile,
                    "share_instances": settings.share_instances,
                    "viewport_mode": settings.viewport_proxy_mode,
                    "max_size": settings.small_component_max_size,
                    "min_faces": settings.heavy_face_threshold,
                    "repeat_threshold": settings.repeat_threshold,
                },
            )
            settings.package_path = self.filepath
            self.report(
                {"INFO"},
                f"导入{report['objects']}个对象；源头实例{report.get('source_instances', 0)}个；"
                f"共享原型{report.get('source_prototypes', 0)}个；唯一网格{report['unique_meshes']}个；"
                f"视口简化{report['viewport_proxies']}个",
            )
            if report.get("warnings"):
                self.report({"WARNING"}, "；".join(report["warnings"]))
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class SLBH_OT_select_same(Operator):
    bl_idname = "slbh.select_same_revit_property"
    bl_label = "选择同类构件"
    bl_options = {"REGISTER", "UNDO"}

    key: EnumProperty(
        items=[
            ("slbh.category", "同类别", ""),
            ("slbh.type", "同类型", ""),
            ("slbh.family", "同族", ""),
            ("slbh.level", "同楼层", ""),
            ("slbh.discipline", "同专业", ""),
            ("slbh.system_name", "同系统名称", ""),
            ("slbh.system_series", "同系统系列", ""),
            ("slbh.system_type", "同系统类型", ""),
            ("slbh.system_code", "同系统代码", ""),
            ("slbh.base_material", "同基础材质", ""),
            ("slbh.display_color_hex", "同Revit着色", ""),
            ("slbh.prototype_id", "同共享原型", ""),
            ("slbh.source_model_key", "Same source model", ""),
            ("slbh.link_instance_id", "Same link instance", ""),
        ]
    )

    def execute(self, context):
        count = select_matching(context, self.key)
        self.report({"INFO"}, f"已选择 {count} 个对象")
        return {"FINISHED"}


class SLBH_OT_select_same_material(Operator):
    bl_idname = "slbh.select_same_active_material"
    bl_label = "选择同一Blender材质"
    bl_options = {"REGISTER", "UNDO"}

    scope: EnumProperty(
        items=[("SCENE", "整个场景", ""), ("COLLECTION", "当前集合", "")],
        default="SCENE",
    )

    def execute(self, context):
        count = select_same_active_material(context, self.scope)
        self.report({"INFO"}, f"已选择 {count} 个包含当前材质的对象")
        return {"FINISHED"}


class SLBH_OT_isolate_source_model(Operator):
    bl_idname = "slbh.isolate_source_model"
    bl_label = "Isolate Source Model"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        count = isolate_same_source_model(context)
        self.report({"INFO"}, f"Isolated {count} objects from the active source model")
        return {"FINISHED"}


class SLBH_OT_reorganize_bridge(Operator):
    bl_idname = "slbh.reorganize_bridge_objects"
    bl_label = "按当前规则重新整理集合"
    bl_description = "修正语义分类、合并重复集合，并按当前规则重新归类"
    bl_options = {"REGISTER", "UNDO"}

    selected_only: BoolProperty(name="仅选中对象", default=False)

    def execute(self, context):
        settings = context.scene.slbh_bridge
        count = reorganize_bridge_objects(context, settings.organize_mode, self.selected_only)
        self.report({"INFO"}, f"已重新整理 {count} 个桥接对象")
        return {"FINISHED"}


class SLBH_OT_apply_material_mode(Operator):
    bl_idname = "slbh.apply_bridge_material_mode"
    bl_label = "应用当前材质显示模式"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.slbh_bridge
        changed = 0
        for obj in _scope_objects(context, settings):
            if reapply_material_mode(obj, settings.material_mode):
                changed += 1
        self.report({"INFO"}, f"已更新 {changed} 个对象的材质显示")
        return {"FINISHED"}


class SLBH_OT_instance_duplicate_meshes(Operator):
    bl_idname = "slbh.instance_duplicate_meshes"
    bl_label = "重复族共享网格"
    bl_description = "对现有模型查找完全相同的重复族，使其共享Mesh数据；适合喷头、风口、阀门等"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.slbh_bridge
        result = instance_duplicate_meshes(_scope_objects(context, settings))
        self.report(
            {"INFO"},
            f"完成：共享实例{result['reused']}个，原型{result['unique']}个，清理重复网格{result['removed_meshes']}个",
        )
        return {"FINISHED"}


class SLBH_OT_viewport_optimize(Operator):
    bl_idname = "slbh.optimize_small_component_viewport"
    bl_label = "小构件视口轻量化"
    bl_description = "按尺寸、面数和重复数量，将喷头等小型高面数构件改为包围盒/线框显示；不影响渲染"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.slbh_bridge
        count = apply_viewport_optimization(
            _scope_objects(context, settings),
            settings.viewport_proxy_mode,
            settings.small_component_max_size,
            settings.heavy_face_threshold,
            settings.repeat_threshold,
        )
        self.report({"INFO"}, f"已轻量显示 {count} 个小型重复构件")
        return {"FINISHED"}


class SLBH_OT_restore_viewport(Operator):
    bl_idname = "slbh.restore_bridge_viewport"
    bl_label = "恢复完整视口显示"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.slbh_bridge
        count = restore_viewport(_scope_objects(context, settings))
        self.report({"INFO"}, f"已恢复 {count} 个对象")
        return {"FINISHED"}


class SLBH_OT_heavy_family_report(Operator):
    bl_idname = "slbh.heavy_family_report"
    bl_label = "统计高负担族"
    bl_description = "按族和类型统计数量、唯一网格和总面数，结果写入文本编辑器"
    bl_options = {"REGISTER"}

    def execute(self, context):
        settings = context.scene.slbh_bridge
        text, ranked = heavy_family_report(_scope_objects(context, settings), limit=30)
        block = bpy.data.texts.get("SLBH_高负担族统计") or bpy.data.texts.new("SLBH_高负担族统计")
        block.clear()
        block.write(text)
        if ranked:
            key, data = ranked[0]
            self.report({"INFO"}, f"最高负担：{key[1] or key[0]} / {key[2]}，总面数{data['total_faces']}")
        else:
            self.report({"INFO"}, "没有可统计的桥接网格")
        return {"FINISHED"}


class SLBH_OT_replace_lowpoly_proxy(Operator):
    bl_idname = "slbh.replace_small_components_with_proxy"
    bl_label = "低模包络替换（不可逆）"
    bl_description = "将小型高面数重复构件永久替换为共享低模包络；请先另存备份"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        settings = context.scene.slbh_bridge
        result = replace_with_bbox_proxies(
            _scope_objects(context, settings),
            settings.small_component_max_size,
            settings.heavy_face_threshold,
            settings.repeat_threshold,
        )
        self.report(
            {"INFO"},
            f"已替换{result['changed']}个构件：面数 {result['before']} → {result['after']}",
        )
        return {"FINISHED"}


class SLBH_OT_rebuild_level_type_merge_proxies(Operator):
    bl_idname = "slbh.rebuild_level_type_merge_proxies"
    bl_label = "Rebuild Level/Type Merge Proxies"
    bl_description = "Create render proxies by merging objects with the same source model, level, family, type, system and base material"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.slbh_bridge
        result = rebuild_level_type_merge_proxies(
            context,
            _scope_objects(context, settings),
            min_count=settings.merge_min_count,
            hide_originals=settings.merge_hide_originals,
        )
        self.report(
            {"INFO"},
            "Created {created} merge proxies from {merged_objects} objects; deleted {deleted} old proxies".format(**result),
        )
        return {"FINISHED"}


class SLBH_OT_delete_merge_proxies(Operator):
    bl_idname = "slbh.delete_merge_proxies"
    bl_label = "Delete Merge Proxies"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        result = delete_merge_proxies(context, restore_originals=True)
        self.report({"INFO"}, f"Deleted {result['deleted']} merge proxies")
        return {"FINISHED"}


class SLBH_OT_set_merge_display_mode(Operator):
    bl_idname = "slbh.set_merge_display_mode"
    bl_label = "Set Merge Display Mode"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(
        items=[
            ("ORIGINAL", "Original Objects", ""),
            ("PROXY", "Merge Proxies", ""),
            ("BOTH", "Both", ""),
        ],
        default="PROXY",
    )

    def execute(self, context):
        result = set_merge_display_mode(context, self.mode)
        self.report({"INFO"}, f"Display mode {self.mode}: {result['originals']} originals, {result['proxies']} proxies")
        return {"FINISHED"}


class SLBH_OT_scan_overall_display_proxy(Operator):
    bl_idname = "slbh.scan_overall_display_proxy"
    bl_label = "扫描当前源模型"
    bl_description = "Scan source BIM objects and write a small-component proxy report to the Text editor"
    bl_options = {"REGISTER"}

    def execute(self, context):
        settings = context.scene.slbh_bridge
        result = scan_overall_proxy_sources(context, settings)
        self.report(
            {"INFO"},
            f"Source objects {result['source_objects']}; high-load groups {result['groups']}; faces {result['faces']}",
        )
        return {"FINISHED"}


class SLBH_OT_rebuild_overall_display_proxy(Operator):
    bl_idname = "slbh.rebuild_overall_display_proxy"
    bl_label = "创建/更新整体展示代理"
    bl_description = "Create an overall display proxy by merging source BIM objects into level/discipline/system blocks"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.slbh_bridge
        try:
            result = rebuild_overall_display_proxy(context, settings)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            "Proxy objects {proxy_objects}; source {source_objects}; filtered {filtered_objects}; "
            "object reduction {object_reduction_percent:.1f}%".format(**result),
        )
        return {"FINISHED"}


class SLBH_OT_delete_overall_display_proxy(Operator):
    bl_idname = "slbh.delete_overall_display_proxy"
    bl_label = "删除整体展示代理"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        result = delete_overall_display_proxy(context, restore_source=True)
        self.report({"INFO"}, f"Deleted {result['deleted']} overall display proxies")
        return {"FINISHED"}


class SLBH_OT_set_overall_display_mode(Operator):
    bl_idname = "slbh.set_overall_display_mode"
    bl_label = "切换源模型/代理显示"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(
        items=[
            ("SOURCE_ONLY", "仅BIM源模型", ""),
            ("PROXY_ONLY", "仅整体展示代理", ""),
            ("BOTH", "同时显示", ""),
            ("HIDE_ALL", "全部隐藏", ""),
        ],
        default="PROXY_ONLY",
    )

    def execute(self, context):
        result = set_overall_display_mode(context, self.mode)
        self.report(
            {"INFO"},
            f"Display {self.mode}: roots={result['roots']} source={result['source_visible']} proxy={result['proxy_visible']}",
        )
        return {"FINISHED"}


class SLBH_OT_enter_large_model_mode(Operator):
    bl_idname = "slbh.enter_large_model_mode"
    bl_label = "进入大模型工作模式"
    bl_description = "Hide BIM source collections, show overall display proxies, and reduce viewport overlays"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        result = enter_large_model_mode(context)
        self.report({"INFO"}, f"Large model mode enabled for {result['roots']} project roots")
        return {"FINISHED"}


class SLBH_OT_safe_optimize_mesh(Operator):
    bl_idname = "slbh.safe_optimize_imported_mesh"
    bl_label = "安全优化选中旧网格"
    bl_description = "合并重合点、三角转四边并溶解近共面冗余边；建议先保存文件"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        objects = [obj for obj in context.selected_objects if obj.type == "MESH"]
        if not objects:
            self.report({"WARNING"}, "请先选择需要优化的网格对象")
            return {"CANCELLED"}

        before = sum(len(obj.data.polygons) for obj in objects)
        processed = 0
        for obj in objects:
            # Shared instances must be made single-user before destructive cleanup.
            if obj.data.users > 1:
                obj.data = obj.data.copy()
            mesh = obj.data
            bm = bmesh.new()
            try:
                bm.from_mesh(mesh)
                bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-6)
                triangles = [face for face in bm.faces if len(face.verts) == 3]
                if triangles:
                    bmesh.ops.join_triangles(
                        bm,
                        faces=triangles,
                        angle_face_threshold=math.radians(3.0),
                        angle_shape_threshold=math.radians(3.0),
                    )
                bmesh.ops.dissolve_limit(
                    bm,
                    angle_limit=math.radians(0.5),
                    use_dissolve_boundaries=False,
                    verts=list(bm.verts),
                    edges=list(bm.edges),
                )
                bm.normal_update()
                bm.to_mesh(mesh)
                mesh.update()
                processed += 1
            finally:
                bm.free()

        after = sum(len(obj.data.polygons) for obj in objects)
        self.report({"INFO"}, f"已优化{processed}个对象：面数 {before} → {after}")
        return {"FINISHED"}


class SLBH_PT_bridge(Panel):
    bl_label = "Revit桥接"
    bl_idname = "SLBH_PT_revit_bridge"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "SLBH"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.slbh_bridge

        box = layout.box()
        box.label(text="导入设置")
        box.prop(settings, "package_path")
        box.prop(settings, "organize_mode")
        box.prop(settings, "material_mode")
        box.prop(settings, "round_quality")
        box.prop(settings, "optimization_profile")
        box.prop(settings, "share_instances")
        if settings.optimization_profile != "SAFE":
            box.prop(settings, "viewport_proxy_mode")
            box.prop(settings, "small_component_max_size")
            box.prop(settings, "heavy_face_threshold")
            box.prop(settings, "repeat_threshold")
        box.operator("slbh.import_revit_bridge", icon="IMPORT")
        box.operator("slbh.reorganize_bridge_objects", icon="OUTLINER_COLLECTION")

        box = layout.box()
        box.label(text="整体展示代理")
        box.prop(settings, "overall_proxy_scope")
        if settings.overall_proxy_scope == "LEVEL_RANGE":
            row = box.row(align=True)
            row.prop(settings, "overall_level_start")
            row.prop(settings, "overall_level_end")
        if settings.overall_proxy_scope == "DISCIPLINE":
            box.prop(settings, "overall_discipline")
        box.prop(settings, "overall_small_mode")
        box.prop(settings, "overall_group_rule")
        box.prop(settings, "overall_hide_source_after_create")
        row = box.row(align=True)
        row.operator("slbh.scan_overall_display_proxy", icon="VIEWZOOM")
        row.operator("slbh.rebuild_overall_display_proxy", icon="MOD_BUILD")
        row = box.row(align=True)
        op = row.operator("slbh.set_overall_display_mode", text="源模型")
        op.mode = "SOURCE_ONLY"
        op = row.operator("slbh.set_overall_display_mode", text="代理")
        op.mode = "PROXY_ONLY"
        op = row.operator("slbh.set_overall_display_mode", text="同时")
        op.mode = "BOTH"
        row = box.row(align=True)
        op = row.operator("slbh.set_overall_display_mode", text="全隐藏")
        op.mode = "HIDE_ALL"
        row.operator("slbh.enter_large_model_mode", text="大模型模式", icon="HIDE_ON")
        box.operator("slbh.delete_overall_display_proxy", icon="TRASH")

        box = layout.box()
        box.label(text="性能优化（v0.2支持源头实例）")
        box.prop(settings, "apply_scope")
        row = box.row(align=True)
        row.operator("slbh.instance_duplicate_meshes", icon="DUPLICATE")
        row.operator("slbh.heavy_family_report", icon="TEXT")
        row = box.row(align=True)
        row.operator("slbh.optimize_small_component_viewport", icon="CUBE")
        row.operator("slbh.restore_bridge_viewport", icon="HIDE_OFF")
        box.operator("slbh.replace_small_components_with_proxy", icon="CUBE")
        box.separator()
        box.label(text="Render merge proxies")
        box.prop(settings, "merge_min_count")
        box.prop(settings, "merge_hide_originals")
        box.operator("slbh.rebuild_level_type_merge_proxies", icon="MOD_BUILD")
        row = box.row(align=True)
        op = row.operator("slbh.set_merge_display_mode", text="Original")
        op.mode = "ORIGINAL"
        op = row.operator("slbh.set_merge_display_mode", text="Proxy")
        op.mode = "PROXY"
        op = row.operator("slbh.set_merge_display_mode", text="Both")
        op.mode = "BOTH"
        box.operator("slbh.delete_merge_proxies", icon="TRASH")
        box.operator("slbh.safe_optimize_imported_mesh", icon="MOD_DECIM")

        box = layout.box()
        box.label(text="材质调整")
        box.operator("slbh.apply_bridge_material_mode", icon="MATERIAL")

        obj = context.active_object
        if obj and "slbh.element_id" in obj:
            source_box = layout.box()
            source_box.label(text=f"Source model: {obj.get('slbh.source_document_name', 'Host Model')}")
            source_box.label(text=f"Linked: {bool(obj.get('slbh.is_linked_element', False))}")
            if obj.get("slbh.is_linked_element", False):
                source_box.label(text=f"Link instance: {obj.get('slbh.link_instance_name', '')}")
                source_box.label(text=f"Linked UniqueId: {obj.get('slbh.linked_unique_id', '')}")
            row = source_box.row(align=True)
            op = row.operator("slbh.select_same_revit_property", text="Same source")
            op.key = "slbh.source_model_key"
            op = row.operator("slbh.select_same_revit_property", text="Same link")
            op.key = "slbh.link_instance_id"
            source_box.operator("slbh.isolate_source_model", text="Isolate source model", icon="HIDE_OFF")
            box = layout.box()
            box.label(text="Revit构件信息")
            box.label(text=f"源类别：{obj.get('slbh.source_category', '')}")
            box.label(text=f"语义类别：{obj.get('slbh.category', '')}")
            box.label(text=f"族：{obj.get('slbh.family', '')}")
            box.label(text=f"类型：{obj.get('slbh.type', '')}")
            box.label(text=f"楼层：{obj.get('slbh.level', '')}")
            box.label(text=f"专业：{obj.get('slbh.discipline', '')}")
            box.label(text=f"系统系列：{obj.get('slbh.system_series', '')}")
            box.label(text=f"系统：{obj.get('slbh.system_name', '')}")
            box.label(text=f"系统类型：{obj.get('slbh.system_type', '')}")
            box.label(text=f"共享原型：{obj.get('slbh.prototype_id', '')}")
            box.label(text=f"基础材质：{obj.get('slbh.base_material', '')}")

            row = box.row(align=True)
            op = row.operator("slbh.select_same_revit_property", text="同类别")
            op.key = "slbh.category"
            op = row.operator("slbh.select_same_revit_property", text="同族")
            op.key = "slbh.family"
            op = row.operator("slbh.select_same_revit_property", text="同类型")
            op.key = "slbh.type"

            row = box.row(align=True)
            op = row.operator("slbh.select_same_revit_property", text="同系列")
            op.key = "slbh.system_series"
            op = row.operator("slbh.select_same_revit_property", text="同原型")
            op.key = "slbh.prototype_id"

            row = box.row(align=True)
            op = row.operator("slbh.select_same_revit_property", text="同基础材质")
            op.key = "slbh.base_material"
            op = row.operator("slbh.select_same_active_material", text="同当前材质")
            op.scope = "SCENE"


_CLASSES = (
    SLBHBridgePreferences,
    SLBHBridgeSettings,
    SLBH_OT_import_bridge,
    SLBH_OT_select_same,
    SLBH_OT_select_same_material,
    SLBH_OT_isolate_source_model,
    SLBH_OT_reorganize_bridge,
    SLBH_OT_apply_material_mode,
    SLBH_OT_instance_duplicate_meshes,
    SLBH_OT_viewport_optimize,
    SLBH_OT_restore_viewport,
    SLBH_OT_heavy_family_report,
    SLBH_OT_replace_lowpoly_proxy,
    SLBH_OT_rebuild_level_type_merge_proxies,
    SLBH_OT_delete_merge_proxies,
    SLBH_OT_set_merge_display_mode,
    SLBH_OT_scan_overall_display_proxy,
    SLBH_OT_rebuild_overall_display_proxy,
    SLBH_OT_delete_overall_display_proxy,
    SLBH_OT_set_overall_display_mode,
    SLBH_OT_enter_large_model_mode,
    SLBH_OT_safe_optimize_mesh,
    SLBH_PT_bridge,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.slbh_bridge = bpy.props.PointerProperty(type=SLBHBridgeSettings)


def unregister():
    if hasattr(bpy.types.Scene, "slbh_bridge"):
        del bpy.types.Scene.slbh_bridge
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
