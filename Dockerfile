FROM python:3.12-slim

# System dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    python3-dev \
    portaudio19-dev \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libxcb1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Application
COPY . .

# Render provides PORT env variable
ENV PORT=10000

CMD ["sh", "-c", "uvicorn control_panel.server:app --host 0.0.0.0 --port ${PORT}"]
