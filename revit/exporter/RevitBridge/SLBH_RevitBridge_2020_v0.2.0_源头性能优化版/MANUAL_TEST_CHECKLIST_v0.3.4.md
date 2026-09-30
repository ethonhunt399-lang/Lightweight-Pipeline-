# SLBH Revit Bridge v0.3.4 Manual Test Checklist

## Revit export
- Revit 2019 add-in compiles and loads.
- Revit 2020 add-in compiles and loads.
- Export a model containing straight round pipes.
- Export a model containing straight round ducts.
- Export a model containing line-based round steel columns or braces.
- Export a model containing point-based vertical round steel columns.
- Confirm export report `参数化直圆管` count increases for eligible round members.
- Confirm active section box still makes clipped round members fall back to mesh when needed.

## Blender import
- Import the v0.3.4 package in Blender 4.5 LTS.
- Confirm straight round pipes use `geometry_mode = straight_round_curve`.
- Confirm round steel columns use `primitive_type = ROUND_STRUCTURAL_COLUMN`.
- Fast quality uses visibly low side counts for full-building views.
- Standard quality is still visually acceptable but lower polygon than older builds.
- Fine quality remains smoother but no longer creates excessive 32-sided large round elements.

## Correctness
- Pipe, duct and round column length, diameter, position and direction are correct.
- Rotated and linked models remain in the correct coordinates.
- Non-round columns do not become round proxy geometry.
- Columns with plates, openings, cuts or unreliable dimensions remain original mesh.
- Overall display proxy from v0.3.3 still works after importing v0.3.4 data.

## Performance values to record
- Number of parametric round elements.
- Total source objects.
- Total source polygons after import.
- Overall display proxy object count and polygon count.
- Blender viewport responsiveness before and after proxy mode.
