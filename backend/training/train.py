"""Train the three reScrub models on the datasets built from MM-OR.

    python -m training.train seg  --data /data/rescrub_seg/dataset.yaml
    python -m training.train pose --data /data/rescrub_pose/dataset.yaml
    python -m training.train reid --data /data/rescrub_reid

Each run starts from the released YOLO26 weights (transfer learning) and, when
it finishes, copies best.pt into MODELS_DIR under the name the backend expects:

    seg  -> trained/rescrub-or-seg.pt   (SURFACE_CONFIG=config/surfaces_mmor.yaml)
    pose -> trained/rescrub-or-pose.pt  (POSE_MODEL=trained/rescrub-or-pose.pt)
    reid -> trained/rescrub-reid-cls.pt (EMBED_MODEL=trained/rescrub-reid-cls.pt)

`trained/` is a mounted folder on the GPU server, so weights survive rebuilds.
"""

import argparse
import shutil
from pathlib import Path

import yaml

from rescrub.settings import get_settings

TASKS = {
    # base weights, output name, sensible defaults for a single 24-48 GB GPU
    "seg": ("yolo26m-seg.pt", "rescrub-or-seg.pt", {"imgsz": 1024, "epochs": 80, "batch": 8}),
    "pose": ("yolo26m-pose.pt", "rescrub-or-pose.pt", {"imgsz": 1024, "epochs": 40, "batch": 8}),
    "reid": ("yolo26n-cls.pt", "rescrub-reid-cls.pt", {"imgsz": 224, "epochs": 30, "batch": 128}),
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task", choices=TASKS)
    ap.add_argument("--data", required=True, help="dataset.yaml (seg/pose) or folder with train/ and val/ (reid)")
    ap.add_argument("--base", help="starting weights (default: released YOLO26 weights)")
    ap.add_argument("--epochs", type=int)
    ap.add_argument("--imgsz", type=int)
    ap.add_argument("--batch", type=int)
    ap.add_argument("--device", default="0")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--project", default="runs/rescrub")
    ap.add_argument("--name")
    ap.add_argument("--no-install", action="store_true", help="do not copy best.pt into MODELS_DIR")
    args = ap.parse_args()
    from ultralytics import YOLO

    base, out_name, defaults = TASKS[args.task]
    params = {**defaults, **{k: v for k in ("epochs", "imgsz", "batch") if (v := getattr(args, k)) is not None}}
    model = YOLO(args.base or base)
    model.train(
        data=args.data,
        device=args.device,
        workers=args.workers,
        project=args.project,
        name=args.name or args.task,
        patience=15,
        cos_lr=True,
        # The camera is fixed and overhead/angled: no vertical flips, mild geometry, strong colour jitter
        # (OR lighting changes a lot between rooms).
        **({"fliplr": 0.5, "flipud": 0.0, "degrees": 5, "scale": 0.4, "hsv_v": 0.5, "mosaic": 0.5}
           if args.task != "reid" else {}),
        **params,
    )
    best = Path(model.trainer.best)
    has_test = args.task != "reid" and _has_images(Path(args.data), "test")
    metrics = model.val(data=args.data, device=args.device, split="test" if has_test else "val")
    print("held-out metrics:", getattr(metrics, "results_dict", metrics))
    if not args.no_install and best.exists():
        dest = get_settings().models_dir / "trained" / out_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(best, dest)
        print(f"Installed {best} -> {dest}")


def _has_images(data_yaml: Path, split: str) -> bool:
    """True if dataset.yaml names `split` and its image folder is not empty (a run may skip the test download)."""
    cfg = yaml.safe_load(data_yaml.read_text()) or {}
    if split not in cfg:
        return False
    folder = Path(cfg.get("path") or data_yaml.parent) / cfg[split]
    return folder.is_dir() and any(folder.iterdir())


if __name__ == "__main__":
    main()
