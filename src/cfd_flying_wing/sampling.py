from __future__ import annotations

import os
import random
import sys

from .models import Design, DesignBounds


def random_samples(bounds: DesignBounds, n: int, seed: int | None = None) -> list[Design]:
    rng = random.Random(seed)
    return [bounds.denormalize([rng.random() for _ in bounds.names]) for _ in range(n)]


def latin_hypercube_samples(bounds: DesignBounds, n: int, seed: int | None = None) -> list[Design]:
    rng = random.Random(seed)
    dimensions = len(bounds.names)
    columns: list[list[float]] = []
    for _ in range(dimensions):
        values = [(index + rng.random()) / n for index in range(n)]
        rng.shuffle(values)
        columns.append(values)
    return [
        bounds.denormalize([columns[dimension][row] for dimension in range(dimensions)])
        for row in range(n)
    ]


def sobol_samples(bounds: DesignBounds, n: int, seed: int | None = None) -> list[Design]:
    # SciPy wheels on some fresh Python releases can be present but slow or broken
    # to import. Keep the default path responsive; enable SciPy Sobol explicitly
    # or run on the recommended Python 3.11/3.12 environment.
    use_scipy = os.environ.get("CFD_FLYING_WING_USE_SCIPY_QMC") == "1" or sys.version_info < (3, 13)
    if not use_scipy:
        return latin_hypercube_samples(bounds, n, seed)

    try:
        from scipy.stats import qmc  # type: ignore
    except Exception:
        return latin_hypercube_samples(bounds, n, seed)
    else:
        sampler = qmc.Sobol(d=len(bounds.names), scramble=True, seed=seed)
        unit_samples = sampler.random(n)
        return [bounds.denormalize([float(value) for value in row]) for row in unit_samples]


def samples(method: str, bounds: DesignBounds, n: int, seed: int | None = None) -> list[Design]:
    method_normalized = method.lower()
    if method_normalized == "random":
        return random_samples(bounds, n, seed)
    if method_normalized in {"latin-hypercube", "lhs"}:
        return latin_hypercube_samples(bounds, n, seed)
    if method_normalized == "sobol":
        return sobol_samples(bounds, n, seed)
    raise ValueError(f"Unknown sampling method: {method}")
