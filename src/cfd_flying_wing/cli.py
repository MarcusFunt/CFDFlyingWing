from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from .config import load_config
from .evaluator import DesignEvaluator
from .geometry import MockGeometryRunner, OpenVspGeometryRunner
from .models import Design, EvaluationResult
from .openfoam import AnalyticOpenFoamRunner, DockerOpenFoamRunner
from .optimizer import BayesianOptimizer, DifferentialEvolutionSearch, RandomSearch
from .sampling import samples
from .storage import ResultStore


def main(argv: Iterable[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return args.func(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cfd-flying-wing")
    parser.add_argument("--config", default="configs/default.toml", help="Path to project TOML config.")
    subparsers = parser.add_subparsers(required=True)

    sample_parser = subparsers.add_parser("sample", help="Generate initial design samples.")
    sample_parser.add_argument("--n", type=int, default=24)
    sample_parser.add_argument("--method", choices=["sobol", "random", "latin-hypercube", "lhs"], default="sobol")
    sample_parser.add_argument("--out", default="", help="Optional JSONL output path.")
    sample_parser.set_defaults(func=_sample_command)

    evaluate_parser = subparsers.add_parser("evaluate", help="Evaluate one design.")
    evaluate_parser.add_argument("--design-json", default="", help="Design JSON object.")
    evaluate_parser.add_argument("--design-file", default="", help="Path to JSON design file.")
    evaluate_parser.add_argument("--mock", action="store_true", help="Use mock geometry and analytic CFD runner.")
    evaluate_parser.set_defaults(func=_evaluate_command)

    optimize_parser = subparsers.add_parser("optimize", help="Run an optimization loop.")
    optimize_parser.add_argument("--budget", type=int, default=8, help="Number of design evaluations.")
    optimize_parser.add_argument("--mock", action="store_true", help="Use mock geometry and analytic CFD runner.")
    optimize_parser.set_defaults(func=_optimize_command)

    compare_parser = subparsers.add_parser("compare-baselines", help="Compare BO, random, and DE under one budget.")
    compare_parser.add_argument("--budget", type=int, default=8)
    compare_parser.add_argument("--mock", action="store_true", help="Use mock geometry and analytic CFD runner.")
    compare_parser.set_defaults(func=_compare_command)
    return parser


def _sample_command(args) -> int:
    config = load_config(args.config)
    generated = samples(args.method, config.bounds, args.n, config.optimizer.random_seed)
    lines = [json.dumps(design.as_dict(), sort_keys=True) for design in generated]
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        print("\n".join(lines))
    return 0


def _evaluate_command(args) -> int:
    config = load_config(args.config)
    design = _read_design(args.design_json, args.design_file)
    evaluator = _make_evaluator(config, args.mock)
    result = evaluator.evaluate(design)
    print(json.dumps(_result_summary(result), indent=2, sort_keys=True))
    return 0 if result.succeeded else 2


def _optimize_command(args) -> int:
    config = load_config(args.config)
    evaluator = _make_evaluator(config, args.mock)
    optimizer = BayesianOptimizer(config.bounds, config.optimizer, allow_fallback=True)
    print(f"optimizer_backend={optimizer.backend_name}")
    best: EvaluationResult | None = None
    for index in range(args.budget):
        design = optimizer.ask()
        result = evaluator.evaluate(design)
        score = result.score if result.score is not None else -1e9
        optimizer.tell(design, score)
        if result.succeeded and (best is None or result.score > (best.score or float("-inf"))):
            best = result
        print(json.dumps({"iteration": index + 1, **_result_summary(result)}, sort_keys=True))
    return 0 if best else 2


def _compare_command(args) -> int:
    config = load_config(args.config)
    strategies = {
        "bayesian": BayesianOptimizer(config.bounds, config.optimizer, allow_fallback=True),
        "random": RandomSearch(config.bounds, config.optimizer.random_seed),
        "differential_evolution": DifferentialEvolutionSearch(config.bounds, seed=config.optimizer.random_seed),
    }
    summaries = {}
    for name, optimizer in strategies.items():
        evaluator = _make_evaluator(config, args.mock, store_suffix=name)
        best_score = float("-inf")
        for _ in range(args.budget):
            design = optimizer.ask()
            result = evaluator.evaluate(design)
            score = result.score if result.score is not None else -1e9
            optimizer.tell(design, score)
            best_score = max(best_score, score)
        summaries[name] = best_score
    print(json.dumps(summaries, indent=2, sort_keys=True))
    return 0


def _read_design(design_json: str, design_file: str) -> Design:
    if bool(design_json) == bool(design_file):
        raise SystemExit("Provide exactly one of --design-json or --design-file.")
    raw = json.loads(design_json) if design_json else json.loads(Path(design_file).read_text(encoding="utf-8-sig"))
    return Design.from_mapping(raw)


def _make_evaluator(config, mock: bool, store_suffix: str | None = None) -> DesignEvaluator:
    database_path = config.run.database_path
    if store_suffix:
        database_path = database_path.with_name(f"{database_path.stem}_{store_suffix}{database_path.suffix}")
        config = replace(
            config,
            run=replace(
                config.run,
                artifacts_root=config.run.artifacts_root / store_suffix,
                database_path=database_path,
            ),
        )
    if mock:
        geometry_runner = MockGeometryRunner(config.openvsp.geometry_filename)
        cfd_runner = AnalyticOpenFoamRunner()
    else:
        geometry_runner = OpenVspGeometryRunner(config.openvsp)
        cfd_runner = DockerOpenFoamRunner(config.openfoam)
    return DesignEvaluator(config, geometry_runner, cfd_runner, ResultStore(database_path))


def _result_summary(result: EvaluationResult) -> dict:
    return {
        "status": result.status,
        "score": result.score,
        "target_aoa_deg": result.target_aoa_deg,
        "cfd_cases": result.cfd_cases,
        "artifact_dir": str(result.artifact_dir),
        "failure_reason": result.failure_reason,
        "design": result.design.as_dict(),
        "aero": {
            "cl": result.aero.cl,
            "cd": result.aero.cd,
            "cm": result.aero.cm,
            "lift_n": result.aero.lift_n,
            "drag_n": result.aero.drag_n,
            "lift_to_drag": result.aero.lift_to_drag,
        }
        if result.aero
        else None,
    }


if __name__ == "__main__":
    raise SystemExit(main())
