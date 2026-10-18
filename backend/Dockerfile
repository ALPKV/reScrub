# One image for both the API and the GPU worker (Vultr Instance 1).
FROM pytorch/pytorch:2.7.1-cuda12.6-cudnn9-runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    MODELS_DIR=/models \
    YOLO_CONFIG_DIR=/models/ultralytics \
    XDG_CACHE_HOME=/models/cache \
    HF_HOME=/models/hf

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates ffmpeg git libgl1 libglib2.0-0 wget unzip \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# Bake every model into the image so containers start without downloads.
RUN python -m rescrub.download_models

RUN useradd --create-home --uid 1000 app && mkdir -p /app/data /models/trained && chown -R app /app /models
USER app

EXPOSE 8000
CMD ["uvicorn", "rescrub.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
