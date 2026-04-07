from cfd_flying_wing.models import DesignBounds, OptimizerSettings
from cfd_flying_wing.optimizer import BayesianOptimizer
from cfd_flying_wing.sampling import latin_hypercube_samples, random_samples


def test_sampling_respects_bounds() -> None:
    bounds = DesignBounds.defaults()
    for design in random_samples(bounds, 5, seed=1) + latin_hypercube_samples(bounds, 5, seed=1):
        bounds.validate(design)


def test_bayesian_optimizer_fallback_ask_tell() -> None:
    bounds = DesignBounds.defaults()
    optimizer = BayesianOptimizer(
        bounds,
        OptimizerSettings(initial_samples=2, random_seed=4, candidate_pool_size=10),
        allow_fallback=True,
    )

    first = optimizer.ask()
    bounds.validate(first)
    optimizer.tell(first, 1.0)
    second = optimizer.ask()
    bounds.validate(second)
    optimizer.tell(second, 2.0)
    third = optimizer.ask()
    bounds.validate(third)
