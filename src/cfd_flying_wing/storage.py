from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import EvaluationResult


SCHEMA = """
CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    design_uid TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    score REAL,
    target_aoa_deg REAL,
    cl REAL,
    cd REAL,
    cm REAL,
    lift_n REAL,
    drag_n REAL,
    lift_to_drag REAL,
    cfd_cases INTEGER NOT NULL,
    artifact_dir TEXT NOT NULL,
    failure_reason TEXT,
    design_json TEXT NOT NULL,
    diagnostics_json TEXT NOT NULL
);
"""


class ResultStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(SCHEMA)
            connection.commit()

    def add_evaluation(self, design_uid: str, result: EvaluationResult) -> None:
        aero = result.aero
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO evaluations (
                    design_uid, created_at, status, score, target_aoa_deg, cl, cd, cm,
                    lift_n, drag_n, lift_to_drag, cfd_cases, artifact_dir, failure_reason,
                    design_json, diagnostics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    design_uid,
                    datetime.now(timezone.utc).isoformat(),
                    result.status,
                    result.score,
                    result.target_aoa_deg,
                    aero.cl if aero else None,
                    aero.cd if aero else None,
                    aero.cm if aero else None,
                    aero.lift_n if aero else None,
                    aero.drag_n if aero else None,
                    aero.lift_to_drag if aero else None,
                    result.cfd_cases,
                    str(result.artifact_dir),
                    result.failure_reason,
                    json.dumps(result.design.as_dict(), sort_keys=True),
                    json.dumps(_jsonable(result.diagnostics), sort_keys=True),
                ),
            )
            connection.commit()

    def all_evaluations(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT * FROM evaluations ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def successful_observations(self) -> list[tuple[dict[str, float], float]]:
        observations: list[tuple[dict[str, float], float]] = []
        for row in self.all_evaluations():
            if row["status"] != "success" or row["score"] is None:
                continue
            observations.append((json.loads(row["design_json"]), float(row["score"])))
        return observations


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
