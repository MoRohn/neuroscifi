#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export OPENBLAS_NUM_THREADS=1
mkdir -p reproduced
python3 scripts/get_inputs.py inputs
python3 scripts/atlas_check.py inputs/receptors.csv inputs/labels.txt reproduced/atlas.json
python3 scripts/atlas_sensitivity.py inputs reproduced/atlas_sensitivity.json
python3 scripts/placement.py inputs reproduced/placement.json
python3 scripts/cue_timing.py inputs reproduced/timing.json
python3 scripts/human_forecast.py inputs reproduced/forecast.json
python3 scripts/association_test.py reproduced/association_final.json --test-start 5000
python3 scripts/context_uncertainty.py reproduced/context.json
python3 scripts/intervention_test.py reproduced/intervention.json
python3 scripts/curiosity_control.py reproduced/curiosity.json
python3 scripts/precision_test.py reproduced/precision.json
