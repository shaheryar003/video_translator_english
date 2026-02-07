FROM python:3.10-slim

# Install system dependencies (ffmpeg is crucial)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first to leverage cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p uploads Output

# Start command
# We use shell form to allow variable expansion of $PORT (Render sets this)
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
