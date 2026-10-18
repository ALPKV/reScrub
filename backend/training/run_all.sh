#!/usr/bin/env bash
# Full MM-OR run on the Vultr GPU instance: download -> datasets -> train -> end-to-end evaluation.
#
#   DATA=/mnt/data PROFILE=small bash training/run_all.sh     # from backend/
#
# Splits (MM-OR paper): train takes train the models, val takes pick checkpoints,
# test takes are only touched in step 4, so the final numbers are on unseen surgeries.
#
# Faster runs (see backend/training/README.md, "Shorter runs"):
#   PROFILE=tiny          one surgery per split
#   EVERY=6               keep every 6th labeled frame (MM-OR frames 1 s apart are near-duplicates)
#   IMGSZ=640             train at 640 px instead of 1024
#   SEG_EPOCHS=30 POSE_EPOCHS=15 REID_EPOCHS=10
#   MODELS="seg"          train only the surface/role model; keep the base pose and ReID models
#   EVAL_SPLIT=val        score on the validation surgery and skip downloading the test split
#   EVAL=0                skip step 4 (and the test download)
set -euo pipefail
DATA=${DATA:-/data}
PROFILE=${PROFILE:-small}     # tiny = 1/1/1 surgeries; small = 2 train / 1 val / 1 test; full = all main takes
DEVICE=${DEVICE:-0}
[[ "$DEVICE" == auto ]] && DEVICE=0   # the worker's .env says auto; the trainers want a GPU index (or cpu)
CAMERAS=${CAMERAS:-auto}      # camera used as the "one angled camera" (auto: simstation 0, else azure 4)
MODELS=${MODELS:-"seg pose reid"}
EVAL=${EVAL:-1}
EVAL_SPLIT=${EVAL_SPLIT:-test}
SPLITS=train,val
[[ "$EVAL" == 1 && "$EVAL_SPLIT" == test ]] && SPLITS=train,val,test
M="$DATA/MM-OR_data"
step() { echo; echo "=== $* ==="; }
has() { [[ " $MODELS " == *" $1 "* ]]; }
opt() { [[ -n "${2:-}" ]] && echo "$1 $2" || true; }   # "--flag value" only when value is set

step "1. Download MM-OR ($PROFILE: $SPLITS)"
python -m training.fetch_mmor --out "$DATA" --profile "$PROFILE" --splits "$SPLITS"

step "2. Build datasets ($MODELS)"
# shellcheck disable=SC2046
has seg  && python -m training.build_seg_dataset  --mmor "$M" --out "$DATA/rescrub_seg" $(opt --every "${EVERY:-}")
# shellcheck disable=SC2046
has reid && python -m training.build_reid_dataset --mmor "$M" --out "$DATA/rescrub_reid" $(opt --every "${EVERY:-}")
# shellcheck disable=SC2046
has pose && python -m training.build_pose_dataset --mmor "$M" --out "$DATA/rescrub_pose" --device "$DEVICE" \
  $(opt --every "${EVERY:-}")
python -m training.export_contacts --mmor "$M" --out "$DATA/rescrub_contacts.jsonl"

step "3. Train (installs the trained models in MODELS_DIR/trained)"
# shellcheck disable=SC2046
has seg  && python -m training.train seg  --data "$DATA/rescrub_seg/dataset.yaml"  --device "$DEVICE" --project "$DATA/runs" \
  $(opt --epochs "${SEG_EPOCHS:-}") $(opt --imgsz "${IMGSZ:-}")
# shellcheck disable=SC2046
has pose && python -m training.train pose --data "$DATA/rescrub_pose/dataset.yaml" --device "$DEVICE" --project "$DATA/runs" \
  $(opt --epochs "${POSE_EPOCHS:-}") $(opt --imgsz "${IMGSZ:-}")
# shellcheck disable=SC2046
has reid && python -m training.train reid --data "$DATA/rescrub_reid"              --device "$DEVICE" --project "$DATA/runs" \
  $(opt --epochs "${REID_EPOCHS:-}")

if [[ "$EVAL" != 1 ]]; then echo "Skipping step 4 (EVAL=$EVAL)."; exit 0; fi

step "4. End-to-end on held-out $EVAL_SPLIT surgeries: label, track, analyze, score timestamps"
# Separate folders per split, so a validation run never picks up (or reports) test surgeries.
VIDEOS="$DATA/eval_videos/$EVAL_SPLIT"
RESULTS="$DATA/eval_results/$EVAL_SPLIT"
python -m training.make_eval_videos --mmor "$M" --out "$VIDEOS" --split "$EVAL_SPLIT" --cameras "$CAMERAS"
export SURFACE_CONFIG=config/surfaces_mmor.yaml
has pose && export POSE_MODEL=trained/rescrub-or-pose.pt
has reid && export EMBED_MODEL=trained/rescrub-reid-cls.pt
# Evaluation videos are whole surgeries (often >1 h at 1 fps): ignore the upload limit from .env.
export MAX_VIDEO_SEC=${EVAL_MAX_VIDEO_SEC:-14400}
mkdir -p "$RESULTS"
for v in "$VIDEOS"/*.mp4; do
  n=$(basename "$v" .mp4)
  python -m rescrub.pipeline.run "$v" --out "$RESULTS/$n"
  python -m rescrub.analysis.breaches --jsonl "$RESULTS/$n" --out "$RESULTS/$n.findings.json" > /dev/null
done
python -m training.evaluate_breaches --mmor "$M" --videos "$VIDEOS" --results "$RESULTS"
echo "Report: $RESULTS/evaluation.json  Labeled videos: $RESULTS/*/*.annotated.mp4"
