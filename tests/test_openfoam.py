from pathlib import Path

from cfd_flying_wing.models import Design, FlightCondition
from cfd_flying_wing.openfoam import parse_force_coefficients


def test_parse_force_coefficients_reads_last_numeric_row(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    coeff_path = case_dir / "postProcessing" / "forceCoeffs" / "0" / "coefficient.dat"
    coeff_path.parent.mkdir(parents=True)
    (case_dir / "case_metadata.json").write_text('{"aoa_deg": 4.0}', encoding="utf-8")
    coeff_path.write_text(
        "# Time Cd Cs Cl CmRoll CmPitch CmYaw\n"
        "0.5 0.05 0 0.6 0 -0.02 0\n"
        "1.0 0.04 0 0.7 0 -0.03 0\n",
        encoding="utf-8",
    )

    result = parse_force_coefficients(
        coeff_path,
        Design(span_m=0.3, wing_area_m2=0.05, taper_ratio=0.7, sweep_deg=15, twist_deg=-3, cg_mac=0.22),
        FlightCondition(),
    )

    assert result.aoa_deg == 4.0
    assert result.cd == 0.04
    assert result.cl == 0.7
    assert result.cm == -0.03
    assert result.lift_n > result.drag_n
