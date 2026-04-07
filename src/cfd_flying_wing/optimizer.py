from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .models import Design, DesignBounds, OptimizerSettings
from .sampling import random_samples, sobol_samples


@dataclass(frozen=True)
class Observation:
    design: Design
    score: float


class AskTellOptimizer:
    def ask(self) -> Design:
        raise NotImplementedError

    def tell(self, design: Design, score: float) -> None:
        raise NotImplementedError


class RandomSearch(AskTellOptimizer):
    def __init__(self, bounds: DesignBounds, seed: int | None = None) -> None:
        self.bounds = bounds
        self.rng = random.Random(seed)
        self.observations: list[Observation] = []

    def ask(self) -> Design:
        return self.bounds.denormalize([self.rng.random() for _ in self.bounds.names])

    def tell(self, design: Design, score: float) -> None:
        self.observations.append(Observation(design, score))


class DifferentialEvolutionSearch(AskTellOptimizer):
    def __init__(self, bounds: DesignBounds, population_size: int = 12, seed: int | None = None) -> None:
        self.bounds = bounds
        self.rng = random.Random(seed)
        self.population = random_samples(bounds, population_size, seed)
        self.observations: list[Observation] = []

    def ask(self) -> Design:
        if len(self.observations) < len(self.population):
            return self.population[len(self.observations)]
        ranked = sorted(self.observations, key=lambda obs: obs.score, reverse=True)
        base = ranked[0].design
        a, b = self.rng.sample(ranked[: min(len(ranked), 6)], 2)
        unit_base = self.bounds.normalize(base)
        unit_a = self.bounds.normalize(a.design)
        unit_b = self.bounds.normalize(b.design)
        mutant = [
            min(1.0, max(0.0, unit_base[index] + 0.7 * (unit_a[index] - unit_b[index])))
            for index in range(len(unit_base))
        ]
        return self.bounds.denormalize(mutant)

    def tell(self, design: Design, score: float) -> None:
        self.observations.append(Observation(design, score))


class BayesianOptimizer(AskTellOptimizer):
    def __init__(
        self,
        bounds: DesignBounds,
        settings: OptimizerSettings,
        allow_fallback: bool = True,
    ) -> None:
        self.bounds = bounds
        self.settings = settings
        self.observations: list[Observation] = []
        self._fallback: _FallbackSurrogateOptimizer | None = None
        try:
            from skopt import Optimizer as SkoptOptimizer  # type: ignore
            from skopt.space import Real  # type: ignore
        except Exception:
            if not allow_fallback:
                raise
            self._skopt = None
            self._fallback = _FallbackSurrogateOptimizer(bounds, settings)
        else:
            dimensions = [
                Real(bounds.values[name].low, bounds.values[name].high, name=name)
                for name in bounds.names
            ]
            self._skopt = SkoptOptimizer(
                dimensions=dimensions,
                base_estimator="GP",
                acq_func="EI",
                random_state=settings.random_seed,
                n_initial_points=settings.initial_samples,
            )

    @property
    def backend_name(self) -> str:
        return "fallback-surrogate" if self._fallback is not None else "scikit-optimize-gp"

    def ask(self) -> Design:
        if self._fallback is not None:
            return self._fallback.ask()
        vector = self._skopt.ask()
        return Design.from_mapping({name: float(vector[index]) for index, name in enumerate(self.bounds.names)})

    def tell(self, design: Design, score: float) -> None:
        self.observations.append(Observation(design, score))
        if self._fallback is not None:
            self._fallback.tell(design, score)
            return
        vector = [getattr(design, name) for name in self.bounds.names]
        self._skopt.tell(vector, -score)


class _FallbackSurrogateOptimizer(AskTellOptimizer):
    """Small dependency-free surrogate search used only when skopt is unavailable."""

    def __init__(self, bounds: DesignBounds, settings: OptimizerSettings) -> None:
        self.bounds = bounds
        self.settings = settings
        self.rng = random.Random(settings.random_seed)
        self.initial = sobol_samples(bounds, settings.initial_samples, settings.random_seed)
        self.observations: list[Observation] = []

    def ask(self) -> Design:
        if len(self.observations) < len(self.initial):
            return self.initial[len(self.observations)]
        pool = random_samples(self.bounds, self.settings.candidate_pool_size, self.rng.randrange(10**9))
        return max(pool, key=self._acquisition)

    def tell(self, design: Design, score: float) -> None:
        self.observations.append(Observation(design, score))

    def _acquisition(self, design: Design) -> float:
        if not self.observations:
            return 0.0
        unit = self.bounds.normalize(design)
        weighted_score = 0.0
        total_weight = 0.0
        nearest = float("inf")
        for observation in self.observations:
            other = self.bounds.normalize(observation.design)
            distance = math.sqrt(sum((unit[index] - other[index]) ** 2 for index in range(len(unit))))
            nearest = min(nearest, distance)
            weight = 1.0 / max(distance, 1e-6)
            weighted_score += weight * observation.score
            total_weight += weight
        predicted = weighted_score / total_weight
        exploration = 0.25 * nearest
        return predicted + exploration
