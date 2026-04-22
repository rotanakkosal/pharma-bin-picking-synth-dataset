#!/usr/bin/env bash
# Batch-generate N synthetic scenes. Each scene runs in its own Blender process
# (BlenderProc requirement — one scene per `blenderproc run` invocation).
#
# Usage:   bash scripts/run_batch.sh <num_scenes> [start_id]
# Example: bash scripts/run_batch.sh 10        -> scenes 1..10
# Example: bash scripts/run_batch.sh 10 100    -> scenes 100..109

set -euo pipefail

N=${1:?"usage: run_batch.sh <num_scenes> [start_id]"}
START=${2:-1}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# shellcheck disable=SC1091
source .venv_synth/bin/activate

echo "Generating $N scenes starting at id=$START"
echo "Output: $PROJECT_DIR/output/"
echo "----------------------------------------"

for i in $(seq 0 $((N - 1))); do
    SCENE_ID=$((START + i))
    printf "[%2d/%2d] scene_%06d ... " "$((i+1))" "$N" "$SCENE_ID"
    t0=$SECONDS
    if blenderproc run scripts/generate_scene.py \
        --config scripts/config.yaml \
        --scene-id "$SCENE_ID" \
        > "output/scene_${SCENE_ID}.log" 2>&1; then
        dt=$((SECONDS - t0))
        n_inst=$(python3 -c "import json; d=json.load(open('output/scene_$(printf %06d $SCENE_ID)/scene_gt.json')); print(len(d['instances']))" 2>/dev/null || echo "?")
        echo "ok (${dt}s, ${n_inst} instances)"
    else
        echo "FAILED — see output/scene_${SCENE_ID}.log"
    fi
done

echo "----------------------------------------"
echo "Done. Scenes in $PROJECT_DIR/output/"
