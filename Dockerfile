FROM python:3.12-slim

WORKDIR /app

# Copy dependency files first (layer cache)
COPY requirements-cloud.txt ./

# Install Python dependencies (no MLX/torch/bitsandbytes)
RUN pip install --no-cache-dir -r requirements-cloud.txt

# Copy application code
COPY . .

# Remove any local model artifacts that shouldn't ship
# (mlx_models/ is large — exclude via .dockerignore instead)

# Default: pre-computed results only (no model)
ENV PORT=8080

EXPOSE 8080

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
