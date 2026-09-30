# SLBH Revit Bridge v0.3.6 Manual Test Checklist

## Install
- Install the Revit 2019 v0.3.6 package.
- Install the Revit 2020 v0.3.6 package.
- Install the Blender v0.3.6 add-on ZIP in Blender 4.5 LTS.

## Revit Export
- Export from a non-perspective 3D view in Revit 2019.
- Export from a non-perspective 3D view in Revit 2020.
- Confirm the linked model dialog says link detail follows the active 3D view and Revit Link Display Settings.
- Export a view containing at least one linked model.
- Check project.json contains view_detail_level and link_detail_policy.

## Round Optimization
- Confirm round pipe imports as a low-segment circular primitive.
- Confirm round duct imports as a low-segment circular primitive.
- Confirm a true round steel column or round steel tube is optimized only when its family/type clearly indicates a round section.
- Confirm H-beam, I-beam, W-shape, rectangular beam, box beam, channel, and angle steel do not become cylinders.
- Confirm beams with chamfers or end cuts keep their original mesh unless an explicit optional proxy workflow is used.

## Blender Import
- Import two different .slbh exports from the same Revit project into one Blender scene.
- Confirm each import has an independent root collection instead of being merged unexpectedly.
- Confirm materials and collections do not create avoidable .001 duplicates.

## Linked Models
- Confirm linked model position, rotation, and source metadata remain correct.
- Confirm linked steel framing is not converted to round cylinders unless it is explicitly a round tube/rod family.

## Real Software Verification
- Compare one exported round pipe against the original Revit model for length, diameter, and location.
- Compare one exported steel beam with chamfers against the original Revit model for shape and orientation.
- Test active 3D view detail level changes in Revit and confirm linked model export follows the view/link display settings.
