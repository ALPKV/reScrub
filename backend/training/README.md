# Training reScrub on MM-OR

[MM-OR](https://github.com/egeozsoy/MM-OR) (CVPR 2025, TU Munich) is a large set of recordings
of simulated robot-assisted knee replacement surgeries (PKA/TKA) in a full-scale operating
room. It has several RGB-D cameras and per-pixel labels for staff roles, the patient, drapes,
tables, instruments and equipment, plus scene graphs ("circulator touching instrument_table").
The older [4D-OR](https://github.com/egeozsoy/4D-OR) dataset uses the same mask format and adds
more rooms.

From it we train three models that slot into the backend without code changes:

| Model | Learns | From MM-OR | Backend setting |
| --- | --- | --- | --- |
| `rescrub-or-seg.pt` (YOLO26-seg) | Sterile/nonsterile surfaces **and** scrubbed vs. unscrubbed people | Panoptic masks | `SURFACE_CONFIG=config/surfaces_mmor.yaml` |
| `rescrub-or-pose.pt` (YOLO26-pose) | Detecting gowned, masked, occluded OR staff (and not drapes or the draped patient) | Teacher keypoints checked against ground-truth person masks | `POSE_MODEL=trained/rescrub-or-pose.pt` |
| `rescrub-reid-cls.pt` (YOLO26-cls) | Appearance features that tell people in identical gowns apart, so "Person N" labels stay stable | Role = identity within each take | `EMBED_MODEL=trained/rescrub-reid-cls.pt` |

A fourth export turns the scene-graph relations into **ground-truth contact events**
(`training/export_contacts.py`) for evaluating and extending the breach analysis.

## 1. Get the data

Access requires accepting MM-OR's terms through the form linked in the
[MM-OR README](https://github.com/egeozsoy/MM-OR#mm-or-dataset); they apply to the data and to
models trained on it. The data and weights are never committed (`*.pt`, `data/`, `models/` are
git-ignored). On the GPU server, with the block-storage volume at `/mnt/data`, run:

```bash
python -m training.fetch_mmor --out /mnt/data --profile small --dry-run   # print sizes
python -m training.fetch_mmor --out /mnt/data --profile tiny              # ~28 GB zipped
python -m training.fetch_mmor --out /mnt/data --profile small             # ~41 GB zipped
python -m training.fetch_mmor --out /mnt/data --profile full              # ~432 GB zipped
python -m training.fetch_mmor --out /mnt/data --takes 013_PKA             # any specific takes
```

This uses the same files as MM-OR's `download_mm-or.sh` but only fetches what reScrub needs:
the takes, `take_jsons`, `take_tracks` and **`take_timestamp_to_sterility_breach`** (MM-OR's
labeled sterility breaches, used to score the final analysis). It skips point clouds, audio and
transcripts, checks free disk before each file, and deletes unused modalities (depth, point
clouds, ...) right after unzipping. Re-running resumes where it stopped.

**Splits, kept strictly apart** (same as the MM-OR paper; each split is a different surgery):

| Split | Used for | Never used for |
| --- | --- | --- |
| train | fitting all three models | — |
| val | choosing checkpoints / early stopping, tuning analysis thresholds | training |
| test | final end-to-end evaluation only (`make_eval_videos` + `evaluate_breaches`) | training, ReID identities, tuning |

**One command does everything** (download → datasets → training → evaluation on the test
surgeries):

```bash
DATA=/mnt/data PROFILE=small bash training/run_all.sh
```

The steps below are what it runs; "Shorter runs" below covers the settings that cut its time.

**Sizes (zipped):**

| Profile | Train | Val | Test | Total |
| --- | --- | --- | --- | --- |
| `tiny` | 005_TKA (4.6 GB) | 009_TKA (3.0 GB) | 038_TKA (19.9 GB) | ~28 GB |
| `small` | 005_TKA, 010_PKA (18.5 GB) | 009_TKA (3.0 GB) | 038_TKA (19.9 GB) | ~41 GB |
| `full` | 9 takes (273 GB) | 3 takes (63 GB) | 4 takes (95 GB) | ~432 GB |

Short clips (`--short-clips`, ~64 GB) add extra training data. Unzipping needs about 2.2× the
zip size of the largest take at once (001_PKA is 74 GB). After pruning, kept data is about
half the zip size. A 1 TB Vultr block-storage volume is comfortable for `full`.

**Dataset format** (handled by the code):
- **1 frame per second.** `take_jsons/<take>.json` has every frame with `original_timestamp`
  in seconds. Evaluation videos are written at 1 fps on that clock, and the analysis adapts its
  event grouping to the frame interval.
- **Camera sets differ between takes.** All takes have the angled 1280×720 "simstation" ceiling
  cameras 0/2/3, except some short clips (013_PKA) which only have the Azure Kinect cameras
  1/4/5 (2048×1536). Several main takes (005, 009, parts of 010) have no Azure images.
  `make_eval_videos --source auto` picks whichever exists.
- The zips unpack into `MM-OR_data/MM-OR_processed/`; `fetch_mmor` flattens that.
- Breach labels are keyed by **labeled-frame index** (a subsample of frames, also used by the
  scene graphs) and stored as `[subject, object, predicate]`.

### Shorter runs

Most of the time goes into the download, the pose teacher (`yolo26x-pose` on every frame) and
training at 1024 px. `run_all.sh` takes these settings:

| Setting | Effect | Cost |
| --- | --- | --- |
| `PROFILE=tiny` | One surgery per split (~28 GB instead of ~41 GB) | Less variety; scores are noisier |
| `EVAL_SPLIT=val` | Scores on the validation surgery and skips downloading the test split (`tiny`: 7.6 GB total) | Validation picked the checkpoints, so the score is slightly optimistic |
| `EVAL=0` | Skips step 4 and the test download | No score; run step 4 later |
| `EVERY=6` | Keeps every 6th labeled frame (default 2, ReID 3). Frames 1 s apart are nearly identical | Fewer examples of rare classes |
| `IMGSZ=640` | Trains at 640 px instead of 1024 (about 2.5× faster) | Thin objects (instruments, drape edges) get less precise |
| `SEG_EPOCHS=30 POSE_EPOCHS=15 REID_EPOCHS=10` | Fewer epochs (defaults 80/40/30; early stopping after 15 epochs without improvement) | Slightly lower accuracy |
| `MODELS="seg"` | Trains only the surface and role model, which matters most for findings; the base pose and ReID models stay in use | No OR-specific gains for keypoints and identity |

The fastest useful run, about 8 GB of downloads:
```bash
DATA=/data PROFILE=tiny EVAL_SPLIT=val MODELS=seg EVERY=6 IMGSZ=640 SEG_EPOCHS=30 bash training/run_all.sh
```
A good middle ground keeps all three models and the real test score:
```bash
DATA=/data PROFILE=small EVERY=4 IMGSZ=768 bash training/run_all.sh
```
Datasets are rebuilt and models retrained on each run, but downloads resume: re-running with a
larger profile only fetches the new surgeries.

## 2. Build the datasets

On the GPU server these run inside the worker container
(`docker compose --profile worker run --rm worker python -m …`), where the block-storage volume
is `/data`:

```bash
# Surfaces + roles. Symlinks images; add --copy-max-side 1280 to store smaller copies instead.
python -m training.build_seg_dataset  --mmor /data/MM-OR_data --or4d /data/4D-OR_data --out /data/rescrub_seg

# Person crops per (take, role) for ReID
python -m training.build_reid_dataset --mmor /data/MM-OR_data --out /data/rescrub_reid

# Pose pseudo-labels (needs a GPU; uses yolo26x-pose as the teacher)
python -m training.build_pose_dataset --mmor /data/MM-OR_data --out /data/rescrub_pose --device 0

# Ground-truth contact events (for evaluating the breach analysis)
python -m training.export_contacts    --mmor /data/MM-OR_data --out /data/rescrub_contacts.jsonl
```

Splits follow the MM-OR paper: train, val and test are different surgeries, so test scores
show how the models do on a recording they have never seen. Each builder writes a
`stats.json` with image and instance counts per class; check it for classes with very few
examples.

## 3. Train

```bash
python -m training.train seg  --data /data/rescrub_seg/dataset.yaml
python -m training.train pose --data /data/rescrub_pose/dataset.yaml
python -m training.train reid --data /data/rescrub_reid
```

Each run starts from the released YOLO26 weights, evaluates on the held-out split, and copies
`best.pt` into `MODELS_DIR/trained/` (the mounted `backend/models/trained/` folder) under the
name in the table above. Defaults (`imgsz` 1024, 80/40/30
epochs, early stopping) suit one 24–48 GB GPU; adjust with `--epochs`, `--imgsz`, `--batch`.
Augmentation avoids vertical flips (the camera is fixed) and uses strong brightness jitter,
because lighting varies a lot between rooms.

Then set these in `backend/.env` on the GPU server, for the models you trained, and restart
the worker:

```
SURFACE_CONFIG=config/surfaces_mmor.yaml       # seg
POSE_MODEL=trained/rescrub-or-pose.pt          # pose
EMBED_MODEL=trained/rescrub-reid-cls.pt        # reid
```

## 4. Check the final analysis and its timestamps on unseen surgeries

```bash
python -m training.make_eval_videos  --mmor /data/MM-OR_data --out /data/eval_videos/test --split test
# for each video: track + label, then the timestamped breach analysis
python -m rescrub.pipeline.run /data/eval_videos/test/038_TKA_simstation0.mp4 --out /data/eval_results/test/038_TKA_simstation0
python -m rescrub.analysis.breaches --jsonl /data/eval_results/test/038_TKA_simstation0 \
       --out /data/eval_results/test/038_TKA_simstation0.findings.json
python -m training.evaluate_breaches --mmor /data/MM-OR_data --videos /data/eval_videos/test --results /data/eval_results/test
```

Each test surgery becomes a single-camera MP4 on MM-OR's real clock (1 frame per second),
named `<take>_<camera>.mp4`. The angled simstation camera 0 is used when the take has it, and
Azure camera 4 otherwise. A `.frames.json` next to it maps MM-OR's labeled-frame indexes
(which the breach labels use) to video seconds. `evaluate_breaches` puts the labels on the same
video clock and reports:

- **timestamp problems**: findings outside the video, ending before they start, unsorted, or
  with evidence outside the finding window (should be 0);
- **precision / recall** of findings against labeled breaches, within `--tolerance` seconds;
- **mean onset error**: how far the finding's start is from the labeled breach start.

Results go to `eval_results/<split>/evaluation.json`. Each video's labeled MP4 is in
`eval_results/<split>/<video>/`.

## 5. Fine-tune on your own mock surgeries

MM-OR is one hospital's OR running one kind of surgery. Your mock OR will look different, so
fine-tune on your own footage once you have some:

1. Export ~200–500 frames from your camera and label them in CVAT (SAM-assisted masks) with
   the **same class names** as `mmor_classes.yaml` (`sterile_drape`, `scrubbed_person`, ...).
   Export them as "YOLO 1.1 segmentation".
2. Append your images and labels to the MM-OR dataset folders (or make a `dataset.yaml`
   listing both) and train starting from the MM-OR model:
   `python -m training.train seg --data mixed.yaml --base /models/trained/rescrub-or-seg.pt --epochs 30`.

If an object is draped differently in your room (for example a draped secondary table), change
its `designation` in `config/surfaces_mmor.yaml`. You don't need to retrain.

## How MM-OR labels map to reScrub

`mmor_classes.yaml` is the single source of truth. In short:

| reScrub class | MM-OR labels | Designation |
| --- | --- | --- |
| `sterile_drape` | drape | sterile |
| `instrument_table` | instrument_table (draped back table) | sterile |
| `instrument` | drill, saw, hammer, cementer | sterile |
| `tracker_array` | tracker | sterile |
| `robot_arm` | mako_robot | neutral (never a breach on its own; see below) |
| `patient` | patient (exposed, outside the drapes) | patient |
| `operating_table`, `anesthesia_equipment`, `robot_station`, `monitor`, `c_arm`, `secondary_table` | ot, ae, mps_station, monitor, c_arm, secondary_table | nonsterile |
| `scrubbed_person` | head_surgeon, assistant_surgeon, nurse (scrub nurse) | sterile |
| `unscrubbed_person` | circulator, anest, mps, student, unrelated_person | nonsterile |

Merging roles into scrubbed/unscrubbed (instead of learning "head surgeon") lets the model
generalize to mock surgeries where students play the roles. In the pipeline, each tracked
person's designation is a confidence-weighted vote over the role masks that fall on them. It
is stored in `people.designation` and drawn as "(scrubbed)" or "(unscrubbed)".

## What is sterile in an OR

This is a summary of standard surgical asepsis (AORN Recommended Practices for Sterile
Technique and the teaching materials based on them). It is what the class designations above
and the future contamination rules are based on.

**Sterile**
- **Scrubbed people's gown front**, from **chest to the level of the sterile field** (table
  height). **Sleeves** count from **2 inches above the elbow to the cuff**, all the way
  around. **Sterile gloves** count too.
- **Draped tables:** only the **horizontal tabletop**. Anything hanging over the edge or
  below table level is not sterile.
- **Sterile drapes** over the patient and equipment, but only on top. The **prepped incision
  site** is sterile too.
- **Sterile instruments and supplies** that stay on the sterile field.

**Not sterile**
- **Gown back, neckline, shoulders, armpits and sleeve cuffs** (once the hands are past
  them). These rub against things and can't be watched, so they are never a barrier. This is
  why reScrub assumes people don't turn their back to the camera.
- **Anything below waist or table level.** Hands and forearms should stay between **waist and
  mid-chest**.
- The **1-inch (2.5 cm) edge** of a drape or wrapper.
- **Unscrubbed staff**: circulator, anesthesia, product specialists, observers. They must stay
  at least **12 inches (30 cm)** from the sterile field, face it when passing, and never reach
  over it.
- **Undraped surfaces**: operating table sides, anesthesia machine, carts, monitors, C-arm,
  floor, walls.
- **Anything that went out of view**, got wet through a drape (strike-through), or whose
  sterility is in doubt ("if in doubt, it is contaminated").

**Typical breaks a reviewer looks for**, and how reScrub can see them:

| Break | Detectable from reScrub data |
| --- | --- |
| Scrubbed hand or glove touches a nonsterile surface, then something sterile | `proximity_events` (nonsterile, `candidate_contact`) followed by sterile contact for the same person and hand |
| Unscrubbed person touches the sterile field, instrument table or instruments | `people.designation = 'nonsterile'` + `proximity_events` on a sterile surface; MM-OR `breach_candidate` rows are ground truth |
| Hands drop below table or waist level | wrist/fingertip `y` below the drape/table top edge or the person's hip keypoints (`keypoints`) |
| Arms folded under the armpits / hands above the chest | wrist near shoulder keypoints |
| Unscrubbed person within 12 in of the field | person box or keypoints within ~30 cm (px scale from table size) of a sterile surface |
| Touching the drape edge | contact within a few px of a sterile mask boundary (`pixel_dist` small, `inside_mask` false) |
| Turning the back to the field | not reliably visible from one camera (assumed not to happen) |

Sources (accessed 2026-10): AORN Recommended Practices for Sterile Technique / Sterile Field
([ANSI preview](https://webstore.ansi.org/preview-pages/AORN/preview_II-03_SterileField_2012.pdf));
[Children's Wisconsin attire and aseptic technique policy](https://childrenswi.org/-/media/chwlibrary/files/secure/protected-pages/staff/attire-policy-and-aseptic-techniques.pdf);
[CNOR study guide: principles of surgical asepsis](https://open-exam-prep.com/study-guides/cnor/ch6/principles-surgical-asepsis);
[NCCT TS-C guide: sterile field boundaries](https://open-exam-prep.com/study-guides/ncct-tsc/introduction-asepsis/aseptic-principles);
[Infection Control Today: maintaining asepsis within a sterile field](https://infectioncontroltoday.com/view/maintaining-asepsis-within-sterile-field-surgery).
The sources disagree slightly on the exact gown and sleeve boundaries. Follow your institution's
current AORN guideline where it differs.

### How this matches MM-OR's own breach labels

The labeled breaches in all 38 MM-OR takes (`take_timestamp_to_sterility_breach`) follow these
rules:

| MM-OR breach (count) | Rule |
| --- | --- |
| nurse/assistant surgeon touching or manipulating the operating table (276) or a monitor (2) | scrubbed person → nonsterile |
| MPS/circulator/anaesthetist touching, holding or preparing the drape (114), instrument table (68), saw (38) | unscrubbed person → sterile |
| anaesthetist/MPS touching the patient (13) | unscrubbed person → patient |

Robot contact is never labeled a breach, by either group, so the robot is `neutral`. Skin
preparation by the circulator before draping is flagged by the patient rule but is not a breach
in MM-OR; set `Params(unscrubbed_patient_is_breach=False)` in `rescrub/analysis/breaches.py` to
exclude unscrubbed contact with the patient.

## Dataset notes

- MM-OR is **knee arthroplasty with a MAKO robot**. Other procedures, room layouts and drape
  colors may need the fine-tuning step above.
- MM-OR masks are **semantic per role** (one mask per role per frame). If two people share a
  role and stand apart, they become separate instances; if they overlap, one merged instance.
- The pose model is trained on **pseudo-labels**. It adapts detection to the OR (fewer false
  people, better with occlusion), but its keypoints can't beat the teacher's.
- ReID identities are **per take**. Validation measures recognizing the same people later in
  the same surgery, which is exactly what tracking needs.
