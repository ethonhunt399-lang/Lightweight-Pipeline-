# SLBH Revit Bridge v0.3.2 Manual Test Checklist

## Revit export
- Revit 2019 and Revit 2020 both load their own add-in without overwriting each other.
- Export dialog shows the active-view linked model list.
- Family/type scan appears before save path selection.
- Same sprinkler family/type can be set to PROTOTYPE and exports as shared mesh instances in Blender.
- A selected family/type set to FULL exports as unique mesh and does not receive prototype_instance geometry mode.
- A selected family/type set to SKIP is absent from Blender and counted in performance.rule_skipped_elements.
- Fast mode recommendations can be changed by the user before export.
- Exporting an already-used package name creates a suffixed folder instead of overwriting silently.
- Export completion dialog shows the real saved folder.

## Geometry correctness
- Different geometry under the same family/type is not incorrectly shared.
- Different material-slot combinations under the same family/type are not incorrectly shared.
- Mirrored, flipped, rotated, and negative-transform instances are visually correct.
- Active section box does not cause clipped FamilyInstances to share unclipped prototypes.
- Straight round pipes and ducts are still parameterized.
- Line-based round structural columns/braces with readable diameter use ROUND_STRUCTURAL_COLUMN.
- Complex, cut, point-based, or unreliable round columns fall back to mesh export.

## Linked models
- Host plus one architecture link imports in the correct position.
- Multiple discipline links retain source_model_key and source document properties.
- The same linked file placed twice appears twice with correct transforms.
- Linked model rotation and shared/project coordinate transforms are correct.
- Linked model repeated families either instance safely or fall back to mesh.
- Unloaded links are listed in skipped_links and do not fail the whole export.

## Blender import
- Blender 4.5 LTS imports a v0.3.2 package without version errors.
- Object custom properties include primitive_type, export_action, family_rule_key, importer_version, exporter_version, revit_version.
- Source prototype instances share Mesh data while remaining independently selectable.
- Level/type merge proxy tools from v0.3.1 still work.
- Materials, Revit color mode, system color mode, and white-model style remain usable on linked and host elements.
- Collection reorganization reuses existing collections and does not create duplicate .001 collections.

## Performance notes to record
- Source element count.
- Host element count.
- Linked element count.
- Unique prototype count.
- Prototype instance count.
- Rule skipped element count.
- Estimated repeated faces avoided.
- Blender object count before and after optional level/type merge proxies.
