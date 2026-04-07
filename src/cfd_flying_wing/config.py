from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any
import tomllib

from .models import (
    CfdSettings,
    DesignBounds,
    FlightCondition,
    OpenFoamSettings,
    OpenVspSettings,
    OptimizerSettings,
    ParameterBounds,
    ProjectConfig,
    RunSettings,
)


def load_config(path: str | Path | None = None) -> ProjectConfig:
    config_path = Path(path) if path else Path("configs/default.toml")
    if config_path.exists():
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
        return config_from_mapping(raw, base_dir=config_path.parent)
    if path:
        raise FileNotFoundError(config_path)
    return ProjectConfig()


def config_from_mapping(raw: dict[str, Any], base_dir: Path | None = None) -> ProjectConfig:
    base_dir = base_dir or Path(".")
    default = ProjectConfig()
    return ProjectConfig(
        run=_dataclass_from_mapping(
            RunSettings,
            raw.get("run", {}),
            default.run,
            path_fields={"artifacts_root", "database_path"},
            base_dir=Path.cwd(),
        ),
        flight=_dataclass_from_mapping(FlightCondition, raw.get("flight", {}), default.flight),
        bounds=_bounds_from_mapping(raw.get("bounds", {}), default.bounds),
        openvsp=_dataclass_from_mapping(
            OpenVspSettings,
            raw.get("openvsp", {}),
            default.openvsp,
            path_fields={"airfoil_path"},
            base_dir=base_dir,
        ),
        openfoam=_openfoam_from_mapping(raw.get("openfoam", {}), default.openfoam, base_dir),
        cfd=_dataclass_from_mapping(CfdSettings, raw.get("cfd", {}), default.cfd),
        optimizer=_dataclass_from_mapping(OptimizerSettings, raw.get("optimizer", {}), default.optimizer),
    )


def _dataclass_from_mapping(
    cls: type,
    raw: dict[str, Any],
    default: Any,
    path_fields: set[str] | None = None,
    base_dir: Path | None = None,
) -> Any:
    path_fields = path_fields or set()
    base_dir = base_dir or Path(".")
    values: dict[str, Any] = {}
    for item in fields(cls):
        value = raw.get(item.name, getattr(default, item.name))
        if item.name in path_fields:
            value = _resolve_path(value, base_dir)
        values[item.name] = value
    return cls(**values)


def _bounds_from_mapping(raw: dict[str, Any], default: DesignBounds) -> DesignBounds:
    values: dict[str, ParameterBounds] = {}
    for name in default.names:
        raw_bound = raw.get(name, {})
        default_bound = default.values[name]
        values[name] = ParameterBounds(
            low=float(raw_bound.get("low", default_bound.low)),
            high=float(raw_bound.get("high", default_bound.high)),
        )
    return DesignBounds(values)


def _openfoam_from_mapping(raw: dict[str, Any], default: OpenFoamSettings, base_dir: Path) -> OpenFoamSettings:
    case_template_raw = raw.get("case_template_dir", default.case_template_dir)
    return OpenFoamSettings(
        docker_image=str(raw.get("docker_image", default.docker_image)),
        case_template_dir=_resolve_optional_path(case_template_raw, base_dir),
        container_case_dir=str(raw.get("container_case_dir", default.container_case_dir)),
        mesh_commands=tuple(raw.get("mesh_commands", default.mesh_commands)),
        solver_command=str(raw.get("solver_command", default.solver_command)),
        force_coefficients_file=Path(raw.get("force_coefficients_file", default.force_coefficients_file)),
    )


def _resolve_optional_path(value: Any, base_dir: Path) -> Path | None:
    if value in (None, ""):
        return None
    return _resolve_path(value, base_dir)


def _resolve_path(value: Any, base_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    repo_relative = Path.cwd() / path
    if repo_relative.exists():
        return repo_relative
    return (base_dir / path).resolve()
