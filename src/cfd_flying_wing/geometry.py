from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .models import ConfigurationError, Design, GeometryArtifact, OpenVspSettings


class GeometryRunner:
    def generate(self, design: Design, artifact_dir: Path) -> GeometryArtifact:
        raise NotImplementedError


class OpenVspGeometryRunner(GeometryRunner):
    def __init__(self, settings: OpenVspSettings) -> None:
        self.settings = settings

    def generate(self, design: Design, artifact_dir: Path) -> GeometryArtifact:
        geometry_dir = artifact_dir / "geometry"
        geometry_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = geometry_dir / "design.json"
        script_path = geometry_dir / "generate_openvsp.py"
        geometry_path = geometry_dir / self.settings.geometry_filename

        metadata = {
            "design": design.as_dict(),
            "mean_chord_m": design.mean_chord_m,
            "root_chord_m": design.root_chord_m,
            "tip_chord_m": design.tip_chord_m,
            "aspect_ratio": design.aspect_ratio,
            "airfoil_path": str(self.settings.airfoil_path),
            "geometry_path": str(geometry_path),
        }
        metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        script_path.write_text(_openvsp_script(design, self.settings, geometry_path), encoding="utf-8")

        if not self.settings.openvsp_python:
            raise ConfigurationError(
                "OpenVSP is not configured. Set openvsp.openvsp_python to a Python executable "
                "that can import the OpenVSP Python module, or run the CLI with --mock."
            )
        if not self.settings.airfoil_path.exists():
            raise ConfigurationError(f"Configured airfoil file does not exist: {self.settings.airfoil_path}")

        completed = subprocess.run(
            [self.settings.openvsp_python, str(script_path)],
            cwd=geometry_dir,
            text=True,
            capture_output=True,
            check=False,
        )
        (geometry_dir / "openvsp.stdout.log").write_text(completed.stdout, encoding="utf-8")
        (geometry_dir / "openvsp.stderr.log").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode != 0:
            raise ConfigurationError(
                f"OpenVSP geometry generation failed with exit code {completed.returncode}. "
                f"See {geometry_dir / 'openvsp.stderr.log'}"
            )
        if not geometry_path.exists():
            raise ConfigurationError(f"OpenVSP completed but did not create expected geometry: {geometry_path}")

        return GeometryArtifact(geometry_path=geometry_path, metadata_path=metadata_path, script_path=script_path)


class MockGeometryRunner(GeometryRunner):
    def __init__(self, filename: str = "wing.stl") -> None:
        self.filename = filename

    def generate(self, design: Design, artifact_dir: Path) -> GeometryArtifact:
        geometry_dir = artifact_dir / "geometry"
        geometry_dir.mkdir(parents=True, exist_ok=True)
        geometry_path = geometry_dir / self.filename
        metadata_path = geometry_dir / "design.json"
        metadata_path.write_text(json.dumps(design.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
        geometry_path.write_text(_placeholder_ascii_stl(), encoding="utf-8")
        return GeometryArtifact(geometry_path=geometry_path, metadata_path=metadata_path, script_path=None)


def copy_geometry_to_case(geometry: GeometryArtifact, case_dir: Path) -> Path:
    target_dir = case_dir / "constant" / "triSurface"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / geometry.geometry_path.name
    shutil.copy2(geometry.geometry_path, target_path)
    return target_path


def _openvsp_script(design: Design, settings: OpenVspSettings, geometry_path: Path) -> str:
    airfoil_path = settings.airfoil_path.resolve()
    vsp_path = geometry_path.with_suffix(".vsp3").resolve()
    export_path = geometry_path.resolve()
    return f'''\
from pathlib import Path
import openvsp as vsp

span = {design.span_m!r}
root_chord = {design.root_chord_m!r}
tip_chord = {design.tip_chord_m!r}
sweep = {design.sweep_deg!r}
twist = {design.twist_deg!r}
airfoil_path = Path(r"{airfoil_path}")
vsp_path = r"{vsp_path}"
export_path = r"{export_path}"

vsp.ClearVSPModel()
wing_id = vsp.AddGeom("WING", "")

# This narrow OpenVSP mapping is isolated because exact parameter names can vary
# by OpenVSP version and model setup.
vsp.SetParmVal(wing_id, "TotalSpan", "WingGeom", span)
vsp.SetParmVal(wing_id, "Root_Chord", "XSec_1", root_chord)
vsp.SetParmVal(wing_id, "Tip_Chord", "XSec_1", tip_chord)
vsp.SetParmVal(wing_id, "Sweep", "XSec_1", sweep)
vsp.SetParmVal(wing_id, "Twist", "XSec_1", twist)

if airfoil_path.exists():
    xsec_surf = vsp.GetXSecSurf(wing_id, 0)
    xsec = vsp.GetXSec(xsec_surf, 0)
    vsp.ChangeXSecShape(xsec_surf, 0, vsp.XS_FILE_AIRFOIL)
    vsp.ReadFileAirfoil(xsec, str(airfoil_path))

vsp.Update()
vsp.WriteVSPFile(vsp_path)
vsp.ExportFile(export_path, vsp.SET_ALL, vsp.EXPORT_STL)
'''


def _placeholder_ascii_stl() -> str:
    return """solid mock_wing
  facet normal 0 0 1
    outer loop
      vertex 0 0 0
      vertex 1 0 0
      vertex 0 1 0
    endloop
  endfacet
endsolid mock_wing
"""
