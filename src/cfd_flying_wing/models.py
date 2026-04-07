from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar


DESIGN_VARIABLES = (
    "span_m",
    "wing_area_m2",
    "taper_ratio",
    "sweep_deg",
    "twist_deg",
    "cg_mac",
)


class ConfigurationError(RuntimeError):
    """Raised when the configured external workflow cannot be run."""


@dataclass(frozen=True)
class ParameterBounds:
    low: float
    high: float

    def __post_init__(self) -> None:
        if self.high <= self.low:
            raise ValueError(f"Invalid bounds: high={self.high} must be greater than low={self.low}")

    def contains(self, value: float) -> bool:
        return self.low <= value <= self.high

    def normalize(self, value: float) -> float:
        return (value - self.low) / (self.high - self.low)

    def denormalize(self, unit_value: float) -> float:
        return self.low + unit_value * (self.high - self.low)


@dataclass(frozen=True)
class Design:
    span_m: float
    wing_area_m2: float
    taper_ratio: float
    sweep_deg: float
    twist_deg: float
    cg_mac: float

    variables: ClassVar[tuple[str, ...]] = DESIGN_VARIABLES

    @classmethod
    def from_mapping(cls, values: dict[str, Any]) -> "Design":
        missing = [name for name in cls.variables if name not in values]
        if missing:
            raise ValueError(f"Missing design variables: {', '.join(missing)}")
        return cls(**{name: float(values[name]) for name in cls.variables})

    def as_dict(self) -> dict[str, float]:
        return {name: float(getattr(self, name)) for name in self.variables}

    @property
    def mean_chord_m(self) -> float:
        return self.wing_area_m2 / self.span_m

    @property
    def aspect_ratio(self) -> float:
        return self.span_m * self.span_m / self.wing_area_m2

    @property
    def root_chord_m(self) -> float:
        return 2.0 * self.wing_area_m2 / (self.span_m * (1.0 + self.taper_ratio))

    @property
    def tip_chord_m(self) -> float:
        return self.root_chord_m * self.taper_ratio


@dataclass(frozen=True)
class DesignBounds:
    values: dict[str, ParameterBounds]

    @classmethod
    def defaults(cls) -> "DesignBounds":
        return cls(
            {
                "span_m": ParameterBounds(0.24, 0.40),
                "wing_area_m2": ParameterBounds(0.035, 0.075),
                "taper_ratio": ParameterBounds(0.35, 1.0),
                "sweep_deg": ParameterBounds(0.0, 35.0),
                "twist_deg": ParameterBounds(-8.0, 3.0),
                "cg_mac": ParameterBounds(0.15, 0.35),
            }
        )

    @property
    def names(self) -> tuple[str, ...]:
        return DESIGN_VARIABLES

    def validate(self, design: Design) -> None:
        errors: list[str] = []
        for name in self.names:
            bound = self.values[name]
            value = getattr(design, name)
            if not bound.contains(value):
                errors.append(f"{name}={value:g} outside [{bound.low:g}, {bound.high:g}]")
        if errors:
            raise ValueError("; ".join(errors))

    def denormalize(self, unit_values: list[float] | tuple[float, ...]) -> Design:
        if len(unit_values) != len(self.names):
            raise ValueError(f"Expected {len(self.names)} unit values, got {len(unit_values)}")
        values = {
            name: self.values[name].denormalize(float(unit_values[index]))
            for index, name in enumerate(self.names)
        }
        return Design.from_mapping(values)

    def normalize(self, design: Design) -> list[float]:
        return [self.values[name].normalize(getattr(design, name)) for name in self.names]


@dataclass(frozen=True)
class FlightCondition:
    mass_kg: float = 0.2
    cruise_speed_mps: float = 10.0
    air_density_kg_m3: float = 1.225
    gravity_mps2: float = 9.80665

    @property
    def target_lift_n(self) -> float:
        return self.mass_kg * self.gravity_mps2

    @property
    def dynamic_pressure_pa(self) -> float:
        return 0.5 * self.air_density_kg_m3 * self.cruise_speed_mps**2

    def target_cl(self, reference_area_m2: float) -> float:
        return self.target_lift_n / (self.dynamic_pressure_pa * reference_area_m2)


@dataclass(frozen=True)
class RunSettings:
    artifacts_root: Path = Path("runs/artifacts")
    database_path: Path = Path("runs/results.sqlite3")


@dataclass(frozen=True)
class OpenVspSettings:
    openvsp_python: str = ""
    airfoil_path: Path = Path("assets/airfoils/placeholder_reflex.dat")
    export_format: str = "stl"
    geometry_filename: str = "wing.stl"


@dataclass(frozen=True)
class OpenFoamSettings:
    docker_image: str = ""
    case_template_dir: Path | None = None
    container_case_dir: str = "/case"
    mesh_commands: tuple[str, ...] = (
        "surfaceFeatureExtract",
        "blockMesh",
        "snappyHexMesh -overwrite",
    )
    solver_command: str = "simpleFoam"
    force_coefficients_file: Path = Path("postProcessing/forceCoeffs/0/coefficient.dat")


@dataclass(frozen=True)
class CfdSettings:
    aoa_min_deg: float = -2.0
    aoa_max_deg: float = 12.0
    max_root_find_cases: int = 5
    lift_tolerance_fraction: float = 0.02
    trim_moment_limit: float = 0.2
    unstable_cm_slope_penalty: float = 20.0
    trim_moment_penalty: float = 5.0

    def lift_tolerance_n(self, flight: FlightCondition) -> float:
        return abs(flight.target_lift_n) * self.lift_tolerance_fraction


@dataclass(frozen=True)
class OptimizerSettings:
    initial_samples: int = 24
    random_seed: int = 42
    candidate_pool_size: int = 2048


@dataclass(frozen=True)
class ProjectConfig:
    run: RunSettings = field(default_factory=RunSettings)
    flight: FlightCondition = field(default_factory=FlightCondition)
    bounds: DesignBounds = field(default_factory=DesignBounds.defaults)
    openvsp: OpenVspSettings = field(default_factory=OpenVspSettings)
    openfoam: OpenFoamSettings = field(default_factory=OpenFoamSettings)
    cfd: CfdSettings = field(default_factory=CfdSettings)
    optimizer: OptimizerSettings = field(default_factory=OptimizerSettings)


@dataclass(frozen=True)
class GeometryArtifact:
    geometry_path: Path
    metadata_path: Path
    script_path: Path | None = None


@dataclass(frozen=True)
class AerodynamicResult:
    aoa_deg: float
    cl: float
    cd: float
    cm: float | None
    lift_n: float
    drag_n: float
    raw_path: Path | None = None

    @property
    def lift_to_drag(self) -> float:
        if self.drag_n <= 0:
            return float("-inf")
        return self.lift_n / self.drag_n


@dataclass(frozen=True)
class EvaluationResult:
    design: Design
    status: str
    score: float | None
    target_aoa_deg: float | None
    aero: AerodynamicResult | None
    cfd_cases: int
    artifact_dir: Path
    failure_reason: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status == "success"
