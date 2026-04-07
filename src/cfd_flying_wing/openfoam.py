from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

from .geometry import copy_geometry_to_case
from .models import (
    AerodynamicResult,
    ConfigurationError,
    Design,
    FlightCondition,
    GeometryArtifact,
    OpenFoamSettings,
)


class CfdRunner:
    def run_case(
        self,
        design: Design,
        geometry: GeometryArtifact,
        aoa_deg: float,
        case_dir: Path,
        flight: FlightCondition,
    ) -> AerodynamicResult:
        raise NotImplementedError


class DockerOpenFoamRunner(CfdRunner):
    def __init__(self, settings: OpenFoamSettings) -> None:
        self.settings = settings

    def run_case(
        self,
        design: Design,
        geometry: GeometryArtifact,
        aoa_deg: float,
        case_dir: Path,
        flight: FlightCondition,
    ) -> AerodynamicResult:
        if not self.settings.docker_image:
            raise ConfigurationError(
                "OpenFOAM Docker image is not configured. Set openfoam.docker_image or run with --mock."
            )
        if self.settings.case_template_dir is None:
            raise ConfigurationError(
                "OpenFOAM case template is not configured. Set openfoam.case_template_dir or run with --mock."
            )
        if not self.settings.case_template_dir.exists():
            raise ConfigurationError(f"OpenFOAM case template does not exist: {self.settings.case_template_dir}")

        if case_dir.exists():
            shutil.rmtree(case_dir)
        shutil.copytree(self.settings.case_template_dir, case_dir)
        copied_geometry = copy_geometry_to_case(geometry, case_dir)
        _write_case_metadata(case_dir, design, aoa_deg, flight, copied_geometry)

        command = " && ".join([*self.settings.mesh_commands, self.settings.solver_command])
        completed = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{case_dir.resolve()}:{self.settings.container_case_dir}",
                "-w",
                self.settings.container_case_dir,
                self.settings.docker_image,
                "bash",
                "-lc",
                command,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        (case_dir / "openfoam.stdout.log").write_text(completed.stdout, encoding="utf-8")
        (case_dir / "openfoam.stderr.log").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode != 0:
            raise ConfigurationError(
                f"OpenFOAM run failed with exit code {completed.returncode}. "
                f"See {case_dir / 'openfoam.stderr.log'}"
            )

        coeff_path = case_dir / self.settings.force_coefficients_file
        return parse_force_coefficients(coeff_path, design, flight)


class AnalyticOpenFoamRunner(CfdRunner):
    """Cheap deterministic stand-in for OpenFOAM used by tests and smoke runs."""

    def run_case(
        self,
        design: Design,
        geometry: GeometryArtifact,
        aoa_deg: float,
        case_dir: Path,
        flight: FlightCondition,
    ) -> AerodynamicResult:
        case_dir.mkdir(parents=True, exist_ok=True)
        alpha_rad = math.radians(aoa_deg)
        aspect_efficiency = max(0.45, min(0.92, 0.55 + 0.08 * design.aspect_ratio))
        sweep_penalty = 1.0 - 0.0025 * design.sweep_deg
        twist_lift = -0.015 * design.twist_deg
        cl = (2.0 * math.pi * alpha_rad * aspect_efficiency + 0.18 + twist_lift) * sweep_penalty
        cd0 = 0.028 + 0.002 * abs(design.twist_deg) + 0.00025 * design.sweep_deg
        induced = cl * cl / (math.pi * max(design.aspect_ratio, 0.1) * max(aspect_efficiency, 0.1))
        cd = max(0.005, cd0 + induced)
        neutral_point = 0.24 + 0.0015 * design.sweep_deg
        cm = -0.035 - 0.45 * (design.cg_mac - neutral_point) - 0.006 * aoa_deg
        lift_n = cl * flight.dynamic_pressure_pa * design.wing_area_m2
        drag_n = cd * flight.dynamic_pressure_pa * design.wing_area_m2
        coeff_path = case_dir / "postProcessing" / "forceCoeffs" / "0" / "coefficient.dat"
        coeff_path.parent.mkdir(parents=True, exist_ok=True)
        coeff_path.write_text(
            "# Time Cd Cs Cl CmRoll CmPitch CmYaw\n"
            f"1 {cd:.8f} 0 {cl:.8f} 0 {cm:.8f} 0\n",
            encoding="utf-8",
        )
        _write_case_metadata(case_dir, design, aoa_deg, flight, geometry.geometry_path)
        return AerodynamicResult(
            aoa_deg=aoa_deg,
            cl=cl,
            cd=cd,
            cm=cm,
            lift_n=lift_n,
            drag_n=drag_n,
            raw_path=coeff_path,
        )


def parse_force_coefficients(path: Path, design: Design, flight: FlightCondition) -> AerodynamicResult:
    if not path.exists():
        raise ConfigurationError(f"OpenFOAM force coefficient file was not found: {path}")
    last_values: list[float] | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            values = [float(part) for part in stripped.split()]
        except ValueError:
            continue
        if len(values) >= 6:
            last_values = values
    if last_values is None:
        raise ConfigurationError(f"No numeric force coefficient rows found in {path}")

    cd = last_values[1]
    cl = last_values[3]
    cm = last_values[5]
    lift_n = cl * flight.dynamic_pressure_pa * design.wing_area_m2
    drag_n = cd * flight.dynamic_pressure_pa * design.wing_area_m2
    return AerodynamicResult(
        aoa_deg=_read_aoa_from_case_metadata(path),
        cl=cl,
        cd=cd,
        cm=cm,
        lift_n=lift_n,
        drag_n=drag_n,
        raw_path=path,
    )


def _write_case_metadata(
    case_dir: Path,
    design: Design,
    aoa_deg: float,
    flight: FlightCondition,
    geometry_path: Path,
) -> None:
    metadata = {
        "aoa_deg": aoa_deg,
        "design": design.as_dict(),
        "flight": {
            "mass_kg": flight.mass_kg,
            "cruise_speed_mps": flight.cruise_speed_mps,
            "air_density_kg_m3": flight.air_density_kg_m3,
            "target_lift_n": flight.target_lift_n,
        },
        "geometry_path": str(geometry_path),
    }
    (case_dir / "case_metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")


def _read_aoa_from_case_metadata(coeff_path: Path) -> float:
    for parent in coeff_path.parents:
        candidate = parent / "case_metadata.json"
        if candidate.exists():
            return float(json.loads(candidate.read_text(encoding="utf-8"))["aoa_deg"])
    return float("nan")
