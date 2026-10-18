"""All configuration comes from environment variables (or backend/.env).

Secrets (DATABASE_URL, S3 keys) live only on the Vultr instance. See .env.example.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # Tiger Cloud (TimescaleDB) connection string, e.g.
    # postgres://tsdbadmin:<password>@<host>.tsdb.cloud.timescale.com:<port>/tsdb?sslmode=require
    database_url: str = "postgresql://rescrub:rescrub@localhost:5432/rescrub"

    # Where videos live: "s3" (Vultr Object Storage) or "local" (a folder, for development).
    storage_backend: str = "local"
    local_storage_dir: Path = BACKEND_DIR / "data"
    s3_endpoint_url: str = ""  # e.g. https://ewr1.vultrobjects.com
    s3_region: str = "us-east-1"
    s3_bucket: str = "rescrub"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    presign_ttl_sec: int = 3600
    # Delete the uploaded original once the annotated video is saved.
    delete_original_after_processing: bool = False

    # Website origins allowed to call the API, comma separated.
    cors_origins: str = "http://localhost:3000"
    max_upload_mb: int = 500
    max_video_sec: float = 3600  # 60 minutes; MM-OR training evaluation uses its own limit (run_all.sh)
    uploads_per_hour_per_client: int = 10
    requests_per_minute_per_client: int = 120
    enable_api_docs: bool = False

    # Sign-in. Cookies are Secure (HTTPS only) unless COOKIE_SECURE=false for http:// development.
    cookie_secure: bool = True
    session_hours: int = 168  # 7 days, then sign in again
    logins_per_15min_per_email: int = 10
    logins_per_15min_per_ip: int = 60  # a whole class may share one school IP

    # Models. "auto" picks cuda when available.
    device: str = "auto"
    models_dir: Path = BACKEND_DIR / "models"
    pose_model: str = "yolo26m-pose.pt"
    tracker_config: Path = BACKEND_DIR / "config" / "botsort_reid.yaml"
    embed_model: str = "yolo26n-cls.pt"
    surface_config: Path = BACKEND_DIR / "config" / "surfaces.yaml"
    enable_hands: bool = True
    hand_model: str = (
        "https://download.openmmlab.com/mmpose/v1/projects/rtmw/onnx_sdk/"
        "rtmw-dw-x-l_simcc-cocktail14_270e-384x288_20231122.zip"
    )
    hand_input_size: tuple[int, int] = (288, 384)
    enable_depth: bool = True
    depth_model: str = "depth-anything/Depth-Anything-V2-Small-hf"

    # Sampling. Every frame is tracked; heavier stages run less often.
    frame_stride: int = 1
    surface_interval_sec: float = 2.0
    depth_interval_frames: int = 5
    embed_interval_frames: int = 10

    # Proximity features (pixels are measured on the original frame).
    near_px: float = 40.0
    contact_px: float = 8.0
    contact_depth_gap: float = 0.08
    contact_max_speed_px_s: float = 400.0

    # Identity merging for "Person N" labels.
    reid_similarity: float = 0.80
    reid_max_gap_sec: float = 30.0
    reid_max_speed_frac: float = 0.6  # fraction of frame diagonal per second

    # Hand-drawn sterile/nonsterile zones for a fixed camera (JSON, see README), used for
    # uploads that do not send their own `zones`. Empty = segment surfaces automatically.
    default_zones_file: Path | None = None

    # Rule-based breach analysis after tracking (findings shown on the website).
    run_breach_analysis: bool = True

    # Final review report written by Google Gemini (API server only; rescrub/report.py).
    # Only findings text and reviewer decisions are sent, never video. Empty key = feature off.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"  # or pin a version listed in Google AI Studio
    gemini_timeout_sec: float = 60.0
    reports_per_hour_per_user: int = 20

    worker_poll_sec: float = 2.0
    db_batch_rows: int = 5000

    @property
    def session_cookie_name(self) -> str:
        # __Host- makes browsers refuse the cookie unless it is Secure, Path=/ and host-only.
        return "__Host-rescrub_session" if self.cookie_secure else "rescrub_session"

    @property
    def session_ttl(self):
        from datetime import timedelta

        return timedelta(hours=self.session_hours)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def resolved_device(self) -> str:
        if self.device != "auto":
            return self.device
        try:
            import torch

            return "cuda:0" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"


@lru_cache
def get_settings() -> Settings:
    return Settings()
