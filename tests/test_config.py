from pathlib import Path

from cfd_flying_wing.config import load_config


def test_default_config_loads() -> None:
    config = load_config("configs/default.toml")

    assert config.flight.mass_kg == 0.2
    assert config.flight.cruise_speed_mps == 10.0
    assert config.bounds.values["span_m"].low == 0.24
    assert config.openvsp.airfoil_path == Path("assets/airfoils/placeholder_reflex.dat").resolve()
