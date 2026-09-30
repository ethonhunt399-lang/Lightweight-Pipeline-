# SLBH Revit Bridge v0.3.5 Manual Test Checklist

## Multiple imports from the same Revit file
- Export two different `.slbh` packages from the same Revit file, for example different 3D views or different names.
- Import package A into Blender.
- Import package B into the same Blender scene.
- Confirm two independent project root collections are created.
- Confirm the roots do not merge only because `project_name` is the same.
- Confirm objects from package A and package B have different `slbh.import_id`.
- Reorganize collections and confirm package A and B remain separated.
- Create/delete overall display proxy and confirm it does not accidentally mix unrelated imported packages unless intentionally scoped.

## Round pipe and round column optimization
- Re-export with Revit add-in v0.3.5.
- Import with Blender add-on v0.3.5.
- Select a straight round pipe fully inside the section box.
- Confirm `slbh.geometry_mode = straight_round_curve`.
- Confirm it imports as a low-side-count smooth cylinder instead of dense Revit triangulated mesh.
- Select a pipe clipped by the section box.
- Confirm it remains mesh and matches the clipped view geometry.
- Test round duct and round structural column in the same way.

## Screenshot diagnosis
- If a selected round pipe still shows dense triangles, check custom properties:
  - `slbh.geometry_mode = mesh`: it did not use the optimized path; inspect whether it is old data, clipped, or not a straight MEPCurve.
  - `slbh.geometry_mode = straight_round_curve`: it is optimized; visible edges are only the generated low-side-count cylinder mesh.
