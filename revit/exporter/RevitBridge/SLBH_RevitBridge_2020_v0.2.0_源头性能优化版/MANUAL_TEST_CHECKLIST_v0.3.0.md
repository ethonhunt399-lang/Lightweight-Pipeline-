# SLBH Revit Bridge v0.3.0 Manual Test Checklist

## Revit Test Matrix
- [ ] Revit 2019: host model plus one architectural linked model.
- [ ] Revit 2020: host model plus one architectural linked model.
- [ ] Host model plus multiple linked models from different disciplines.
- [ ] Same linked file placed twice.
- [ ] Linked model rotated and translated.
- [ ] Linked model positioned by shared coordinates.
- [ ] Linked model contains walls, beams, slabs, ducts, pipes, sprinklers, and equipment.
- [ ] Linked model contains repeated family instances.
- [ ] Some linked elements hidden in the current 3D view.
- [ ] Linked model cut by an active section box.
- [ ] One link instance is unloaded.

## Expected Results
- [ ] Linked elements are not missing from the Blender import.
- [ ] Coordinates, rotation, mirror state, scale, and direction are correct.
- [ ] Duplicate link placements appear in their own positions and do not collapse into the host.
- [ ] Systems, materials, Revit display colors, and collection classification work for linked elements.
- [ ] Host and linked elements with the same ElementId do not overwrite or skip each other.
- [ ] Repeated family/prototype sharing still works within the same source model.
- [ ] Different linked files with the same family/type names are not incorrectly merged.
- [ ] Current view hidden elements remain excluded.
- [ ] Section-box-cut linked elements are imported as correct clipped mesh when needed.
- [ ] Unloaded links are listed in the export/import report and do not fail the export.
- [ ] Blender 4.5 LTS add-on installs, registers, unregisters, and imports the package.
