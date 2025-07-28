#===============================================
# Dockerfile for ERPNext-Woocommerce Integration
#===============================================
FROM python:3.11-slim

# Install OS deps & clean up
RUN apt-get update \
    && apt-get install -y curl wget \
    && rm -rf /var/lib/apt/lists/*

# All source will live under /code
WORKDIR /code

# Install Python deps first for layer caching
COPY app/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy entire repo into the image
COPY . .

# ALSO symlink /app → /code/app so imports and tools expecting /app/... still work
RUN ln -s /code/app /app

# Expose HTTP port
EXPOSE 8000

# Launch the ASGI app from the app package
CMD ["uvicorn", "app.main_app:app", "--host", "0.0.0.0", "--port", "8000"]
