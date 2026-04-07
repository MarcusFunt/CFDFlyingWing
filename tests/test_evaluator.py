from pathlib import Path

from cfd_flying_wing.evaluator import DesignEvaluator
from cfd_flying_wing.geometry import MockGeometryRunner
from cfd_flying_wing.models import Design, ProjectConfig, RunSettings
from cfd_flying_wing.openfoam import AnalyticOpenFoamRunner
from cfd_flying_wing.storage import ResultStore


def test_mock_evaluator_root_finds_and_stores_result(tmp_path: Path) -> None:
    config = ProjectConfig(
        run=RunSettings(
            artifacts_root=tmp_path / "artifacts",
            database_path=tmp_path / "results.sqlite3",
        )
    )
    store = ResultStore(config.run.database_path)
    evaluator = DesignEvaluator(config, MockGeometryRunner(), AnalyticOpenFoamRunner(), store)
    design = Design(span_m=0.3, wing_area_m2=0.05, taper_ratio=0.7, sweep_deg=15, twist_deg=-3, cg_mac=0.22)

    result = evaluator.evaluate(design)

    assert result.succeeded
    assert result.score is not None
    assert result.aero is not None
    assert result.cfd_cases <= config.cfd.max_root_find_cases
    assert (result.artifact_dir / "evaluation_result.json").exists()
    rows = store.all_evaluations()
    assert len(rows) == 1
    assert rows[0]["status"] == "success"
