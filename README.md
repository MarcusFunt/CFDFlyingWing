# CFD Flying Wing

This repository scaffolds an automated design loop for a small RC flying wing:

1. Python samples a six-variable flying-wing design.
2. OpenVSP generates geometry.
3. OpenFOAM evaluates the design through Docker.
4. The evaluator root-finds angle of attack to meet the target lift.
5. Results are stored in SQLite and artifact folders.
6. Bayesian optimization chooses the next candidate.

The first prototype targets a 0.3 m span scale, 0.2 kg mass, 10 m/s cruise speed, fixed configurable airfoil, and single-objective target-lift L/D.

## Setup

Python 3.11 or 3.12 is recommended for the real OpenVSP/OpenFOAM workflow. The scaffold and tests are stdlib-first and can run on newer Python versions.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test,optimize]"
```

If `scikit-optimize` is not installed, the Bayesian optimizer class falls back to a deterministic surrogate search for smoke tests. Install the `optimize` extra for the intended Gaussian-process Bayesian optimization backend.

## Smoke Test Without External CFD Tools

Use `--mock` to exercise the full control flow without OpenVSP or OpenFOAM:

```powershell
python -m pytest
python -m cfd_flying_wing.cli sample --n 5 --out runs\samples.jsonl
python -m cfd_flying_wing.cli evaluate --mock --design-json '{"span_m":0.3,"wing_area_m2":0.05,"taper_ratio":0.7,"sweep_deg":15,"twist_deg":-3,"cg_mac":0.22}'
python -m cfd_flying_wing.cli optimize --mock --budget 8
```

The mock path creates placeholder geometry and analytic force coefficients only so orchestration, storage, and optimizer behavior can be tested before installing external tools.

## Real Tool Configuration

Configure `configs/default.toml` or pass another config file with:

- `openvsp.openvsp_python`: Python executable that can import `openvsp`.
- `openvsp.airfoil_path`: the actual low-Re/reflex airfoil `.dat` file.
- `openfoam.docker_image`: Docker image containing OpenFOAM and shell utilities.
- `openfoam.case_template_dir`: an OpenFOAM case template with `system/`, `constant/`, and `0/`.

OpenFOAM remains the truth source. The optimizer only decides which design to evaluate next.
