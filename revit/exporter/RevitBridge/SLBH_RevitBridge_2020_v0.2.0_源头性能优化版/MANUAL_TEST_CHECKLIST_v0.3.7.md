# SLBH Revit Bridge v0.3.7 Manual Test Checklist

## Build / Install
- Run .\build_revit.ps1 -Year All without compiler errors.
- Install the Revit 2020 package and confirm the SLBH Tools ribbon loads.

## Regression (unchanged behaviour)
- Export a host-only 3D view; import in SLBH Toolbox; object count matches v0.3.6.
- Round pipes/ducts still import as low-segment primitives; steel beams keep their mesh.

## v0.3.7 fixes
- Host view with an element override (By Element colour) on host element N and a linked
  model loaded: linked elements must NOT inherit that colour.
- Linked round pipe that crosses an active section box: exported geometry stops at the box.
- Worksharing link: linked elements show the link document workset name.
- Standard profile: small linked fittings receive BOUNDS/PROXY lod hints like host ones.
- A DN100 pipe: parametric cylinder diameter matches the outside diameter in Revit.
- Force an export failure (e.g. read-only target folder after creation): EXPORT_FAILED.txt exists.
- Compare model.obj size with a v0.3.6 export of the same view (expected noticeably smaller).
