from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .geometry import GeometryRunner
from .models import AerodynamicResult, ConfigurationError, Design, EvaluationResult, ProjectConfig
from .openfoam import CfdRunner
from .storage import ResultStore


class DesignEvaluator:
    def __init__(
        self,
        config: ProjectConfig,
        geometry_runner: GeometryRunner,
        cfd_runner: CfdRunner,
        store: ResultStore | None = None,
    ) -> None:
        self.config = config
        self.geometry_runner = geometry_runner
        self.cfd_runner = cfd_runner
        self.store = store

    def evaluate(self, design: Design) -> EvaluationResult:
        design_uid = design_hash(design)
        artifact_dir = self.config.run.artifacts_root / design_uid
        artifact_dir.mkdir(parents=True, exist_ok=True)

        try:
            self.config.bounds.validate(design)
            geometry = self.geometry_runner.generate(design, artifact_dir)
            result = self._evaluate_with_aoa_root_find(design, geometry, artifact_dir)
        except Exception as exc:
            result = EvaluationResult(
                design=design,
                status="failed",
                score=None,
                target_aoa_deg=None,
                aero=None,
                cfd_cases=0,
                artifact_dir=artifact_dir,
                failure_reason=str(exc),
                diagnostics={"exception_type": type(exc).__name__},
            )

        (artifact_dir / "evaluation_result.json").write_text(
            json.dumps(_result_to_json(result), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        if self.store:
            self.store.add_evaluation(design_uid, result)
        return result

    def _evaluate_with_aoa_root_find(self, design: Design, geometry, artifact_dir: Path) -> EvaluationResult:
        target_lift = self.config.flight.target_lift_n
        tolerance = self.config.cfd.lift_tolerance_n(self.config.flight)
        max_cases = max(2, self.config.cfd.max_root_find_cases)
        cases: list[AerodynamicResult] = []

        for aoa in (self.config.cfd.aoa_min_deg, self.config.cfd.aoa_max_deg):
            cases.append(self._run_case(design, geometry, artifact_dir, aoa, len(cases) + 1))
            if abs(cases[-1].lift_n - target_lift) <= tolerance:
                return self._success(design, cases[-1], cases, artifact_dir, converged=True)

        low, high = _bracket(target_lift, cases)
        if low is None or high is None:
            return EvaluationResult(
                design=design,
                status="failed",
                score=None,
                target_aoa_deg=None,
                aero=None,
                cfd_cases=len(cases),
                artifact_dir=artifact_dir,
                failure_reason="Target lift was not bracketed by configured AoA range.",
                diagnostics={
                    "target_lift_n": target_lift,
                    "sampled_cases": [_aero_to_json(case) for case in cases],
                },
            )

        while len(cases) < max_cases:
            next_aoa = _interpolate_aoa_for_lift(low, high, target_lift)
            if any(abs(case.aoa_deg - next_aoa) < 1e-6 for case in cases):
                break
            candidate = self._run_case(design, geometry, artifact_dir, next_aoa, len(cases) + 1)
            cases.append(candidate)
            if abs(candidate.lift_n - target_lift) <= tolerance:
                return self._success(design, candidate, cases, artifact_dir, converged=True)
            low, high = _bracket(target_lift, cases)
            if low is None or high is None:
                break

        interpolated = _interpolate_aero_at_lift(low, high, target_lift)
        return self._success(design, interpolated, cases, artifact_dir, converged=False)

    def _run_case(self, design: Design, geometry, artifact_dir: Path, aoa_deg: float, case_number: int) -> AerodynamicResult:
        case_dir = artifact_dir / "cfd" / f"case_{case_number:02d}_aoa_{aoa_deg:+06.3f}"
        return self.cfd_runner.run_case(design, geometry, aoa_deg, case_dir, self.config.flight)

    def _success(
        self,
        design: Design,
        aero: AerodynamicResult,
        cases: list[AerodynamicResult],
        artifact_dir: Path,
        converged: bool,
    ) -> EvaluationResult:
        if not math.isfinite(aero.lift_to_drag) or aero.cd <= 0.0:
            raise ConfigurationError("Computed non-finite L/D or non-positive drag coefficient.")
        penalty, penalty_terms = _stability_penalty(aero, cases, self.config)
        return EvaluationResult(
            design=design,
            status="success",
            score=aero.lift_to_drag - penalty,
            target_aoa_deg=aero.aoa_deg,
            aero=aero,
            cfd_cases=len(cases),
            artifact_dir=artifact_dir,
            diagnostics={
                "root_find_converged": converged,
                "target_lift_n": self.config.flight.target_lift_n,
                "sampled_cases": [_aero_to_json(case) for case in cases],
                "penalty": penalty,
                "penalty_terms": penalty_terms,
            },
        )


def design_hash(design: Design) -> str:
    payload = json.dumps(design.as_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _bracket(target_lift: float, cases: list[AerodynamicResult]) -> tuple[AerodynamicResult | None, AerodynamicResult | None]:
    below = [case for case in cases if case.lift_n <= target_lift]
    above = [case for case in cases if case.lift_n >= target_lift]
    if not below or not above:
        return None, None
    return max(below, key=lambda case: case.lift_n), min(above, key=lambda case: case.lift_n)


def _interpolate_aoa_for_lift(low: AerodynamicResult, high: AerodynamicResult, target_lift: float) -> float:
    if high.lift_n == low.lift_n:
        return 0.5 * (low.aoa_deg + high.aoa_deg)
    fraction = (target_lift - low.lift_n) / (high.lift_n - low.lift_n)
    return low.aoa_deg + fraction * (high.aoa_deg - low.aoa_deg)


def _interpolate_aero_at_lift(
    low: AerodynamicResult | None,
    high: AerodynamicResult | None,
    target_lift: float,
) -> AerodynamicResult:
    if low is None or high is None:
        raise ConfigurationError("Cannot interpolate target-lift aerodynamic result without a valid lift bracket.")
    fraction = 0.5 if high.lift_n == low.lift_n else (target_lift - low.lift_n) / (high.lift_n - low.lift_n)

    def interp(name: str) -> float:
        return float(getattr(low, name) + fraction * (getattr(high, name) - getattr(low, name)))

    cm = None
    if low.cm is not None and high.cm is not None:
        cm = float(low.cm + fraction * (high.cm - low.cm))
    return AerodynamicResult(
        aoa_deg=interp("aoa_deg"),
        cl=interp("cl"),
        cd=max(1e-9, interp("cd")),
        cm=cm,
        lift_n=target_lift,
        drag_n=max(1e-9, interp("drag_n")),
        raw_path=None,
    )


def _stability_penalty(
    aero: AerodynamicResult,
    cases: list[AerodynamicResult],
    config: ProjectConfig,
) -> tuple[float, dict[str, float]]:
    penalty = 0.0
    terms: dict[str, float] = {}
    if aero.cm is not None and abs(aero.cm) > config.cfd.trim_moment_limit:
        terms["trim_moment"] = config.cfd.trim_moment_penalty * (abs(aero.cm) - config.cfd.trim_moment_limit)
        penalty += terms["trim_moment"]

    cm_cases = [case for case in sorted(cases, key=lambda item: item.aoa_deg) if case.cm is not None]
    if len(cm_cases) >= 2:
        first, last = cm_cases[0], cm_cases[-1]
        if last.aoa_deg != first.aoa_deg:
            slope = (float(last.cm) - float(first.cm)) / (last.aoa_deg - first.aoa_deg)
            terms["cm_slope_per_deg"] = slope
            if slope > 0.0:
                terms["unstable_cm_slope"] = config.cfd.unstable_cm_slope_penalty * slope
                penalty += terms["unstable_cm_slope"]
    return penalty, terms


def _aero_to_json(aero: AerodynamicResult) -> dict[str, float | str | None]:
    return {
        "aoa_deg": aero.aoa_deg,
        "cl": aero.cl,
        "cd": aero.cd,
        "cm": aero.cm,
        "lift_n": aero.lift_n,
        "drag_n": aero.drag_n,
        "lift_to_drag": aero.lift_to_drag,
        "raw_path": str(aero.raw_path) if aero.raw_path else None,
    }


def _result_to_json(result: EvaluationResult) -> dict:
    return {
        "design": result.design.as_dict(),
        "status": result.status,
        "score": result.score,
        "target_aoa_deg": result.target_aoa_deg,
        "aero": _aero_to_json(result.aero) if result.aero else None,
        "cfd_cases": result.cfd_cases,
        "artifact_dir": str(result.artifact_dir),
        "failure_reason": result.failure_reason,
        "diagnostics": result.diagnostics,
    }
